import json
import logging
from typing import Any, Dict, Optional
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from app.config import settings

logger = logging.getLogger(__name__)


async def extract_receipt_info(image_b64: str, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Analisa a imagem de um comprovante de pagamento utilizando Gemini Vision.
    Extrai o titular da conta bancária pagadora, valor, data/hora e identificador.
    """
    if not image_b64:
        return None

    # Limpa prefixos caso venha com data:image/...
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    key = api_key or settings.GEMINI_API_KEY
    if not key or key == "mock-gemini-key":
        # Mock para testes offline
        return {
            "is_receipt": True,
            "pagador": "Maria Souza",
            "valor": 66.00,
            "destinatario": "Sabor & Forno Pizzaria e Lanches",
            "data_hora": "10/11/2026 - 19:08:30",
            "id_transacao": "E12345678MOCK"
        }

    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            api_version="v1beta",
            temperature=0.1,
            google_api_key=key
        )

        prompt = (
            "Você é um auditor financeiro especializado em delivery. "
            "Analise a imagem deste comprovante de pagamento bancário (PIX, TED ou transferência) "
            "e extraia as informações em formato JSON estrito:\n"
            "{\n"
            '  "is_receipt": true,\n'
            '  "pagador": "Nome completo de quem enviou/pagou",\n'
            '  "valor": 0.00,\n'
            '  "destinatario": "Nome ou CNPJ de quem recebeu",\n'
            '  "data_hora": "Data e hora",\n'
            '  "id_transacao": "ID ou código da transação"\n'
            "}\n"
            "Se NÃO for um comprovante de pagamento legítimo, responda apenas: {\"is_receipt\": false}.\n"
            "Responda estritamente o JSON puro sem markdown ou blocos de código."
        )

        msg = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                }
            ]
        )

        resp = await llm.ainvoke([msg])
        raw_text = resp.content if hasattr(resp, "content") else str(resp)
        raw_text = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        data = json.loads(raw_text)
        if isinstance(data, dict) and data.get("is_receipt"):
            return data
    except Exception as e:
        logger.error(f"Erro ao processar comprovante com Gemini Vision: {e}")

    return None

