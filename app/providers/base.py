from typing import Protocol


class ProcessProvider(Protocol):
    async def buscar_movimentos(
        self, numero_processo: str, tribunal_alias: str
    ) -> list[dict[str, str]]: ...
