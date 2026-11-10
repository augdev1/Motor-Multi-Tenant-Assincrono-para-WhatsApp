import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Formatador customizado para emissão de logs estruturados em JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Anexa campos contextuais extras se presentes
        if hasattr(record, "audit_data") and isinstance(record.audit_data, dict):
            log_record["audit"] = record.audit_data

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record, ensure_ascii=False)


def setup_audit_logger(name: str = "audit") -> logging.Logger:
    """Configura e retorna o logger estruturado para trilha de auditoria."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger


audit_logger = setup_audit_logger("audit")


def log_audit_event(
    event_type: str,
    company_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    level: int = logging.INFO
) -> None:
    """Emite um registro estruturado de auditoria de forma padronizada."""
    audit_data = {
        "event_type": event_type,
        "company_id": company_id or "unresolved",
        "phone_number": phone_number or "unidentified",
        "execution_time_ms": execution_time_ms,
        "details": details or {},
    }

    audit_logger.log(
        level,
        f"[AUDIT] {event_type} | Tenant: {company_id or 'N/A'} | Phone: {phone_number or 'N/A'}",
        extra={"audit_data": audit_data}
    )
