"""
Script de desenvolvimento local.
Inicia o servidor uvicorn + tunel ngrok e imprime a URL do webhook para configurar no Meta Console.

Uso:
    python run_dev.py --ngrok-token SEU_AUTHTOKEN
    python run_dev.py --port 8000 --ngrok-token SEU_AUTHTOKEN

Ou defina a variavel de ambiente antes de rodar:
    $env:NGROK_AUTHTOKEN = "SEU_AUTHTOKEN"
    python run_dev.py

Obtenha seu authtoken gratuito em: https://dashboard.ngrok.com/get-started/your-authtoken
"""
import argparse
import os
import subprocess
import sys

try:
    from pyngrok import ngrok, conf
    from pyngrok.exception import PyngrokNgrokError
except ImportError:
    print("[ERRO] pyngrok nao instalado. Execute: pip install pyngrok")
    sys.exit(1)

NGROK_HELP = """
[ERRO] ngrok requer autenticacao.

  1. Crie uma conta gratuita em : https://dashboard.ngrok.com/signup
  2. Copie seu authtoken em     : https://dashboard.ngrok.com/get-started/your-authtoken
  3. Execute novamente com:

       python run_dev.py --ngrok-token SEU_AUTHTOKEN

     Ou salve no ambiente (permanente):

       $env:NGROK_AUTHTOKEN = "SEU_AUTHTOKEN"
       python run_dev.py
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ngrok-token", type=str, default=None)
    args = parser.parse_args()

    # Token: argumento > variavel de ambiente
    token = args.ngrok_token or os.environ.get("NGROK_AUTHTOKEN")
    if token:
        conf.get_default().auth_token = token
    else:
        print(NGROK_HELP)
        sys.exit(1)

    try:
        tunnel = ngrok.connect(args.port, "http")
    except PyngrokNgrokError as e:
        if "ERR_NGROK_4018" in str(e) or "authentication" in str(e).lower():
            print(NGROK_HELP)
        else:
            print(f"[ERRO] Falha ao iniciar ngrok: {e}")
        sys.exit(1)

    public_url = tunnel.public_url.replace("http://", "https://")

    print("\n" + "=" * 60)
    print("  ARCANJOS — Servidor de desenvolvimento")
    print("=" * 60)
    print(f"  URL publica  : {public_url}")
    print(f"  Webhook URL  : {public_url}/webhook")
    print(f"  Verify Token : arcanjos_zap")
    print("=" * 60)
    print("  Configure o webhook acima no Meta Developer Console")
    print("  Ctrl+C para encerrar")
    print("=" * 60 + "\n")

    proc = subprocess.Popen([
        sys.executable, "-m", "uvicorn",
        "main:app",
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--reload",
    ])

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        proc.terminate()
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()


if __name__ == "__main__":
    main()
