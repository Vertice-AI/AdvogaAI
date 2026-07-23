import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenant"
    __table_args__ = (CheckConstraint("plano IN ('solo', 'escritorio')", name="ck_tenant_plano"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(200))
    plano: Mapped[str] = mapped_column(String(20))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Cliente(Base):
    __tablename__ = "cliente"
    # Único por tenant, não global: o mesmo número de WhatsApp pode ser
    # cliente de dois escritórios diferentes.
    __table_args__ = (
        UniqueConstraint("tenant_id", "whatsapp_numero", name="uq_cliente_tenant_whatsapp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    nome: Mapped[str] = mapped_column(String(200))
    whatsapp_numero: Mapped[str] = mapped_column(String(20))
    verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Advogado(Base):
    __tablename__ = "advogado"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    nome: Mapped[str] = mapped_column(String(200))
    area_atuacao: Mapped[str] = mapped_column(String(100))
    disponivel: Mapped[bool] = mapped_column(Boolean, default=True)


class Processo(Base):
    __tablename__ = "processo"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    cliente_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cliente.id"))
    numero: Mapped[str] = mapped_column(String(30))
    tribunal_alias: Mapped[str] = mapped_column(String(20))
    advogado_responsavel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("advogado.id"), nullable=True
    )


class Movimento(Base):
    __tablename__ = "movimento"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    processo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("processo.id"))
    data: Mapped[date] = mapped_column(Date)
    tipo: Mapped[str] = mapped_column(String(100))
    texto_origem: Mapped[str] = mapped_column(Text)
    relevante: Mapped[bool] = mapped_column(Boolean)
    resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    guardrail_passou: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decisao: Mapped[str] = mapped_column(String(20))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
