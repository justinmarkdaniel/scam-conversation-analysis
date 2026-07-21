# Fraud Prevention — Take-Home Challenge

> A small, time-boxed take-home coding challenge (fraud-prevention domain).

An API-only fraud-prevention service: given a conversation, it estimates how
likely the conversation is fraudulent (a `scam_probability`, 0–1) and surfaces
the risk indicators it contains — account details, emails, phone numbers, and
PayIDs — behind a validation/normalisation layer, so a platform can flag and
stop fraud early. It persists each analysis and is shaped for production:
called repeatedly on a growing conversation, at volume.

## Run

```bash
docker compose up          # serves on http://localhost:8000
```

Defaults to a deterministic offline provider (no key needed). To use a real
LLM, copy `.env.example` to `.env` and set `LLM_PROVIDER=real` + `LLM_API_KEY`
+ `LLM_MODEL` in litellm `provider/model` form (e.g. `gemini/gemini-2.5-flash`,
`openai/gpt-4o-mini`, `anthropic/claude-…`). Swapping provider is a config
change, not a code change.

```bash
curl -X POST localhost:8000/analyse -H 'content-type: application/json' -d '{
  "conversation_id": "conv_1",
  "channel": "sms",
  "participants": ["agent", "caller"],
  "messages": [
    {"sender": "caller", "text": "URGENT verify your account. Pay bsb 062-000 acct 12345678"},
    {"sender": "caller", "text": "or my PayID john [at] secure-verify [dot] co, call 0412 345 678"}
  ]
}'
```

```jsonc
{
  "conversation_id": "conv_1",
  "scam_probability": 1.0,
  "intel": {
    "bank_accounts": [{"scheme": "AU_BSB", "routing": "062-000", "account_number": "12345678"}],
    "emails": ["john@secure-verify.co"],
    "phones": ["+61412345678"],
    "payids": [{"kind": "email", "value": "john@secure-verify.co"}]
  },
  "cached": false,
  "llm_status": "ok"
}
```

`GET /health` returns `{"status": "ok"}`.

## Tests

```bash
pip install -r requirements.txt
pytest                     # unit + API — deterministic, offline, fast
pytest -m eval evals/      # model eval, on demand (see below)
```

## How it's built

The whole thing is one design decision: **split along a trust boundary.**

- **Deterministic extraction (`app/extract.py`)** owns the identifier extraction.
  Identifiers have *specified* formats (BSB `NNN-NNN`, sort code `NN-NN-NN`, an
  account is digits, a PayID is an email/phone/ABN), so regexes propose
  candidates and `normalise_*` validators decide — nothing becomes intel until
  it passes one. Pure, no I/O, cheap enough to re-run every message, and it's
  where the tests concentrate. It can't be steered by prompt injection.
  Validation uses the standard library for each domain rather than hand-rolled
  logic — **`phonenumbers`** for phone parsing/E.164, **`python-stdnum`** for the
  ABN checksum, **`email-validator`** for emails — and only the trivial BSB /
  sort-code formats are plain regex. The obfuscation layer (`[at]`/`(dot)`) is
  the one genuinely custom bit, since no library covers it.
- **The LLM (`app/llm.py`)** owns only the fuzzy judgment — the scam
  probability — behind a `Provider` interface (Adapter pattern) with a real
  adapter (via **litellm**) and a deterministic `FakeProvider`. The real call is
  `temperature=0` with structured output constrained to a Pydantic schema;
  litellm translates that to each provider's native mechanism (OpenAI
  `json_schema`, Gemini `responseSchema`, …) and handles a retry. The result is
  parsed defensively and clamped to `[0,1]`, and provider/model is config, not
  code.
- **Avoiding re-done work (`app/analyse.py` + `app/store.py`)**: the
  conversation is hashed and the verdict persisted per `conversation_id` in
  SQLite. An unchanged conversation returns the stored result with **no LLM
  call** (`cached: true`). Extraction is free, so the hash gates the one
  expensive thing.
- **Observability**: one structured log line per analysis (request id, cache
  hit, latency, score, model, tokens, status) — and deliberately **no message
  text**, since the payload is a sensitive conversation.

### Testing strategy — three tiers

Different guarantees, different tools:

- **Unit tests** (`tests/test_extract.py`) — the core. Each identifier type:
  clean / messy / obfuscated / **negative**, plus dedup and the ABN modulus-89
  checksum (a valid ABN passes, a one-digit mutation is rejected).
- **API tests** (`tests/test_api.py`) — happy path, the cache hit, and the
  LLM-degrade branch (via `LLM_MODE=fail`), all offline.
- **Evals** (`evals/`) — you can't unit-assert an LLM's float, so a small
  labelled set measures the scorer *directionally* (accuracy / FP / FN). It's
  marked `@pytest.mark.eval` and **excluded from the default run** — evals gate
  model/prompt changes, not code merges. Against `gemini-2.5-flash` (via
  litellm): **12/12 (0 FP, 0 FN)** — a small, deliberately clear-cut set, so
  this validates the harness and directional behaviour, not model quality at
  scale. The default offline run uses the deterministic FakeProvider;
  `LLM_PROVIDER=real` runs it against a live model.

## Trade-offs

- **Deterministic extraction over LLM extraction** — reliability, testability,
  and cost. The LLM is reserved for the one thing regex can't do (judgment).
- **BSB↔account pairing is by proximity within a message.** Simple and right
  for the common case; cross-message pairing is out of scope (below).
- **LLM outage degrades, it doesn't fail.** If the model is unavailable the
  request still returns `200` with the extracted indicators and
  `scam_probability: null` / `llm_status: "unavailable"` — losing the extracted
  fraud indicators because the model hiccupped is the wrong failure, and the
  score is retried on the next message (a degraded result is never cache-served).
  Some would prefer a `502`; this is a conscious product choice.
- **SQLite** — satisfies "persists" with zero setup and runs in the container;
  Redis is the swap at real QPS.
- **Bounded deobfuscation** — handles `[at]` / `(dot)` / spaced forms, not an
  arms race.
- **Metadata accepted but not yet used (out of scope).** The request mirrors
  the documented input schema; four fields are validated and carried but don't
  feed the logic yet. They're `Optional`, so the caller isn't burdened, and
  keeping that shape beats narrowing it to only what today's code reads:

  | Field | Status |
  |---|---|
  | `sender` | used in the cache key; not yet fed to extraction or the model — would let the model see speaker turns and scope extraction to the flagged party's messages |
  | `timestamp` | accepted, unused — would enable incremental extraction over only the new messages |
  | `channel` | accepted, unused — a natural metric label (sms vs email fraud) |
  | `participants` | accepted, unused |

## Security

The conversation is **untrusted, attacker-controlled input** that flows
into the scoring prompt. Mitigations in place: extraction is deterministic (can't
be redirected by injected instructions), the prompt marks the conversation as
data not instructions, oversized inputs are rejected before processing, SQL is
fully parameterised, and message text is never logged. Next hardening steps: a
moderation pass (Llama-Guard-style) on the way into the model, and PII
redaction before the model call (see Out of scope).

## Out of scope (assumed upstream)

Deliberately not built — naming them so the boundary is explicit, not accidental:

- **Auth / tenancy.** The store is keyed by the caller-supplied `conversation_id`;
  authn/authz and tenant isolation are assumed at the gateway in front of this
  internal service. A half-built auth layer would be worse than none.
- **Metrics.** Observability here is structured logs carrying the dimensions
  (latency, tokens, cache hit, `llm_status`). The swap to true metrics is a
  Prometheus counter on `llm_status` + a latency histogram — I didn't pull in a
  metrics dependency at this scope. Traces with Arize Phoenix were also considered
  but left out as out of scope.
- **Body-size limit.** The size caps (declared on the model, rejected as `422`)
  bound extraction/LLM *compute*; a true memory bound (rejecting a huge body
  before it's parsed) belongs at the proxy/ASGI layer, not application code.
- **PII detection / anonymisation before the model call.** The fraud judgment
  needs the *presence and shape* of identifiers, not their literal values —
  and extraction already located every one deterministically, so redacting
  them to typed placeholders (`[BANK_ACCOUNT]`, `[EMAIL]`, `[PHONE]`,
  `[PAYID]`) before `provider.score()` is a near-free data-minimisation step
  to the third-party model. Left out here for the signal trade-off (some
  content — e.g. lookalike domains — is itself a fraud signal) and because it's
  the LLM leg only: the indicators are stored and returned regardless. A clear
  future win, not an anti-injection control.

## Known issues / cleanup for later

Minor, low-impact, and understood — noted so they're deliberate, not blind spots:

- **Phone matching runs both AU and GB regions**, so an international `+61`/`+44`
  number is found twice and collapsed by dedup. Harmless, but a single-pass
  matcher with region inference would be tidier.
- **Email local-part is lowercased** (`John@x.com` → `john@x.com`). Technically
  lossy — the local part is case-sensitive per RFC — but intentional, so the
  same address dedups regardless of casing.
- **Phone region precedence is AU-then-GB**: a number valid in both numbering
  plans would be labelled AU. Negligible for real AU/GB numbers; a
  metadata-driven region hint (`channel`/locale) would remove the ambiguity.

## What I'd do with more time

- LLM-assisted extraction for messy shapes, feeding the **same** validators
  (regex misses → model proposes → validator still decides).
- Incremental extraction over only the new messages; cross-message pairing.
- Redis + request coalescing at real QPS; richer per-scheme confidence.
- A larger, adversarial eval set with tracked precision/recall per fraud type.
