import os
import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough

from prompts import SYSTEM_PROMPT

load_dotenv()

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

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

DISCONTINUED = [
    ("telemedicina familiar", "120"),
]

def format_docs(docs):
    filtered = []
    for doc in docs:
        content_lower = doc.page_content.lower()
        if any(kw1 in content_lower and kw2 in content_lower for kw1, kw2 in DISCONTINUED):
            continue
        filtered.append(doc.page_content)
    return "\n\n".join(filtered)


chain = (
    RunnablePassthrough.assign(
        context=lambda x: format_docs(retriever.invoke(x["question"])) if retriever else ""
    )
    | prompt
    | llm
)

memory_store: dict[str, list] = {}
processed_message_ids: set[str] = set()

app = FastAPI()


def send_twilio_message(to_number: str, text: str):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    resp = requests.post(url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_WHATSAPP_FROM, "To": f"whatsapp:{to_number}", "Body": text},
        timeout=30)
    if not resp.ok:
        print(f"[TWILIO ERRO] {resp.status_code}: {resp.text}")
    else:
        print(f"[TWILIO OK] Mensagem enviada para {to_number}")
    return resp


def process_and_reply(phone_number: str, text: str):
    try:
        chat_history = memory_store.setdefault(phone_number, [])
        response = chain.invoke({"question": text, "chat_history": chat_history})
        bot_text = response.content

        chat_history.append(HumanMessage(content=text))
        chat_history.append(AIMessage(content=bot_text))

        send_twilio_message(phone_number, bot_text)
    except Exception as e:
        print(f"[TWILIO ERRO] Processamento de {phone_number}: {e}")


@app.post("/twilio")
async def handle_twilio_message(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    body_text = form.get("Body", "").strip()
    from_number = form.get("From", "").replace("whatsapp:", "")
    message_sid = form.get("MessageSid", "")

    import json as _json
    with open("webhook_log.txt", "a", encoding="utf-8") as f:
        f.write(_json.dumps({"canal": "twilio", "de": from_number, "texto": body_text, "sid": message_sid}, ensure_ascii=False) + "\n")

    print(f"[TWILIO MSG] De: {from_number} | Texto: {body_text}")

    if not body_text or not from_number:
        return Response(content="", media_type="text/plain")

    if message_sid in processed_message_ids:
        return Response(content="", media_type="text/plain")
    processed_message_ids.add(message_sid)

    background_tasks.add_task(process_and_reply, from_number, body_text)
    return Response(content="", media_type="text/plain")


@app.get("/logs")
def ver_logs():
    try:
        with open("webhook_log.txt", "r", encoding="utf-8") as f:
            return {"logs": f.read()[-3000:]}
    except FileNotFoundError:
        return {"logs": "nenhum log ainda"}


@app.get("/debug")
def debug(msg: str = "Ola"):
    result: dict = {"faiss_ok": retriever is not None}
    try:
        resp = chain.invoke({"question": msg, "chat_history": []})
        result["chain_ok"] = True
        result["bot_response"] = resp.content[:400]
    except Exception as e:
        result["chain_ok"] = False
        result["chain_error"] = str(e)
    return result
