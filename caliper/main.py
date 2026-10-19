import os

import uvicorn
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Caliper", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "caliper", "version": "0.1.0"}

    return app


app = create_app()


def run() -> None:
    host = os.environ.get("CALIPER_HOST", "127.0.0.1")
    port = int(os.environ.get("CALIPER_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
