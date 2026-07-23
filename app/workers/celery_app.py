from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery("advogai", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    timezone="America/Sao_Paulo",
    beat_schedule={
        "sincronizar-processos-diario": {
            "task": "workers.sincronizar_processos",
            "schedule": crontab(hour=3, minute=0),
        },
        "verificar-saude-uazapi": {
            "task": "workers.verificar_saude_uazapi",
            "schedule": 300.0,  # 5 minutos (CLAUDE.md §4.7)
        },
    },
)

celery_app.autodiscover_tasks(["app.workers"])
