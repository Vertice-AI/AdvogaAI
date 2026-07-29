"""Envio de alertas operacionais fora-de-banda.

Fora-de-banda de propósito (CLAUDE.md §4.7): o alerta de queda da instância
UAZAPI não pode sair pelo próprio WhatsApp que ele monitora — quando a
instância cai, o envio pela UAZAPI também para. Por isso o canal é um webhook
HTTP independente (Slack/Discord/Mattermost, formato ``{"text": ...}``).
"""

import asyncio

import httpx
import structlog

logger = structlog.get_logger()

_TIMEOUT_SEGUNDOS = 10.0
_MAX_TENTATIVAS = 3
_BACKOFF_BASE_SEGUNDOS = 0.5


async def enviar_alerta(
    texto: str, webhook_url: str, client: httpx.AsyncClient | None = None
) -> None:
    """Posta ``texto`` no webhook de alerta, com timeout e retry explícitos
    (CLAUDE.md §6). Sem URL configurada, é no-op silencioso — em dev/local não
    há canal e isso não pode quebrar o healthcheck."""
    if not webhook_url:
        logger.info("alerta_sem_canal_configurado", texto=texto)
        return

    proprio_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS)
    try:
        for tentativa in range(_MAX_TENTATIVAS):
            ultima_tentativa = tentativa == _MAX_TENTATIVAS - 1
            try:
                resposta = await client.post(webhook_url, json={"text": texto})
                resposta.raise_for_status()
                return
            except httpx.HTTPStatusError as erro:
                if erro.response.status_code < 500 or ultima_tentativa:
                    raise
            except (httpx.TimeoutException, httpx.TransportError):
                if ultima_tentativa:
                    raise
            await asyncio.sleep(_BACKOFF_BASE_SEGUNDOS * (2**tentativa))
    finally:
        if proprio_client:
            await client.aclose()
