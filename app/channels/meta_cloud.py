from typing import Any

from app.channels.base import InboundMessage, MessageId

_NAO_IMPLEMENTADO = (
    "MetaCloudProvider é um stub — ativar quando migrarmos da UAZAPI (CLAUDE.md §4.7)"
)


class MetaCloudProvider:
    """Stub com a interface de ChannelProvider correta, sem lógica real."""

    async def send_text(self, to: str, text: str) -> MessageId:
        raise NotImplementedError(_NAO_IMPLEMENTADO)

    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId:
        raise NotImplementedError(_NAO_IMPLEMENTADO)

    def parse_webhook(self, payload: dict[str, Any]) -> InboundMessage:
        raise NotImplementedError(_NAO_IMPLEMENTADO)

    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool:
        raise NotImplementedError(_NAO_IMPLEMENTADO)

    async def aclose(self) -> None:
        # Stub sem cliente HTTP próprio — nada pra liberar ainda.
        pass
