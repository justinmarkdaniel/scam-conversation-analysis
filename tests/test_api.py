"""Endpoint tests: happy path, cache hit (no LLM re-run), and the LLM-degrade
branch. All offline via the FakeProvider — no network."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # isolate the SQLite file per test; force the deterministic provider
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.delenv("LLM_MODE", raising=False)
    import app.store as store
    import app.main as main
    importlib.reload(store)
    importlib.reload(main)
    main.store.init()
    return TestClient(main.app)


def _conv(text, cid="conv_1"):
    return {
        "conversation_id": cid,
        "channel": "sms",
        "participants": ["agent", "caller"],
        "messages": [{"sender": "caller", "text": text}],
    }


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_analyse_extracts_and_scores(client):
    r = client.post("/analyse", json=_conv("urgent! pay bsb 062-000 acct 12345678"))
    assert r.status_code == 200
    body = r.json()
    assert body["intel"]["bank_accounts"] == [
        {"scheme": "AU_BSB", "routing": "062-000", "account_number": "12345678"}
    ]
    assert body["scam_probability"] > 0        # 'urgent'/'bsb'/'account' signals
    assert body["cached"] is False


def test_second_identical_call_is_cached(client):
    payload = _conv("pay bsb 062-000")
    assert client.post("/analyse", json=payload).json()["cached"] is False
    assert client.post("/analyse", json=payload).json()["cached"] is True


def test_llm_failure_degrades_but_keeps_intel(client, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fail")
    import app.main as main
    main._provider = main.get_provider()  # rebuild provider in fail mode
    r = client.post("/analyse", json=_conv("pay bsb 062-000 acct 12345678"))
    assert r.status_code == 200
    body = r.json()
    assert body["llm_status"] == "unavailable"
    assert body["scam_probability"] is None
    assert body["intel"]["bank_accounts"][0]["routing"] == "062-000"


def test_response_carries_request_id_header(client):
    r = client.post("/analyse", json=_conv("pay bsb 062-000"))
    assert r.headers.get("X-Request-ID")  # correlates the caller's report to a log line


def test_benign_conversation_returns_empty_intel_and_low_score(client):
    r = client.post("/analyse", json=_conv("are we still on for lunch tomorrow?"))
    body = r.json()
    assert body["scam_probability"] < 0.5
    assert body["intel"] == {"bank_accounts": [], "emails": [], "phones": [], "payids": []}


def test_oversized_message_rejected_by_model(client):
    big = _conv("x" * 10_001)  # exceeds Message.text max_length -> Pydantic 422
    assert client.post("/analyse", json=big).status_code == 422
