from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router

app = FastAPI(title="AdvogAI")
app.include_router(webhooks_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
