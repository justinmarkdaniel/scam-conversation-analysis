"""API surface: POST /analyse and GET /health."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from .analyse import analyse
from .llm import get_provider
from .models import AnalysisResult, Conversation
from . import store

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    yield


app = FastAPI(title="Fraud Prevention API", lifespan=lifespan)
_provider = get_provider()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyse", response_model=AnalysisResult)
def analyse_endpoint(conv: Conversation, response: Response) -> AnalysisResult:
    # Size caps are declared on the model (Message.text / messages length) and
    # rejected as 422 by Pydantic before we get here — no manual guard needed.
    request_id = uuid.uuid4().hex
    response.headers["X-Request-ID"] = request_id  # correlate the response to its log line
    return analyse(conv, _provider, request_id=request_id)
