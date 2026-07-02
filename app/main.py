"""Production-ready FastAPI server with an Ollama proxy endpoint."""

import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app")

# Base URL of the Ollama sidecar; overridable via env (see docker-compose.yml).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
# Default model used when a request doesn't specify one.
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
# Upper bound on how long we'll wait for Ollama to generate a response.
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create one shared HTTP client for the app's lifetime."""
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=OLLAMA_TIMEOUT) as client:
        app.state.ollama = client
        logger.info("Ollama client ready (base_url=%s)", OLLAMA_URL)
        yield


app = FastAPI(
    title="LLM API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


class GenerateRequest(BaseModel):
    """A prompt to send to Ollama."""

    prompt: str = Field(..., min_length=1)
    model: str = DEFAULT_MODEL
    stream: bool = False


class GenerateResponse(BaseModel):
    """The model's completion."""

    model: str
    response: str
    # Wall-clock time for the whole proxied call, measured by this server.
    latency_ms: float
    # Ollama's own server-side generation time (excludes network/proxy overhead),
    # derived from its nanosecond `total_duration`. None if not reported.
    ollama_ms: float | None = None


@app.get("/")
async def hello() -> dict[str, str]:
    """Root endpoint."""
    return {"message": "Hello, World!"}


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness/readiness probe for orchestrators and load balancers."""
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Proxy a prompt to the Ollama sidecar and return the completion."""
    client: httpx.AsyncClient = app.state.ollama
    start = time.perf_counter()
    try:
        resp = await client.post(
            "/api/generate",
            json={"model": req.model, "prompt": req.prompt, "stream": False},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Surface Ollama's own error (e.g. model not pulled) as a 502.
        detail = exc.response.text or exc.response.reason_phrase
        logger.warning("Ollama returned %s: %s", exc.response.status_code, detail)
        raise HTTPException(status_code=502, detail=f"Ollama error: {detail}") from exc
    except httpx.HTTPError as exc:
        logger.error("Cannot reach Ollama at %s: %s", OLLAMA_URL, exc)
        raise HTTPException(status_code=503, detail="Ollama is unavailable") from exc

    latency_ms = (time.perf_counter() - start) * 1000
    data = resp.json()

    # Ollama reports total_duration in nanoseconds; convert to ms when present.
    total_ns = data.get("total_duration")
    ollama_ms = total_ns / 1e6 if isinstance(total_ns, (int, float)) else None

    logger.info(
        "generate model=%s latency_ms=%.1f ollama_ms=%s",
        req.model,
        latency_ms,
        f"{ollama_ms:.1f}" if ollama_ms is not None else "n/a",
    )

    return GenerateResponse(
        model=data.get("model", req.model),
        response=data.get("response", ""),
        latency_ms=round(latency_ms, 1),
        ollama_ms=round(ollama_ms, 1) if ollama_ms is not None else None,
    )
