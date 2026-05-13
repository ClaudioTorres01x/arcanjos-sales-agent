import os
import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks
from chainlit.utils import mount_chainlit
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough

from prompts import SYSTEM_PROMPT

load_dotenv()

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "arcanjos_zap")
GRAPH_API_URL = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

# Agente RAG compartilhado para o canal WhatsApp
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
try:
    vector_store = FAISS.load_local("faiss_index", embeddings, allow_dangerous_deserialization=True)
    retriever = vector_store.as_retriever(search_kwargs={"k": 4})
except Exception as e:
    print(f"[AVISO] faiss_index não carregado: {e}")
    retriever = None

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{question}")
])

llm = ChatOpenAI(model="gpt-4o", temperature=0.3)


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


whatsapp_chain = (
    RunnablePassthrough.assign(
        context=lambda x: format_docs(retriever.invoke(x["question"])) if retriever else ""
    )
    | prompt
    | llm
)

# Histórico por número de telefone
memory_store: dict[str, list] = {}
# IDs de mensagens já processadas (deduplicação de webhooks duplicados da Meta)
processed_message_ids: set[str] = set()

app = FastAPI()


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


def send_whatsapp_message(phone_number: str, text: str):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {"body": text},
    }
    resp = requests.post(GRAPH_API_URL, headers=headers, json=payload, timeout=10)
    if not resp.ok:
        print(f"[ERRO] Falha ao enviar mensagem para {phone_number}: {resp.text}")


def process_and_reply(phone_number: str, text: str):
    try:
        chat_history = memory_store.setdefault(phone_number, [])
        response = whatsapp_chain.invoke({
            "question": text,
            "chat_history": chat_history,
        })
        bot_text = response.content

        chat_history.append(HumanMessage(content=text))
        chat_history.append(AIMessage(content=bot_text))

        send_whatsapp_message(phone_number, bot_text)
    except Exception as e:
        print(f"[ERRO] Processamento da mensagem de {phone_number}: {e}")


@app.post("/webhook")
async def handle_whatsapp_message(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()

    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        if "messages" not in value:
            return {"status": "ok"}

        msg = value["messages"][0]

        # Ignorar mensagens que não sejam texto
        if msg.get("type") != "text":
            return {"status": "ok"}

        message_id = msg.get("id", "")
        if message_id in processed_message_ids:
            return {"status": "ok"}
        processed_message_ids.add(message_id)

        phone_number = msg["from"]
        text = msg["text"]["body"]

        background_tasks.add_task(process_and_reply, phone_number, text)

    except Exception as e:
        print(f"[ERRO] Parsing do webhook: {e}")

    return {"status": "ok"}


# Interface Web Chainlit montada na raiz
mount_chainlit(app=app, target="app.py", path="/")
