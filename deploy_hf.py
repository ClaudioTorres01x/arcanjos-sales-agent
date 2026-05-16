import os
import sys
from huggingface_hub import HfApi
from dotenv import load_dotenv

if len(sys.argv) < 2:
    print("❌ Erro: Faltou o Token.")
    print("Uso: python deploy_hf.py <SEU_TOKEN_HUGGINGFACE>")
    sys.exit(1)

token = sys.argv[1]
api = HfApi(token=token)

repo_id = "Claudiotorres01x/arcanjos-sales-agent"
print(f"Criando repositório {repo_id} no Hugging Face (Docker Space)...")

try:
    api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker")
except Exception as e:
    print(f"Repositório já existe ou aviso: {e}")

print("Fazendo upload dos arquivos para o Hugging Face... Isso pode demorar 1-2 minutos.")

api.upload_folder(
    folder_path=".",
    repo_id=repo_id,
    repo_type="space",
    ignore_patterns=[
        "venv", "venv/*", 
        ".env", 
        "__pycache__", "__pycache__/*", 
        "*.pyc", 
        ".vscode", ".vscode/*",
        ".git", ".git/*"
    ]
)

load_dotenv()
openai_key = os.environ.get("OPENAI_API_KEY")

if openai_key:
    print("Adicionando OPENAI_API_KEY nos Secrets do Space de forma segura...")
    try:
        api.add_space_secret(repo_id=repo_id, key="OPENAI_API_KEY", value=openai_key)
    except Exception as e:
        print(f"Aviso: {e}")

for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"):
    value = os.environ.get(key)
    if value:
        print(f"Adicionando {key} nos Secrets...")
        try:
            api.add_space_secret(repo_id=repo_id, key=key, value=value)
        except Exception as e:
            pass

print(f"Deploy concluido e arquivos enviados!")
print(f"Acesse sua aplicacao em: https://huggingface.co/spaces/{repo_id}")
