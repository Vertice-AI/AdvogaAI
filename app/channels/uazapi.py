import asyncio
import hmac
import random
import re
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import redis.asyncio as aioredis
import structlog

from app.channels.base import InboundMessage, MessageId

logger = structlog.get_logger()

_TIMEOUT_SEGUNDOS = 10.0
_MAX_TENTATIVAS = 3
_BACKOFF_BASE_SEGUNDOS = 0.5
# CLAUDE.md §4.7: intervalo mínimo de 3 a 5s entre envios, com jitter.
_INTERVALO_MINIMO_SEGUNDOS = 3.0
_JITTER_MAXIMO_SEGUNDOS = 2.0
# Suspeita em teste (2026-08-09): ReadTimeout de 10.3s reproduz 100% das
# vezes quando send_text responde a uma mensagem que ACABOU de chegar via
# webhook real — nunca reproduz em envio isolado/sintético (mesmo número,
# mesmo código). Terceiros documentam um campo "delay" (ms) no /send/text de
# APIs Baileys-like que mostra "digitando..." e parece desacoplar o envio
# síncrono do processamento interno da instância — não confirmado na doc
# oficial (SPA em JS, sem acesso a navegador nesta investigação). Testando
# como mitigação; se não resolver, reverter (CLAUDE.md §8).
_DELAY_DIGITACAO_MS = 3000
# Chave usada dentro do dict `headers` de verify_signature — não é um header
# HTTP literal da UAZAPI (ela não assina as chamadas que faz ao nosso
# webhook). Quem registra o webhook embute esse valor antes de chamar
# verify_signature; ver docstring do método.
_WEBHOOK_SECRET_KEY = "webhook_secret"
# Lock distribuído (Redis) pra serializar envios entre processos diferentes
# do worker. Achado em produção (2026-08-06/07): cada task cria uma
# UazapiProvider nova, então um asyncio.Lock por instância não protege nada
# entre tasks concorrentes (worker roda com concurrency>1, processos
# separados). Duas mensagens processadas ao mesmo tempo batem na UAZAPI sem
# nenhum espaçamento — o backend dela trava numa das duas, e o timeout de
# 10s do nosso lado estoura (ReadTimeout).
#
# TTL do lock precisa cobrir o PIOR CASO do que roda dentro dele (rate limit
# + _chamar_com_retry), não só o timeout de uma tentativa — 20s (fixado na
# primeira versão) era curto demais e expirava com a operação ainda em
# andamento, causando LockNotOwnedError na hora de liberar (achado em
# produção logo depois do primeiro deploy desse fix, 2026-08-07). Pior caso:
# espera de rate limit (até 5s) + 3 tentativas de _TIMEOUT_SEGUNDOS (10s)
# cada + backoff entre elas (~1.5s) ≈ 36.5s — 60s dá margem confortável.
_CHAVE_LOCK_ENVIO = "advogai:uazapi:envio_lock"
_LOCK_TTL_SEGUNDOS = 60
_LOCK_ESPERA_MAXIMA_SEGUNDOS = 60.0


class UazapiProvider:
    def __init__(
        self,
        base_url: str,
        token: str,
        webhook_secret: str,
        client: httpx.AsyncClient | None = None,
        redis_url: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._webhook_secret = webhook_secret
        # max_keepalive_connections=0: nunca reaproveita conexão HTTP entre
        # requisições — cada tentativa de _chamar_com_retry abre conexão nova.
        # Achado em produção (2026-08-07): um worker de vida longa (10h+ no
        # ar) acumulou um ReadTimeout que se repetia nas 3 tentativas de uma
        # mesma task, sempre a mesma mensagem — consistente com reaproveitar
        # uma conexão persistente que ficou "meio-quebrada". Testes isolados
        # feitos logo após um redeploy (conexão sempre nova) nunca reproduziram
        # isso. Custa uma conexão TCP/TLS a mais por tentativa, aceitável pro
        # volume de mensagens do Solo.
        self._client = client or httpx.AsyncClient(
            timeout=_TIMEOUT_SEGUNDOS,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self._redis = aioredis.from_url(redis_url, decode_responses=True) if redis_url else None
        # Fallback local (sem Redis, ex.: testes unitários) — só protege
        # dentro do mesmo processo, mas mantém o comportamento anterior.
        self._envio_lock = asyncio.Lock()
        self._ultimo_envio: float | None = None

    async def send_text(self, to: str, text: str) -> MessageId:
        async with self._lock_de_envio():
            await self._respeitar_rate_limit()
            resposta = await self._chamar_com_retry(
                "POST",
                "/send/text",
                {"number": to, "text": text, "delay": _DELAY_DIGITACAO_MS},
            )
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

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._redis is not None:
            await self._redis.aclose()

    def _lock_de_envio(self) -> AbstractAsyncContextManager[Any]:
        if self._redis is not None:
            return self._redis.lock(
                _CHAVE_LOCK_ENVIO,
                timeout=_LOCK_TTL_SEGUNDOS,
                blocking_timeout=_LOCK_ESPERA_MAXIMA_SEGUNDOS,
            )
        return _sem_lock()

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
        # Log de timing temporário — investigação do ReadTimeout intermitente
        # que só aparece no fluxo real (processar_mensagem_recebida), nunca
        # na task de diagnóstico isolada (mesmo código, mesmo processo). Os
        # timestamps do Railway chegam fora de ordem (log buffering), então
        # a duração aqui é medida com time.monotonic() no próprio processo —
        # dado confiável independente da ordem de entrega do log. Remover
        # depois que o bug for identificado (CLAUDE.md §8).
        url = f"{self._base_url}{path}"
        for tentativa in range(_MAX_TENTATIVAS):
            ultima_tentativa = tentativa == _MAX_TENTATIVAS - 1
            inicio = time.monotonic()
            try:
                resposta = await self._client.request(
                    metodo, url, json=corpo, headers=self._headers()
                )
                resposta.raise_for_status()
                logger.warning(
                    "uazapi_tentativa_ok",
                    metodo=metodo,
                    path=path,
                    tentativa=tentativa,
                    duracao=f"{time.monotonic() - inicio:.2f}s",
                )
                return cast(dict[str, Any], resposta.json())
            except httpx.HTTPStatusError as erro:
                logger.warning(
                    "uazapi_tentativa_falhou",
                    metodo=metodo,
                    path=path,
                    tentativa=tentativa,
                    tipo="HTTPStatusError",
                    status=erro.response.status_code,
                    duracao=f"{time.monotonic() - inicio:.2f}s",
                )
                if erro.response.status_code < 500 or ultima_tentativa:
                    raise
            except (httpx.TimeoutException, httpx.TransportError) as erro:
                logger.warning(
                    "uazapi_tentativa_falhou",
                    metodo=metodo,
                    path=path,
                    tentativa=tentativa,
                    tipo=type(erro).__name__,
                    duracao=f"{time.monotonic() - inicio:.2f}s",
                )
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


@asynccontextmanager
async def _sem_lock() -> AsyncIterator[None]:
    """Fallback quando não há Redis configurado (ex.: testes unitários) — só
    mantém a interface de context manager, sem exclusão entre processos."""
    yield
