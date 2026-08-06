import json
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.channels import get_channel_provider
from app.workers.processar_mensagem import processar_mensagem_recebida

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/uazapi/{tenant_id}")
async def receber_webhook_uazapi(tenant_id: uuid.UUID, request: Request) -> Response:
    corpo = await request.body()
    segredo = request.query_params.get("secret", "")

    channel = get_channel_provider()
    if not channel.verify_signature(corpo, {"webhook_secret": segredo}):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="assinatura inválida")

    try:
        payload = json.loads(corpo)
        inbound = channel.parse_webhook(payload)
    except ValueError as erro:
        # eventos que não são "messages" (connection, presence, etc.) ou
        # payload mal formado — não é erro nosso, só não processamos aqui.
        # Log só de estrutura (chaves), nunca de conteúdo — CLAUDE.md §6
        # proíbe logar texto de relato do cliente.
        dados = payload.get("data") if isinstance(payload, dict) else None
        logger.warning(
            "webhook_uazapi_nao_processado",
            erro=str(erro),
            evento=payload.get("event") if isinstance(payload, dict) else None,
            chaves_payload=sorted(payload.keys()) if isinstance(payload, dict) else None,
            chaves_data=sorted(dados.keys()) if isinstance(dados, dict) else None,
        )
        return Response(status_code=status.HTTP_200_OK)

    processar_mensagem_recebida.delay(
        tenant_id=str(tenant_id),
        from_number=inbound.from_number,
        text=inbound.text,
        message_id=inbound.message_id,
        timestamp_iso=inbound.timestamp.isoformat(),
        from_me=inbound.from_me,
    )
    return Response(status_code=status.HTTP_200_OK)
