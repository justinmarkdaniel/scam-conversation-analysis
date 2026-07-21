"""Orchestrator: hash -> cache -> extract -> score -> persist.

The content hash gates the expensive LLM call: the bot re-calls this on a
growing conversation every message, so an unchanged conversation returns the
stored verdict with no model call. A degraded (LLM-down) result is *not*
served from cache, so the score is retried on the next call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import List

from .extract import extract_intel
from .llm import Provider, ProviderError
from .models import AnalysisResult, Conversation, Intel
from . import store

log = logging.getLogger("fraud-analysis")


def _content_hash(conv: Conversation) -> str:
    # sender-attributed, ordered: the key covers who said what, not just text
    joined = "\n".join(f"{m.sender}\x1f{m.text}" for m in conv.messages)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def analyse(conv: Conversation, provider: Provider, request_id: str = "") -> AnalysisResult:
    request_id = request_id or uuid.uuid4().hex
    started = time.perf_counter()
    texts = [m.text for m in conv.messages]
    content_hash = _content_hash(conv)

    stored = store.get(conv.conversation_id)
    if stored and stored["content_hash"] == content_hash and stored["llm_status"] == "ok":
        _log(request_id, conv.conversation_id, True, started,
             stored["scam_probability"], "ok", None, None)
        return AnalysisResult(
            conversation_id=conv.conversation_id,
            scam_probability=stored["scam_probability"],
            intel=Intel(**stored["intel"]),
            cached=True,
            llm_status="ok",
        )

    intel = extract_intel(texts)

    probability = None
    llm_status = "ok"
    model = tokens = None
    try:
        result = provider.score("\n".join(texts))
        probability = round(result.probability, 3)
        model, tokens = result.model, result.tokens
    except ProviderError:
        llm_status = "unavailable"  # keep the extracted intel; retry score next call

    store.upsert(
        conversation_id=conv.conversation_id,
        content_hash=content_hash,
        scam_probability=probability,
        intel=intel,
        llm_status=llm_status,
        updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    _log(request_id, conv.conversation_id, False, started,
         probability, llm_status, model, tokens)
    return AnalysisResult(
        conversation_id=conv.conversation_id,
        scam_probability=probability,
        intel=Intel(**intel),
        cached=False,
        llm_status=llm_status,
    )


def _log(request_id, conversation_id, cache_hit, started, prob, llm_status, model, tokens):
    """One structured line per analysis. No message text — privacy by default."""
    log.info(json.dumps({
        "request_id": request_id,
        "conversation_id": conversation_id,
        "cache_hit": cache_hit,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "scam_probability": prob,
        "llm_status": llm_status,
        "model": model,
        "tokens": tokens,
    }))
