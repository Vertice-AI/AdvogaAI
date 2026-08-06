import asyncio
import hmac
import random
import re
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from app.channels.base import InboundMessage, MessageId

_TIMEOUT_SEGUNDOS = 10.0
_MAX_TENTATIVAS = 3
_BACKOFF_BASE_SEGUNDOS = 0.5
# CLAUDE.md §4.7: intervalo mínimo de 3 a 5s entre envios, com jitter.
_INTERVALO_MINIMO_SEGUNDOS = 3.0
_JITTER_MAXIMO_SEGUNDOS = 2.0
# Chave usada dentro do dict `headers` de verify_signature — não é um header
# HTTP literal da UAZAPI (ela não assina as chamadas que faz ao nosso
# webhook). Quem registra o webhook embute esse valor antes de chamar
# verify_signature; ver docstring do método.
_WEBHOOK_SECRET_KEY = "webhook_secret"


class UazapiProvider:
    def __init__(
        self,
        base_url: str,
        token: str,
        webhook_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._webhook_secret = webhook_secret
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS)
        self._envio_lock = asyncio.Lock()
        self._ultimo_envio: float | None = None

    async def send_text(self, to: str, text: str) -> MessageId:
        await self._respeitar_rate_limit()
        resposta = await self._chamar_com_retry("POST", "/send/text", {"number": to, "text": text})
        return _extrair_message_id(resposta)

    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId:
        # UAZAPI não tem janela de 24h nem template aprovado (CLAUDE.md §4.7)
        # — renderiza localmente e envia como texto comum. Mantém a chamada
        # separada de send_text para não reescrever a lógica de notificação
        # quando migrarmos para a Meta Cloud API (que exige template real).
        texto_renderizado = template.format(**params)
        return await self.send_text(to, texto_renderizado)

    def parse_webhook(self, payload: dict[str, Any]) -> InboundMessage:
        # Schema real da instância (confirmado em produção, diverge da doc
        # oficial): o evento vem em "EventType", não "event", e o corpo da
        # mensagem em "message", não "data". Os campos internos (sender,
        # chatid, text, messageid, messageTimestamp, fromMe) são os mesmos.
        evento = payload.get("EventType")
        if evento != "messages":
            raise ValueError(f"parse_webhook só entende o evento 'messages', recebeu {evento!r}")

        dados = payload.get("message")
        if not isinstance(dados, dict):
            # ValueError (não TypeError) de propósito: é o mesmo tipo de erro
            # das outras validações desta função, e quem chama (app/api/webhooks.py)
            # trata tudo como payload malformado/inesperado com um único except ValueError.
            raise ValueError("payload de webhook sem campo 'message'")  # noqa: TRY004

        numero = _extrair_numero(str(dados.get("sender") or dados.get("chatid") or ""))
        timestamp_ms = dados.get("messageTimestamp")
        if not numero or timestamp_ms is None:
            raise ValueError(
                "payload de webhook incompleto (sender/chatid ou messageTimestamp ausente)"
            )

        return InboundMessage(
            from_number=numero,
            text=str(dados.get("text") or ""),
            message_id=str(dados.get("messageid") or dados.get("id") or ""),
            timestamp=datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC),
            from_me=bool(dados.get("fromMe", False)),
        )

    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool:
        """A UAZAPI não assina as chamadas que faz para o nosso webhook — sem
        HMAC, sem token de volta (confirmado na doc oficial; CLAUDE.md §4.7
        já previa isso: "não invente assinatura que não existe"). O segredo
        aqui é nosso: a rota que recebe o webhook (app/api/, fora do escopo
        desta fatia) precisa embutir um valor verificável na URL registrada
        junto à UAZAPI e repassá-lo em `headers[_WEBHOOK_SECRET_KEY]` antes de
        chamar este método — não é um header HTTP que a UAZAPI envia.
        """
        recebido = headers.get(_WEBHOOK_SECRET_KEY, "")
        return hmac.compare_digest(recebido, self._webhook_secret)

    async def verificar_status(self) -> str:
        # A resposta tem dois campos "status" em níveis diferentes: o de
        # nível raiz é um OBJETO ({"connected": bool, "loggedIn": bool, ...}),
        # não o estado que queremos. O enum de estado (connected/connecting/
        # disconnected/hibernated) mora em resposta["instance"]["status"] —
        # confirmado na doc oficial (docs.uazapi.com, schema de resposta do
        # GET /instance/status). Pegar o campo errado faz o healthcheck achar
        # a instância sempre "desconhecida"/não saudável mesmo conectada.
        resposta = await self._chamar_com_retry("GET", "/instance/status", None)
        instancia = resposta.get("instance")
        estado = instancia.get("status") if isinstance(instancia, dict) else None
        return str(estado or "desconhecido")

    async def _respeitar_rate_limit(self) -> None:
        async with self._envio_lock:
            loop = asyncio.get_running_loop()
            agora = loop.time()
            if self._ultimo_envio is not None:
                intervalo = _INTERVALO_MINIMO_SEGUNDOS + random.uniform(0, _JITTER_MAXIMO_SEGUNDOS)
                espera = self._ultimo_envio + intervalo - agora
                if espera > 0:
                    await asyncio.sleep(espera)
            self._ultimo_envio = loop.time()

    def _headers(self) -> dict[str, str]:
        return {
            "token": self._token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _chamar_com_retry(
        self, metodo: str, path: str, corpo: dict[str, Any] | None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        for tentativa in range(_MAX_TENTATIVAS):
            ultima_tentativa = tentativa == _MAX_TENTATIVAS - 1
            try:
                resposta = await self._client.request(
                    metodo, url, json=corpo, headers=self._headers()
                )
                resposta.raise_for_status()
                return cast(dict[str, Any], resposta.json())
            except httpx.HTTPStatusError as erro:
                if erro.response.status_code < 500 or ultima_tentativa:
                    raise
            except (httpx.TimeoutException, httpx.TransportError):
                if ultima_tentativa:
                    raise
            await asyncio.sleep(_BACKOFF_BASE_SEGUNDOS * (2**tentativa))
        raise RuntimeError("loop de retry terminou sem retornar nem levantar exceção")


def _extrair_message_id(resposta: dict[str, Any]) -> MessageId:
    message_id = resposta.get("id") or resposta.get("messageid")
    if not message_id:
        raise ValueError(f"resposta da UAZAPI sem id de mensagem: {resposta!r}")
    return str(message_id)


def _extrair_numero(jid: str) -> str:
    return re.sub(r"@.*$", "", jid)
