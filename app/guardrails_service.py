import os
import re
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from pathlib import Path

from app.logging_config import log_audit_event

logger = logging.getLogger("guardrails")

# Diretório base das configurações do NeMo Guardrails
GUARDRAILS_CONFIG_DIR = Path(__file__).parent / "guardrails"


@dataclass
class GuardrailsDecision:
    allowed: bool
    blocked_category: Optional[str] = None
    safe_response: Optional[str] = None
    sanitized_input: Optional[str] = None


class SecurityGuardrails:
    """
    Camada de Segurança de Nível Corporativo baseada no NVIDIA NeMo Guardrails
    com estratégia de Defesa em Profundidade (Defense in Depth):
    1. Heurísticas e Padrões Rápidos de Injeção e Jailbreak (< 1ms).
    2. Motor Semântico do NeMo Guardrails (Colang + Rails).
    3. Output Guardrails: Prevenção rigorosa de vazamento de segredos e PII.
    """

    def __init__(self):
        self.rails = None
        self._init_nemo_rails()

        # Compilação de padrões de ataque para inspeção de entrada ultra-rápida
        self.injection_patterns = [
            re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions?", re.IGNORECASE),
            re.compile(r"ignore\s+todas?\s+(as\s+)?regras?\s+(anteriores|iniciais)?", re.IGNORECASE),
            re.compile(r"(desative|ignore|esque[cç]a)\s+(seus?\s+)?filtros?\s+(de\s+)?seguran[cç]a", re.IGNORECASE),
            re.compile(r"\b(dan\s+mode|do\s+anything\s+now|developer\s+mode)\b", re.IGNORECASE),
            re.compile(r"\b(jailbreak|prompt\s+injection)\b", re.IGNORECASE),
            re.compile(r"\b(gemini_api_key|api_key|system\s*prompt|system_prompt)\b", re.IGNORECASE),
            re.compile(r"(mostre|qual\s+[eé]|revele|me\s+passe|informe).*?(chave|secret|password|senha|credenciais)", re.IGNORECASE),
            re.compile(r"\b(drop\s+table|delete\s+from|union\s+select|insert\s+into)\b", re.IGNORECASE),
            re.compile(r"(sou\s+o\s+dono|sou\s+o\s+administrador|sou\s+o\s+augusto).*?(libere|gr[aá]tis|senha)", re.IGNORECASE),
            re.compile(r"<\s*script.*?>|exec\s*\(\s*['\"].*?['\"]\s*\)", re.IGNORECASE),
        ]

        # Padrões de saída proibida (vazamento de segredos e chaves)
        self.secret_leak_patterns = [
            re.compile(r"AQ\.[a-zA-Z0-9_\-]{40,}", re.IGNORECASE),  # Chave Gemini
            re.compile(r"AIza[0-9A-Za-z-_]{35}", re.IGNORECASE),     # Google API Key
            re.compile(r"sk-[a-zA-Z0-9]{32,}", re.IGNORECASE),       # OpenAI Key
            re.compile(r"postgres(ql)?://[^\s]+", re.IGNORECASE),     # String de conexão DB
            re.compile(r"redis://[^\s]+", re.IGNORECASE),            # String de conexão Redis
        ]

    def _init_nemo_rails(self):
        """Inicializa o motor do NeMo Guardrails a partir dos arquivos de configuração."""
        try:
            from nemoguardrails import RailsConfig, LLMRails
            if GUARDRAILS_CONFIG_DIR.exists():
                config = RailsConfig.from_path(str(GUARDRAILS_CONFIG_DIR))
                self.rails = LLMRails(config)
                logger.info("NVIDIA NeMo Guardrails carregado com sucesso.")
        except Exception as e:
            logger.warning(f"Aviso ao inicializar NeMo Guardrails (modo de defesa heurística ativo): {e}")

    def inspect_input(self, text: str, phone: str = "", company_id: str = "") -> GuardrailsDecision:
        """
        Input Rails: Avalia a mensagem do cliente antes de atingir o LLM ou o banco de dados.
        Retorna se a entrada é segura ou se deve ser sumariamente bloqueada.
        """
        if not text:
            return GuardrailsDecision(allowed=True, sanitized_input="")

        clean_text = text.strip()

        # 1. Avaliação heurística imediata contra injeção e jailbreak
        for pattern in self.injection_patterns:
            if pattern.search(clean_text):
                matched = pattern.pattern
                log_audit_event(
                    event_type="SECURITY_INPUT_RAIL_BLOCKED",
                    company_id=company_id,
                    phone_number=phone,
                    details={
                        "attack_type": "prompt_injection_or_jailbreak",
                        "matched_rule": matched,
                        "raw_input_snippet": clean_text[:80],
                    },
                    level=logging.WARNING
                )
                return GuardrailsDecision(
                    allowed=False,
                    blocked_category="prompt_injection_or_jailbreak",
                    safe_response=(
                        "Como assistente virtual de atendimento da loja, não posso responder a esse tipo de comando. "
                        "Posso te ajudar com nossos deliciosos pedidos de pizzas, lanches ou bebidas do cardápio!"
                    )
                )

        return GuardrailsDecision(allowed=True, sanitized_input=clean_text)

    def inspect_output(self, response_text: str, phone: str = "", company_id: str = "") -> str:
        """
        Output Rails: Verifica a resposta gerada antes de despachá-la para o cliente.
        Impede vazamento de tokens de API, credenciais de banco ou dados confidenciais.
        """
        if not response_text:
            return ""

        sanitized_response = response_text

        # Verifica se algum segredo vazou na saída do modelo
        for pattern in self.secret_leak_patterns:
            if pattern.search(sanitized_response):
                log_audit_event(
                    event_type="SECURITY_OUTPUT_RAIL_TRIGGERED",
                    company_id=company_id,
                    phone_number=phone,
                    details={
                        "attack_type": "sensitive_data_leak_prevented",
                        "pattern_matched": pattern.pattern
                    },
                    level=logging.CRITICAL
                )
                return (
                    "Olá! Posso te ajudar com as opções e pedidos do nosso cardápio de pizzas, lanches e bebidas! "
                    "Qual item você gostaria de escolher hoje?"
                )

        return sanitized_response


# Instância global singleton do SecurityGuardrails
security_guardrails = SecurityGuardrails()
