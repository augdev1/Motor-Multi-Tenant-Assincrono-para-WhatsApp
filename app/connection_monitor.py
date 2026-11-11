import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.logging_config import log_audit_event
from app.models import AuditLog, Company
from app.schemas import EvolutionMessagePayload

logger = logging.getLogger("whatsapp_engine.connection_monitor")


class ConnectionMonitorService:
    """
    Serviço de monitoramento de alta disponibilidade da Evolution API v2.
    Supervisiona eventos CONNECTION_UPDATE para detectar quedas de bateria ou internet
    no aparelho do lojista e emitir alertas imediatos de reconexão.
    """

    STATE_OPEN = "open"
    STATE_CLOSE = "close"
    STATE_CONNECTING = "connecting"
    STATE_REFUSED = "refused"
    STATE_DISCONNECTED = "disconnected"

    OFFLINE_STATES = {STATE_CLOSE, STATE_REFUSED, STATE_DISCONNECTED}

    async def handle_connection_update(
        self,
        payload: EvolutionMessagePayload,
        db: AsyncSession,
        redis=None
    ) -> JSONResponse:
        """
        Processa o evento CONNECTION_UPDATE originado pela Evolution API.
        Atualiza o estado de prontidão da instância e aciona alertas operacionais se houver desconexão.
        """
        instance_name = payload.get_instance_name()
        state = payload.get_connection_state()
        reason = payload.get_connection_status_reason()

        if not instance_name:
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "missing_instance_name"}
            )

        # 1. Resolução do Tenant proprietário da instância
        stmt = select(Company).where(Company.instance_name == instance_name)
        result = await db.execute(stmt)
        company = result.scalar_one_or_none()

        if not company:
            logger.warning(
                f"[CONNECTION_UPDATE] Recebido evento para instância não cadastrada: '{instance_name}' (state={state})"
            )
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "company_not_found", "instance": instance_name}
            )

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # 2. Atualização dos metadados de conectividade da empresa
        settings = dict(company.settings or {})
        previous_state = settings.get("connection_status", "unknown")
        settings["connection_status"] = state
        settings["connection_reason"] = reason
        settings["last_connection_update"] = now_iso

        is_offline = state in self.OFFLINE_STATES
        alert_triggered = False
        alert_message = None

        if is_offline:
            alert_triggered = True
            if not settings.get("disconnected_at"):
                settings["disconnected_at"] = now_iso

            alert_message = (
                f"ALERTA CRÍTICO: Conexão do WhatsApp da empresa '{company.name}' (Instância: {instance_name}) "
                f"CAIU! Estado atual: '{state}' (Motivo: {reason}). "
                f"O smartphone do lojista pode estar sem internet ou descarregado. Reconecte o WhatsApp imediatamente."
            )
            logger.critical(f"🚨 {alert_message}")

            # Registro de auditoria persistente de alta prioridade
            audit = AuditLog(
                company_id=company.id,
                event_type="WHATSAPP_DISCONNECTED_ALERT",
                phone_number=company.phone_number,
                payload={
                    "instance": instance_name,
                    "company_name": company.name,
                    "previous_state": previous_state,
                    "current_state": state,
                    "reason": reason,
                    "severity": "CRITICAL",
                    "alert_message": alert_message,
                },
                execution_time_ms=0,
            )
            db.add(audit)

            # Persistência do alerta em cache distribuído (Redis) para checagens em tempo real
            if redis:
                await redis.set(f"evolution:status:{instance_name}", state)
                await redis.set(f"evolution:alert:{instance_name}", alert_message)
                if not await redis.get(f"evolution:disconnected_at:{instance_name}"):
                    await redis.set(f"evolution:disconnected_at:{instance_name}", now_iso)

            log_audit_event(
                event_type="WHATSAPP_CONNECTION_DOWN",
                company_id=company.id,
                details={
                    "instance": instance_name,
                    "state": state,
                    "reason": reason,
                    "alert": alert_message,
                }
            )

            await self.dispatch_push_alert(
                company=company,
                instance_name=instance_name,
                state=state,
                reason=reason,
                alert_message=alert_message,
                redis=redis,
            )

        elif state == self.STATE_OPEN:
            settings.pop("disconnected_at", None)
            settings["last_connected_at"] = now_iso

            info_message = (
                f"CONEXÃO RESTABELECIDA: WhatsApp da empresa '{company.name}' (Instância: {instance_name}) "
                f"está conectado e operacional (Estado: '{state}')."
            )
            logger.info(f"✅ {info_message}")

            audit = AuditLog(
                company_id=company.id,
                event_type="WHATSAPP_CONNECTED",
                phone_number=company.phone_number,
                payload={
                    "instance": instance_name,
                    "company_name": company.name,
                    "state": state,
                    "severity": "INFO",
                    "message": info_message,
                },
                execution_time_ms=0,
            )
            db.add(audit)

            if redis:
                await redis.set(f"evolution:status:{instance_name}", self.STATE_OPEN)
                await redis.delete(f"evolution:alert:{instance_name}")
                await redis.delete(f"evolution:disconnected_at:{instance_name}")
                await redis.delete(f"evolution:alert_cooldown:{instance_name}")

            log_audit_event(
                event_type="WHATSAPP_CONNECTION_UP",
                company_id=company.id,
                details={"instance": instance_name, "state": state}
            )

        company.settings = settings
        await db.commit()

        return JSONResponse(
            status_code=200,
            content={
                "status": "processed",
                "event": "CONNECTION_UPDATE",
                "instance": instance_name,
                "company_id": company.id,
                "company_name": company.name,
                "state": state,
                "is_connected": state == self.STATE_OPEN,
                "alert_triggered": alert_triggered,
                "alert_message": alert_message,
            }
        )

    def get_status_summary(self, company: Company) -> Dict[str, Any]:
        """Calcula o sumário de saúde da conexão da empresa, incluindo tempo de queda."""
        settings = company.settings or {}
        status = settings.get("connection_status", "unknown")
        disconnected_at_str = settings.get("disconnected_at")

        downtime_minutes = None
        if disconnected_at_str:
            try:
                disconnected_at = datetime.fromisoformat(disconnected_at_str)
                delta = datetime.now(timezone.utc) - disconnected_at
                downtime_minutes = round(delta.total_seconds() / 60.0, 1)
            except Exception:
                pass

        is_connected = status == self.STATE_OPEN
        return {
            "company_id": company.id,
            "company_name": company.name,
            "instance_name": company.instance_name,
            "connection_status": status,
            "is_connected": is_connected,
            "last_update": settings.get("last_connection_update"),
            "disconnected_at": disconnected_at_str,
            "downtime_minutes": downtime_minutes,
            "alert_required": not is_connected and status in self.OFFLINE_STATES,
        }

    async def dispatch_push_alert(
        self,
        company: Company,
        instance_name: str,
        state: str,
        reason: Optional[Any],
        alert_message: str,
        redis=None,
        cooldown_seconds: int = 1800,
    ) -> bool:
        """
        Dispara notificação HTTP ativa (Slack/Discord/n8n/Webhook) quando uma instância cai.
        Aplica cooldown atômico no Redis (padrão 30 minutos) para evitar spam durante oscilações.
        """
        alert_url = (app_settings.ALERT_WEBHOOK_URL or "").strip()
        if not alert_url:
            return False

        if redis:
            cooldown_key = f"evolution:alert_cooldown:{instance_name}"
            acquired = await redis.set(cooldown_key, "1", nx=True, ex=cooldown_seconds)
            if not acquired:
                logger.info(
                    f"[ALERT_COOLDOWN] Alerta ativo já enviado recentemente para '{instance_name}'."
                )
                return False

        payload = {
            "event": "WHATSAPP_DISCONNECTED_ALERT",
            "severity": "CRITICAL",
            "company_id": company.id,
            "company_name": company.name,
            "instance_name": instance_name,
            "state": state,
            "reason": reason,
            "text": alert_message,
            "content": alert_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(alert_url, json=payload)
                return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.error(f"[ALERT_DISPATCH_FAILED] Falha ao enviar webhook de alerta para {alert_url}: {exc}")
            return False


connection_monitor = ConnectionMonitorService()

