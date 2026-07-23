from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

MessageId = str


@dataclass(frozen=True)
class InboundMessage:
    from_number: str
    text: str
    message_id: str
    timestamp: datetime


class ChannelProvider(Protocol):
    # send_text/send_template são I/O de rede (async, como ProcessProvider em
    # app/providers/base.py); parse_webhook/verify_signature são cálculo puro
    # (sync). Diverge do snippet ilustrativo do CLAUDE.md §4.7, que mostra as
    # quatro operações síncronas — tratado aqui como ilustrativo da interface,
    # não como assinatura exata, para seguir a convenção do resto do código.
    async def send_text(self, to: str, text: str) -> MessageId: ...
    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId: ...
    def parse_webhook(self, payload: dict[str, Any]) -> InboundMessage: ...
    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool: ...
