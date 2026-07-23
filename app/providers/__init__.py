from app.core.config import settings
from app.providers.base import ProcessProvider
from app.providers.datajud import DataJudProvider


def get_process_provider() -> ProcessProvider:
    """Única fábrica de ProcessProvider — troca de fornecedor é variável de
    ambiente (ADVOGAI_PROCESS_PROVIDER), nunca import direto (CLAUDE.md §4.2)."""
    if settings.process_provider == "datajud":
        return DataJudProvider(api_key=settings.datajud_api_key, base_url=settings.datajud_base_url)
    raise ValueError(f"process_provider não suportado: {settings.process_provider!r}")
