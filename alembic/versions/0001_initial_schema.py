"""Initial multi-tenant schema with pgvector HNSW index

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-11 16:27:00.000000
"""
from typing import Sequence, Union

import pgvector.sqlalchemy
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute("CREATE SCHEMA IF NOT EXISTS evolution;")

    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("instance_name", sa.String(length=100), nullable=False),
        sa.Column("segment", sa.String(length=50), nullable=False),
        sa.Column("phone_number", sa.String(length=30), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companies_instance_name", "companies", ["instance_name"], unique=True)

    op.create_table(
        "customer_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("phone_number", sa.String(length=30), nullable=False),
        sa.Column("customer_name", sa.String(length=100), nullable=True),
        sa.Column("current_step", sa.String(length=50), nullable=False),
        sa.Column("session_data", sa.JSON(), nullable=False),
        sa.Column("last_interaction", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customer_sessions_company_id", "customer_sessions", ["company_id"], unique=False)
    op.create_index("ix_customer_sessions_phone_number", "customer_sessions", ["phone_number"], unique=False)

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_info", sa.JSON(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(768), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_chunks_company_id", "knowledge_chunks", ["company_id"], unique=False)
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "order_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("customer_phone", sa.String(length=30), nullable=False),
        sa.Column("customer_name", sa.String(length=100), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_records_company_id", "order_records", ["company_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("phone_number", sa.String(length=30), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_company_id", "audit_logs", ["company_id"], unique=False)

    op.create_table(
        "message_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("customer_phone", sa.String(length=30), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_history_company_id", "message_history", ["company_id"], unique=False)
    op.create_index("ix_message_history_customer_phone", "message_history", ["customer_phone"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_message_history_customer_phone", table_name="message_history")
    op.drop_index("ix_message_history_company_id", table_name="message_history")
    op.drop_table("message_history")

    op.drop_index("ix_audit_logs_company_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_order_records_company_id", table_name="order_records")
    op.drop_table("order_records")

    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_company_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index("ix_customer_sessions_phone_number", table_name="customer_sessions")
    op.drop_index("ix_customer_sessions_company_id", table_name="customer_sessions")
    op.drop_table("customer_sessions")

    op.drop_index("ix_companies_instance_name", table_name="companies")
    op.drop_table("companies")

