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
    __table_args__ = (
        CheckConstraint("plano IN ('solo', 'escritorio')", name="ck_tenant_plano"),
        CheckConstraint(
            "nivel_autonomia_padrao IN ('automatico', 'aprovacao_manual')",
            name="ck_tenant_nivel_autonomia_padrao",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(200))
    plano: Mapped[str] = mapped_column(String(20))
    # Usado pelo worker de sincronização (app/workers/sync_processual.py) até
    # existir granularidade por cliente/categoria de movimento (CLAUDE.md §4.3).
    # 'aprovacao_manual' é o default seguro.
    nivel_autonomia_padrao: Mapped[str] = mapped_column(
        String(20), server_default="aprovacao_manual"
    )
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
    # Número pessoal do advogado (não o número do escritório que fala com os
    # clientes) — usado pra pedir aprovação de movimentos needs_approval e
    # reconhecer o comando de resposta (app/services/aprovacoes.py).
    __table_args__ = (
        UniqueConstraint("tenant_id", "whatsapp_numero", name="uq_advogado_tenant_whatsapp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    nome: Mapped[str] = mapped_column(String(200))
    oab: Mapped[str | None] = mapped_column(String(20), nullable=True)
    whatsapp_numero: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    # Deduplicação do worker de sincronização: o provider devolve a lista
    # inteira de movimentos a cada chamada, não só os novos (app/workers/sync_processual.py).
    __table_args__ = (
        UniqueConstraint("processo_id", "data", "tipo", "texto_origem", name="uq_movimento_dedupe"),
    )

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
    # Marca que já pedimos aprovação ao advogado responsável (evita pedir de
    # novo a cada execução do job) — só relevante quando decisao=needs_approval.
    aprovacao_solicitada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ConversaEstado(Base):
    __tablename__ = "conversa_estado"
    # Não referencia Cliente de propósito: a saudação diária e a regra do
    # silêncio (CLAUDE.md §4.5) valem também para números ainda não
    # vinculados a nenhum Cliente (§4.6) — o estado é por número, não por
    # cadastro.
    __table_args__ = (
        UniqueConstraint("tenant_id", "whatsapp_numero", name="uq_conversa_estado_tenant_whatsapp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    whatsapp_numero: Mapped[str] = mapped_column(String(20))
    ultima_saudacao_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    atendimento_humano_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True enquanto a próxima mensagem desse número deve ser interpretada
    # como os dados de vinculação (nome/CPF/processo), não como uma nova
    # pergunta (app/services/atendimento.py).
    aguardando_dados_vinculo: Mapped[bool] = mapped_column(Boolean, default=False)


class SolicitacaoVinculo(Base):
    __tablename__ = "solicitacao_vinculo"
    # Registro do que a pessoa informou pra pedir vínculo — nunca usado pelo
    # agente pra liberar informação sozinho (CLAUDE.md §4.6, "sem exceção").
    # Só serve de contexto pro advogado decidir e vincular manualmente.

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"))
    whatsapp_numero: Mapped[str] = mapped_column(String(20))
    nome_informado: Mapped[str] = mapped_column(String(200))
    cpf_informado: Mapped[str] = mapped_column(String(11))
    numero_processo_informado: Mapped[str | None] = mapped_column(String(30), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Preenchido quando o advogado resolve via comando "vincular"/"descartar"
    # (app/services/aprovacoes.py) — evita reprocessar a mesma solicitação.
    resolvida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
