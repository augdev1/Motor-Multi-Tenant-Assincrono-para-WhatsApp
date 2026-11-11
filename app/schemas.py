from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class KeyData(BaseModel):
    remoteJid: Optional[str] = None
    fromMe: Optional[bool] = False
    id: Optional[str] = None


class MessageContent(BaseModel):
    conversation: Optional[str] = None
    extendedTextMessage: Optional[Dict[str, Any]] = None
    buttonsResponseMessage: Optional[Dict[str, Any]] = None
    listResponseMessage: Optional[Dict[str, Any]] = None


class EvolutionData(BaseModel):
    key: Optional[KeyData] = None
    pushName: Optional[str] = None
    message: Optional[Dict[str, Any]] = None
    messageType: Optional[str] = None
    instance: Optional[str] = None


class EvolutionMessagePayload(BaseModel):
    """Schema para validação do payload de webhook da Evolution API v2."""
    event: Optional[str] = None
    instance: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    def get_instance_name(self) -> str:
        """Recupera o nome da instância da Evolution API."""
        if self.instance:
            return self.instance
        if self.data and isinstance(self.data, dict):
            return str(self.data.get("instance", ""))
        return ""

    def is_connection_update(self) -> bool:
        """Verifica se o webhook é um evento de atualização do status de conexão do WhatsApp."""
        evt = (self.event or "").upper().replace(".", "_")
        if evt == "CONNECTION_UPDATE":
            return True
        if self.data and isinstance(self.data, dict):
            # Formatos complementares da Evolution API v2 para status de conexão
            if ("state" in self.data or "connection" in self.data) and not self.data.get("key"):
                return True
        return False

    def get_connection_state(self) -> str:
        """Extrai o estado da conexão (open, close, connecting, refused, etc.)."""
        if not self.data or not isinstance(self.data, dict):
            return "unknown"
        state = self.data.get("state") or self.data.get("status") or self.data.get("connection") or ""
        return str(state).lower().strip()

    def get_connection_status_reason(self) -> Optional[Any]:
        """Extrai o código ou motivo de erro de desconexão (ex: 401, 408, 428, 500)."""
        if not self.data or not isinstance(self.data, dict):
            return None
        return self.data.get("statusReason") or self.data.get("reason")

    def get_message_id(self) -> str:
        """Recupera o ID único da mensagem para idempotência."""
        if not self.data or not isinstance(self.data, dict):
            return ""
        key = self.data.get("key") or {}
        return str(key.get("id", ""))

    def is_from_me(self) -> bool:
        """Verifica se a mensagem foi originada pelo próprio bot."""
        if not self.data or not isinstance(self.data, dict):
            return False
        key = self.data.get("key") or {}
        return bool(key.get("fromMe", False))

    def is_group(self) -> bool:
        """Verifica se a mensagem provém de um grupo do WhatsApp (@g.us)."""
        remote_jid = self.get_remote_jid()
        return "@g.us" in remote_jid

    def get_remote_jid(self) -> str:
        """Recupera o JID remoto do remetente."""
        if not self.data or not isinstance(self.data, dict):
            return ""
        key = self.data.get("key") or {}
        return str(key.get("remoteJid", ""))

    def get_phone_number(self) -> str:
        """Extrai o número de telefone limpo (somente dígitos)."""
        jid = self.get_remote_jid()
        raw = jid.split("@")[0] if "@" in jid else jid
        # Remover sufixos ou caracteres não numéricos
        return "".join(ch for ch in raw if ch.isdigit())

    def get_customer_name(self) -> str:
        """Recupera o nome de exibição do usuário."""
        if self.data and isinstance(self.data, dict):
            return str(self.data.get("pushName") or "Cliente")
        return "Cliente"

    def get_text_content(self) -> str:
        """Extrai o corpo do texto a partir de diferentes tipos de mensagens."""
        if not self.data or not isinstance(self.data, dict):
            return ""
        
        msg = self.data.get("message")
        if not msg or not isinstance(msg, dict):
            return ""

        # 1. Texto direto simples
        if "conversation" in msg and msg["conversation"]:
            return str(msg["conversation"]).strip()

        # 2. Texto estendido com formatação/links
        if "extendedTextMessage" in msg and isinstance(msg["extendedTextMessage"], dict):
            return str(msg["extendedTextMessage"].get("text", "")).strip()

        # 3. Resposta de botões
        if "buttonsResponseMessage" in msg and isinstance(msg["buttonsResponseMessage"], dict):
            return str(msg["buttonsResponseMessage"].get("selectedDisplayText") or 
                       msg["buttonsResponseMessage"].get("selectedButtonId", "")).strip()

        # 4. Resposta de listas
        if "listResponseMessage" in msg and isinstance(msg["listResponseMessage"], dict):
            single_select = msg["listResponseMessage"].get("singleSelectReply") or {}
            return str(single_select.get("selectedRowId", "")).strip()

        # 5. Legenda de imagem ou documento
        if "imageMessage" in msg and isinstance(msg["imageMessage"], dict):
            return str(msg["imageMessage"].get("caption", "")).strip()

        if "documentMessage" in msg and isinstance(msg["documentMessage"], dict):
            return str(msg["documentMessage"].get("caption", "")).strip()

        return ""

    def is_image_or_document(self) -> bool:
        """Verifica se a mensagem contém imagem ou documento (comprovante)."""
        if not self.data or not isinstance(self.data, dict):
            return False
        msg = self.data.get("message")
        if not msg or not isinstance(msg, dict):
            return False
        return "imageMessage" in msg or "documentMessage" in msg or "documentWithCaptionMessage" in msg

    def get_inline_base64(self) -> Optional[str]:
        """Tenta extrair o base64 da imagem se fornecido diretamente no webhook."""
        if not self.data or not isinstance(self.data, dict):
            return None
        # Verifica direto no data ou no message
        if "base64" in self.data and self.data["base64"]:
            return str(self.data["base64"])
        msg = self.data.get("message") or {}
        if "base64" in msg and msg["base64"]:
            return str(msg["base64"])
        img_msg = msg.get("imageMessage") or {}
        if "base64" in img_msg and img_msg["base64"]:
            return str(img_msg["base64"])
        return None


class BotState(BaseModel):
    """Estado unificado para orquestração da máquina de estados no LangGraph."""
    # Identificação Tenant
    company_id: str = ""
    owner_id: str = ""
    company_name: str = ""
    segment: str = ""
    instance_name: str = ""

    # Identificação Lead
    phone_number: str = ""
    customer_name: Optional[str] = "Cliente"

    # Controle de Ciclo
    current_step: str = "inicio"
    user_input: str = ""
    bot_response: str = ""

    # Negócio & Pedidos
    cart: List[Dict[str, Any]] = Field(default_factory=list)
    delivery_address: Optional[str] = None
    payment_method: Optional[str] = None
    order_id: Optional[str] = None

    # RAG & Handoff
    rag_context: Optional[str] = None
    human_handoff: bool = False

    # Camada Cognitiva & Memória
    chat_history: List[Dict[str, str]] = Field(default_factory=list)
    system_prompt_override: Optional[str] = None

    # Metadados de sessão
    session_metadata: Dict[str, Any] = Field(default_factory=dict)

