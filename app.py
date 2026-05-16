# pyrefly: ignore [missing-import]
import chainlit as cl
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
# pyrefly: ignore [missing-import]
from langchain_community.vectorstores import FAISS
# pyrefly: ignore [missing-import]
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.runnables.config import RunnableConfig
from prompts import SYSTEM_PROMPT


load_dotenv()


DISCONTINUED = [
    ("telemedicina familiar", "120"),
    ("familiar", "40 anos"),
]

def format_docs(docs):
    filtered = []
    for doc in docs:
        content_lower = doc.page_content.lower()
        if any(kw1 in content_lower and kw2 in content_lower for kw1, kw2 in DISCONTINUED):
            continue
        filtered.append(doc.page_content)
    return "\n\n".join(filtered)


@cl.on_chat_start
async def on_chat_start():
    llm = ChatOpenAI(model="gpt-4o", temperature=0.3, streaming=True)
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    try:
        vector_store = FAISS.load_local("faiss_index", embeddings, allow_dangerous_deserialization=True)
    except Exception as e:
        await cl.Message(content="Erro ao carregar a base de conhecimento. Por favor, tente novamente mais tarde.").send()
        raise RuntimeError(f"Falha ao carregar faiss_index: {e}") from e

    retriever = vector_store.as_retriever(search_kwargs={"k": 4})

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}")
    ])

    chain = (
        RunnablePassthrough.assign(
            context=lambda x: format_docs(retriever.invoke(x["question"]))
        )
        | prompt
        | llm
    )

    cl.user_session.set("chain", chain)
    cl.user_session.set("chat_history", [])

    await cl.Message(content="Olá! Sou o Executivo de vendas dos Arcanjos. Como posso te ajudar hoje?").send()


@cl.on_message
async def on_message(message: cl.Message):
    chain = cl.user_session.get("chain")
    chat_history = cl.user_session.get("chat_history")

    msg = cl.Message(content="")
    await msg.send()

    inputs = {
        "question": message.content,
        "chat_history": chat_history,
    }

    async for chunk in chain.astream(inputs, config=RunnableConfig(callbacks=[cl.AsyncLangchainCallbackHandler()])):
        if hasattr(chunk, "content"):
            await msg.stream_token(chunk.content)

    chat_history.append(HumanMessage(content=message.content))
    chat_history.append(AIMessage(content=msg.content))
    cl.user_session.set("chat_history", chat_history)

    await msg.update()
