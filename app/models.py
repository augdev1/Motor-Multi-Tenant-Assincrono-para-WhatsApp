import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base declarativa SQLAlchemy."""
    pass


class Company(Base):
    """Representa a empresa/tenant cadastrada no sistema."""
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    owner_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    instance_name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(50), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    settings: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    # Relacionamentos
    sessions: Mapped[List["CustomerSession"]] = relationship("CustomerSession", back_populates="company", cascade="all, delete-orphan")
    chunks: Mapped[List["KnowledgeChunk"]] = relationship("KnowledgeChunk", back_populates="company", cascade="all, delete-orphan")
    orders: Mapped[List["OrderRecord"]] = relationship("OrderRecord", back_populates="company", cascade="all, delete-orphan")
    messages: Mapped[List["MessageHistory"]] = relationship("MessageHistory", back_populates="company", cascade="all, delete-orphan")


class CustomerSession(Base):
    """Sessão e histórico de conversa por cliente dentro de um tenant."""
    __tablename__ = "customer_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), index=True, nullable=False)
    phone_number: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    customer_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    current_step: Mapped[str] = mapped_column(String(50), default="inicio")
    session_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    last_interaction: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now(), onupdate=utc_now)

    company: Mapped["Company"] = relationship("Company", back_populates="sessions")


class KnowledgeChunk(Base):
    """Fragmentos de conhecimento do catálogo para recuperação RAG vetorial."""
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_info: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding: Mapped[List[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    company: Mapped["Company"] = relationship("Company", back_populates="chunks")


class OrderRecord(Base):
    """Registro de pedidos finalizados com comanda de expedição."""
    __tablename__ = "order_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), index=True, nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    items: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    delivery_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payment_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="recebido")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    company: Mapped["Company"] = relationship("Company", back_populates="orders")


class AuditLog(Base):
    """Trilha de auditoria para observabilidade de eventos e métricas de execução."""
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class MessageHistory(Base):
    """Histórico de mensagens conversacionais multi-tenant para memória deslizante."""
    __tablename__ = "message_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), index=True, nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' ou 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    company: Mapped["Company"] = relationship("Company", back_populates="messages")


