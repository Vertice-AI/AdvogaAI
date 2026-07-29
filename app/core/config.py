from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADVOGAI_", env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-5"

    datajud_api_key: str = ""
    datajud_base_url: str = "https://api-publica.datajud.cnj.jus.br"

    database_url: str = "postgresql+psycopg://advogai_app:advogai_app@localhost:5432/advogai"

    redis_url: str = "redis://localhost:6379/0"
    process_provider: str = "datajud"

    channel_provider: str = "uazapi"
    uazapi_base_url: str = ""
    uazapi_token: str = ""
    uazapi_webhook_secret: str = ""

    # Webhook fora-de-banda pra alertas operacionais (queda da instância
    # UAZAPI). Formato {"text": ...} — Slack/Discord/Mattermost. Vazio = alerta
    # vira no-op (dev/local). Fora-de-banda de propósito (CLAUDE.md §4.7): o
    # alerta não pode depender do WhatsApp que ele está monitorando.
    alert_webhook_url: str = ""


settings = Settings()
