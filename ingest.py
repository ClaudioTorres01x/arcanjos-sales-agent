import os
from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader, Docx2txtLoader, UnstructuredPowerPointLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
# pyrefly: ignore [missing-import]
from langchain_openai import OpenAIEmbeddings
# pyrefly: ignore [missing-import]
from langchain_community.vectorstores import FAISS

load_dotenv()

# Caminho dos documentos
DATA_DIR = r"G:\Meu Drive\0003. CLAUDIO TORRES\NEGOCIOS\ARCANJOS"
POSTGRES_URL = os.environ.get("POSTGRES_URL")

# Coleção para os embeddings
COLLECTION_NAME = "arcanjos_docs"

def main():
    print(f"Carregando documentos do diretório: {DATA_DIR}")
    
    # Configurar loaders para diferentes tipos de arquivos
    pdf_loader = DirectoryLoader(DATA_DIR, glob="**/*.pdf", loader_cls=PyPDFLoader)
    docx_loader = DirectoryLoader(DATA_DIR, glob="**/*.docx", loader_cls=Docx2txtLoader)
    pptx_loader = DirectoryLoader(DATA_DIR, glob="**/*.pptx", loader_cls=UnstructuredPowerPointLoader)
    txt_loader = DirectoryLoader(DATA_DIR, glob="**/*.txt", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"})

    docs = []
    docs.extend(pdf_loader.load())
    docs.extend(docx_loader.load())
    try:
        docs.extend(pptx_loader.load())
    except Exception as e:
        print(f"Aviso: Não foi possível carregar apresentações PPTX: {e}")
    try:
        docs.extend(txt_loader.load())
    except Exception as e:
        print(f"Aviso: Não foi possível carregar TXT: {e}")
    
    if not docs:
        print("Nenhum documento encontrado ou carregado.")
        return

    print(f"Foram carregados {len(docs)} documentos. Iniciando o text splitting...")

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len
    )
    
    chunks = text_splitter.split_documents(docs)
    print(f"Os documentos foram divididos em {len(chunks)} chunks.")

    print("Inicializando o modelo de embeddings da OpenAI...")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    print("Criando banco FAISS local...")
    vector_store = FAISS.from_documents(chunks, embeddings)
    vector_store.save_local("faiss_index")
    
    print("Processo de ingestão concluído com sucesso! Os embeddings estão salvos no banco de dados.")

if __name__ == "__main__":
    main()
