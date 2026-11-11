import argparse
import base64
import os
import sys
import time
from pathlib import Path
import httpx
import qrcode
from dotenv import load_dotenv

# Carrega variáveis do .env caso existam
load_dotenv()

DEFAULT_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080").rstrip("/")
DEFAULT_API_KEY = os.getenv("EVOLUTION_API_KEY", "secret-token")
DEFAULT_WEBHOOK_URL = "http://api:8000/webhook/evolution"
DEFAULT_INSTANCE_NAME = "distribuidora_central"


def wait_for_evolution_api(base_url: str, api_key: str, max_retries: int = 30, delay: int = 2) -> bool:
    """Aguarda a Evolution API inicializar completamente."""
    headers = {"apikey": api_key}
    print(f"⏳ Conectando à Evolution API em {base_url}...")

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{base_url}/instance/fetchInstances", headers=headers)
                if resp.status_code in (200, 401, 403):
                    print("✅ Evolution API pronta e respondendo!")
                    return True
        except Exception:
            pass

        print(f"   Tentativa {attempt}/{max_retries} - aguardando inicialização...")
        time.sleep(delay)

    print("❌ Tempo limite esgotado esperando a Evolution API.")
    return False


def instance_exists(client: httpx.Client, base_url: str, api_key: str, instance_name: str) -> bool:
    """Verifica se a instância já existe no ecossistema."""
    headers = {"apikey": api_key}
    try:
        resp = client.get(f"{base_url}/instance/fetchInstances", headers=headers)
        if resp.status_code == 200:
            instances = resp.json()
            if isinstance(instances, list):
                for inst in instances:
                    # Formato Evolution v2 pode conter dict com 'name' ou 'instanceName'
                    name = inst.get("name") or inst.get("instanceName") or (inst.get("instance", {}).get("instanceName"))
                    if name == instance_name:
                        return True
    except Exception as e:
        print(f"⚠️ Aviso ao verificar instâncias: {e}")
    return False


def create_instance(client: httpx.Client, base_url: str, api_key: str, instance_name: str) -> bool:
    """Cria a instância na Evolution API v2."""
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    payload = {
        "instanceName": instance_name,
        "token": f"tok_{instance_name}",
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
    }
    print(f"🚀 Criando instância '{instance_name}'...")
    resp = client.post(f"{base_url}/instance/create", headers=headers, json=payload)
    if resp.status_code in (200, 201):
        print("✅ Instância criada com sucesso!")
        return True
    elif resp.status_code == 403 or "already in use" in resp.text:
        print(f"ℹ️ Instância '{instance_name}' já existia.")
        return True
    else:
        print(f"⚠️ Resposta da criação ({resp.status_code}): {resp.text}")
        return False


def configure_webhook(client: httpx.Client, base_url: str, api_key: str, instance_name: str, webhook_url: str) -> bool:
    """Configura o webhook da instância apontando para o backend FastAPI."""
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    webhook_cfg = {
        "enabled": True,
        "url": webhook_url,
        "byEvents": False,
        "base64": False,
        "events": [
            "MESSAGES_UPSERT",
            "CONNECTION_UPDATE"
        ],
    }
    if webhook_secret:
        webhook_cfg["headers"] = {"X-Webhook-Secret": webhook_secret}

    payload = {"webhook": webhook_cfg}
    print(f"🔗 Configurando Webhook da instância para '{webhook_url}'...")
    resp = client.post(f"{base_url}/webhook/set/{instance_name}", headers=headers, json=payload)
    if resp.status_code in (200, 201):
        print("✅ Webhook configurado com eventos MESSAGES_UPSERT e CONNECTION_UPDATE habilitados!")
        return True
    else:
        print(f"⚠️ Falha ao configurar Webhook ({resp.status_code}): {resp.text}")
        return False


def fetch_and_display_qrcode(client: httpx.Client, base_url: str, api_key: str, instance_name: str, output_path: str) -> None:
    """Obtém o QR Code, salva em arquivo de imagem e renderiza em ASCII no terminal."""
    headers = {"apikey": api_key}
    print(f"📲 Solicitando QR Code para '{instance_name}'...")

    resp = client.get(f"{base_url}/instance/connect/{instance_name}", headers=headers)
    if resp.status_code != 200:
        print(f"❌ Erro ao solicitar conexão ({resp.status_code}): {resp.text}")
        return

    data = resp.json()

    # Verifica se já está conectado
    instance_state = data.get("instance", {}).get("state") or data.get("state")
    if instance_state == "open" or data.get("connected") is True:
        print("🎉 Esta instância já está CONECTADA ao WhatsApp!")
        return

    raw_code = data.get("code")
    base64_img = data.get("base64")

    # 1. Salva imagem física em disco
    if base64_img:
        try:
            if "," in base64_img:
                base64_img = base64_img.split(",", 1)[1]
            img_bytes = base64.b64decode(base64_img)
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            print(f"🖼️ QR Code salvo com sucesso em: {Path(output_path).resolve()}")
        except Exception as e:
            print(f"⚠️ Erro ao salvar arquivo {output_path}: {e}")

    # 2. Renderiza no terminal via blocos ASCII
    qr_data_to_render = raw_code or data.get("pairingCode")
    if qr_data_to_render:
        print("\n" + "=" * 50)
        print("📲 ESCANEIE O QR CODE ABAIXO COM SEU WHATSAPP:")
        print("=" * 50 + "\n")
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=2,
        )
        qr.add_data(qr_data_to_render)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        print("\n" + "=" * 50)
        print("1. Abra o WhatsApp no celular")
        print("2. Vá em Configurações / Aparelhos Conectados")
        print("3. Toque em 'Conectar um Aparelho' e aponte para a tela!")
        print("=" * 50 + "\n")
    elif base64_img:
        print(f"\n📲 QR Code disponível em imagem: abra o arquivo '{output_path}' para escanear.")
    else:
        print(f"ℹ️ Dados retornados: {data}")


def main():
    parser = argparse.ArgumentParser(description="Automação de Setup da Evolution API v2 e Pareamento WhatsApp")
    parser.add_argument("--instance-name", default=DEFAULT_INSTANCE_NAME, help="Nome da instância")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="URL base da Evolution API")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key da Evolution API")
    parser.add_argument("--webhook-url", default=DEFAULT_WEBHOOK_URL, help="URL de Webhook da API FastAPI")
    parser.add_argument("--output-image", default="qrcode.png", help="Caminho do arquivo de imagem do QR Code")

    args = parser.parse_args()

    # 1. Espera a Evolution API responder
    if not wait_for_evolution_api(args.api_url, args.api_key):
        sys.exit(1)

    with httpx.Client(timeout=30.0) as client:
        # 2. Cria a instância se necessário
        if not instance_exists(client, args.api_url, args.api_key, args.instance_name):
            create_instance(client, args.api_url, args.api_key, args.instance_name)
        else:
            print(f"ℹ️ Instância '{args.instance_name}' já existe.")

        # 3. Configura Webhook
        configure_webhook(client, args.api_url, args.api_key, args.instance_name, args.webhook_url)

        # 4. Obtém e renderiza QR Code
        fetch_and_display_qrcode(client, args.api_url, args.api_key, args.instance_name, args.output_image)


if __name__ == "__main__":
    main()
