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

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "arcanjos_zap")
GRAPH_API_URL = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

ZAPI_BASE_URL = os.environ.get("ZAPI_BASE_URL", "")
ZAPI_CLIENT_TOKEN = os.environ.get("ZAPI_CLIENT_TOKEN", "")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

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


def send_zapi_message(phone_number: str, text: str):
    url = f"{ZAPI_BASE_URL}/send-text"
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN}
    resp = requests.post(url, headers=headers, json={"phone": phone_number, "message": text}, timeout=30)
    if not resp.ok:
        print(f"[ZAPI ERRO] {resp.status_code}: {resp.text}")
    else:
        print(f"[ZAPI OK] Mensagem enviada para {phone_number}")
    return resp


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
    resp = requests.post(GRAPH_API_URL, headers=headers, json=payload, timeout=30)
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

    import json as _json
    with open("webhook_log.txt", "a", encoding="utf-8") as f:
        f.write(_json.dumps(body, ensure_ascii=False) + "\n")

    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        if "messages" not in value:
            return {"status": "ok"}

        msg = value["messages"][0]

        if msg.get("type") != "text":
            return {"status": "ok"}

        message_id = msg.get("id", "")
        if message_id in processed_message_ids:
            return {"status": "ok"}
        processed_message_ids.add(message_id)

        # Usar o wa_id normalizado pela Meta como destino do reply
        phone_number = value.get("contacts", [{}])[0].get("wa_id") or msg["from"]
        text = msg["text"]["body"]

        print(f"[MSG] De: {phone_number} | Texto: {text}")
        background_tasks.add_task(process_and_reply, phone_number, text)

    except Exception as e:
        print(f"[ERRO] Parsing do webhook: {e}")

    return {"status": "ok"}


@app.get("/logs")
def ver_logs():
    try:
        with open("webhook_log.txt", "r", encoding="utf-8") as f:
            return {"logs": f.read()[-3000:]}
    except FileNotFoundError:
        return {"logs": "nenhum log ainda"}


@app.post("/twilio")
async def handle_twilio_message(request: Request, background_tasks: BackgroundTasks):
    """Recebe mensagens do WhatsApp via Twilio Sandbox."""
    form = await request.form()
    body_text = form.get("Body", "").strip()
    from_number = form.get("From", "").replace("whatsapp:", "")
    message_sid = form.get("MessageSid", "")

    print(f"[TWILIO MSG] De: {from_number} | Texto: {body_text}")

    if not body_text or not from_number:
        return Response(content="", media_type="text/plain")

    if message_sid in processed_message_ids:
        return Response(content="", media_type="text/plain")
    processed_message_ids.add(message_sid)

    background_tasks.add_task(process_and_reply_twilio, from_number, body_text)
    return Response(content="", media_type="text/plain")


def process_and_reply_twilio(phone_number: str, text: str):
    try:
        chat_history = memory_store.setdefault(phone_number, [])
        response = whatsapp_chain.invoke({"question": text, "chat_history": chat_history})
        bot_text = response.content

        chat_history.append(HumanMessage(content=text))
        chat_history.append(AIMessage(content=bot_text))

        send_twilio_message(phone_number, bot_text)
    except Exception as e:
        print(f"[TWILIO ERRO] Processamento de {phone_number}: {e}")


@app.post("/zapi")
async def handle_zapi_message(request: Request, background_tasks: BackgroundTasks):
    """Recebe mensagens do WhatsApp via Z-API."""
    body = await request.json()

    import json as _json
    print(f"[ZAPI] Webhook recebido: {_json.dumps(body, ensure_ascii=False)[:300]}")

    try:
        # Ignorar mensagens enviadas pelo próprio bot e não-texto
        if body.get("fromMe"):
            return {"status": "ok"}

        msg_type = body.get("type", "")
        if msg_type not in ("ReceivedCallback",):
            return {"status": "ok"}

        text = body.get("text", {}).get("message", "").strip()
        if not text:
            return {"status": "ok"}

        phone_number = body.get("phone", "")
        message_id = body.get("messageId", "")

        if message_id in processed_message_ids:
            return {"status": "ok"}
        processed_message_ids.add(message_id)

        print(f"[ZAPI MSG] De: {phone_number} | Texto: {text}")
        background_tasks.add_task(process_and_reply_zapi, phone_number, text)

    except Exception as e:
        print(f"[ZAPI ERRO] Parsing: {e}")

    return {"status": "ok"}


def process_and_reply_zapi(phone_number: str, text: str):
    try:
        chat_history = memory_store.setdefault(phone_number, [])
        response = whatsapp_chain.invoke({"question": text, "chat_history": chat_history})
        bot_text = response.content

        chat_history.append(HumanMessage(content=text))
        chat_history.append(AIMessage(content=bot_text))

        send_zapi_message(phone_number, bot_text)
    except Exception as e:
        print(f"[ZAPI ERRO] Processamento de {phone_number}: {e}")


@app.get("/debug")
def debug(phone: str = "5500000000000", msg: str = "Ola"):
    """Testa toda a pipeline de forma síncrona e retorna diagnóstico completo."""
    result: dict = {"faiss_ok": retriever is not None}

    try:
        r = requests.get(
            f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            timeout=8,
        )
        result["token_ok"] = r.ok
        result["token_status"] = r.status_code
    except Exception as e:
        result["token_ok"] = False
        result["token_error"] = str(e)

    try:
        resp = whatsapp_chain.invoke({"question": msg, "chat_history": []})
        result["chain_ok"] = True
        result["bot_response"] = resp.content[:400]
    except Exception as e:
        result["chain_ok"] = False
        result["chain_error"] = str(e)

    try:
        hdrs = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        body_text = result.get("bot_response", "Teste de conexao Arcanjos")
        pl = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": body_text}}
        sr = requests.post(GRAPH_API_URL, headers=hdrs, json=pl, timeout=30)
        result["send_ok"] = sr.ok
        result["send_status"] = sr.status_code
        result["send_body"] = sr.text[:400]
    except Exception as e:
        result["send_ok"] = False
        result["send_error"] = str(e)

    return result

