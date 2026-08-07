from app.channels.base import ChannelProvider
from app.channels.meta_cloud import MetaCloudProvider
from app.channels.uazapi import UazapiProvider
from app.core.config import settings


def get_channel_provider() -> ChannelProvider:
    """Única fábrica de ChannelProvider — troca de gateway é variável de
    ambiente (ADVOGAI_CHANNEL_PROVIDER), nunca import direto (CLAUDE.md §4.7)."""
    if settings.channel_provider == "uazapi":
        return UazapiProvider(
            base_url=settings.uazapi_base_url,
            token=settings.uazapi_token,
            webhook_secret=settings.uazapi_webhook_secret,
            redis_url=settings.redis_url,
        )
    if settings.channel_provider == "meta_cloud":
        return MetaCloudProvider()
    raise ValueError(f"channel_provider não suportado: {settings.channel_provider!r}")
