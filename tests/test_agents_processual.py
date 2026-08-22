"""Testes do cliente da Anthropic — a fronteira que nenhum fake cobria.

Os testes do pipeline trocam o cliente inteiro por um fake que devolve texto,
então o formato real da resposta do SDK nunca era exercitado. Foi por aí que
passou o bug de 2026-08-22: `content[0]` é um bloco `thinking` no
claude-sonnet-5, e o resumo de todo movimento relevante quebrava em produção.
"""

from dataclasses import dataclass
from typing import Any

import pytest

from app.agents.processual import AnthropicMessagesClient


@dataclass
class _Bloco:
    type: str
    text: str = ""


class _MessagesFake:
    def __init__(self, blocos: list[_Bloco]) -> None:
        self._blocos = blocos
        self.chamada: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.chamada = kwargs
        return type("Resposta", (), {"content": self._blocos})()


def _cliente(blocos: list[_Bloco]) -> tuple[AnthropicMessagesClient, _MessagesFake]:
    cliente = AnthropicMessagesClient("chave-de-teste")
    messages = _MessagesFake(blocos)
    # Substitui o cliente do SDK: é justamente o formato da resposta dele que
    # queremos exercitar, e não há injeção pública para isso.
    cliente._client = type("SDK", (), {"messages": messages})()
    return cliente, messages


async def test_pega_o_texto_mesmo_com_bloco_de_thinking_antes():
    # Formato real do claude-sonnet-5, onde o thinking vem ligado por padrão.
    cliente, _ = _cliente([_Bloco("thinking"), _Bloco("text", "Resumo do movimento.")])

    texto = await cliente.create_message(
        system="prompt", user="entrada", model="claude-sonnet-5", max_tokens=4000
    )

    assert texto == "Resumo do movimento."


async def test_pega_o_texto_quando_ele_e_o_primeiro_bloco():
    cliente, _ = _cliente([_Bloco("text", "Resposta direta.")])

    texto = await cliente.create_message(
        system="prompt", user="entrada", model="claude-haiku-4-5", max_tokens=200
    )

    assert texto == "Resposta direta."


async def test_falha_alto_quando_nao_ha_bloco_de_texto():
    # Sem texto o resumo não existe — falhar aqui é melhor que devolver "" e
    # mandar uma notificação vazia pro cliente (CLAUDE.md §2).
    cliente, _ = _cliente([_Bloco("thinking")])

    with pytest.raises(ValueError, match="sem bloco de texto"):
        await cliente.create_message(
            system="prompt", user="entrada", model="claude-sonnet-5", max_tokens=4000
        )
