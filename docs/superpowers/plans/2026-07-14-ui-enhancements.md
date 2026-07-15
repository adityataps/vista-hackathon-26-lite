# UI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend pre-check worker (auto-triage on ingest), token cost tracking, React Router tab routing, SLA-sorted exception queue with polling, and a resolution archive panel.

**Architecture:** A module-level `asyncio.Queue` in `main.py` drives a single long-running worker task that calls `intake_node` directly for lightweight triage; full investigation token counts are captured from `on_chat_model_end` events and written to DB; the frontend switches from local tab state to React Router routes and polls the exception queue every 5 seconds.

**Tech Stack:** FastAPI + asyncio, LangGraph/LangChain AWS Bedrock, psycopg2, React 18, react-router-dom v6, Recharts

## Global Constraints

- Python backend: `fastapi`, `psycopg2`, `langchain-aws`, `langgraph` — no new packages
- Frontend: add only `react-router-dom` — no other new npm packages
- All DB changes use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — idempotent, safe on restart
- Pricing constants: Input $0.003/1k tokens, Output $0.015/1k tokens (claude-sonnet-4-6, us-west-2 Bedrock)
- Exception status lifecycle: `pending → evaluating → pending → investigating → awaiting_approval → resolved | rejected`
- `reject` in `routers/resolutions.py` currently sets `status='escalated'` — fix to `'rejected'` in Task 2

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `backend/db.py` | Modify | Add 5 new columns via ALTER TABLE |
| `backend/agents/nodes/intake.py` | Modify | Return `usage_metadata` from ainvoke response |
| `backend/agents/state.py` | Modify | Make `investigation_id` Optional[int] |
| `backend/main.py` | Modify | Add precheck queue, worker, lifespan sweep, seed integration |
| `backend/routers/exceptions.py` | Modify | Status filter, SLA sort, new response fields, enqueue on ingest |
| `backend/routers/resolutions.py` | Modify | Fix reject to set `status='rejected'` not `'escalated'` |
| `backend/routers/metrics.py` | Modify | Rewrite token-costs to query real DB data |
| `frontend/package.json` | Modify | Add react-router-dom |
| `frontend/src/main.jsx` | Modify | Wrap with BrowserRouter |
| `frontend/src/App.jsx` | Modify | Replace tab state with useNavigate/useLocation/Routes |
| `frontend/src/api/client.js` | Modify | Add `status` param to getExceptions, add getResolvedExceptions |
| `frontend/src/mock/data.js` | Modify | Add resolvedExceptions mock + update tokenCostPerType format |
| `frontend/src/views/ExceptionQueue.jsx` | Modify | Polling, status pills, precheck sub-line, SLA badge, archive |
| `frontend/src/views/OperationsDashboard.jsx` | Modify | Dual-bar token cost chart |

---

## Task 1: DB Schema Migrations

**Files:**
- Modify: `backend/db.py`

**Interfaces:**
- Produces: `exceptions.precheck_summary JSONB`, `exceptions.precheck_input_tokens INT`, `exceptions.precheck_output_tokens INT`, `investigations.input_tokens INT`, `investigations.output_tokens INT`

- [ ] **Step 1: Add the 5 ALTER TABLE statements to `_ensure_schema`**

In `backend/db.py`, inside `_ensure_schema`, after the existing `CREATE INDEX` for exceptions, add:

```python
        # Pre-check columns on exceptions
        cur.execute("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_summary JSONB")
        cur.execute("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_input_tokens INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_output_tokens INTEGER DEFAULT 0")

        # Token tracking on investigations
        cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0")
```

The full tail of `_ensure_schema` (after the investigations CREATE TABLE block) should look like:

```python
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exceptions_msg_id ON exceptions(msg_id)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS investigations (
                id              SERIAL PRIMARY KEY,
                exception_id    INTEGER REFERENCES exceptions(id),
                msg_id          TEXT NOT NULL,
                steps           JSONB NOT NULL DEFAULT '[]',
                findings        JSONB,
                recommendation  JSONB,
                approval_status TEXT DEFAULT 'pending',
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                completed_at    TIMESTAMPTZ
            )
        """)

        # Pre-check columns on exceptions
        cur.execute("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_summary JSONB")
        cur.execute("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_input_tokens INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_output_tokens INTEGER DEFAULT 0")

        # Token tracking on investigations
        cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0")

    conn.commit()
```

- [ ] **Step 2: Verify columns are created**

Start the backend with `DATABASE_URL` set, then run:

```bash
psql $DATABASE_URL -c "\d exceptions"
psql $DATABASE_URL -c "\d investigations"
```

Expected: `precheck_summary`, `precheck_input_tokens`, `precheck_output_tokens` visible in exceptions; `input_tokens`, `output_tokens` visible in investigations.

- [ ] **Step 3: Commit**

```bash
git add backend/db.py
git commit -m "feat(db): add precheck and token tracking columns"
```

---

## Task 2: Exceptions API — Status Filter, SLA Sort, New Fields, Reject Fix

**Files:**
- Modify: `backend/routers/exceptions.py`
- Modify: `backend/routers/resolutions.py`

**Interfaces:**
- Produces: `GET /api/exceptions?status=active|resolved,rejected` — response rows gain `settlement_date`, `precheck_summary`, `resolved_at`, `recommendation_action`
- Produces: `POST /api/resolutions/{id}/reject` sets `exceptions.status = 'rejected'` (was `'escalated'`)

- [ ] **Step 1: Fix reject status in `routers/resolutions.py`**

In `backend/routers/resolutions.py`, in the `reject()` function, change:

```python
        cur.execute("UPDATE exceptions SET status='escalated' WHERE id=%s", (row[0],))
```

to:

```python
        cur.execute("UPDATE exceptions SET status='rejected' WHERE id=%s", (row[0],))
```

- [ ] **Step 2: Rewrite `list_exceptions` in `routers/exceptions.py`**

Replace the entire `list_exceptions` function with:

```python
from typing import Optional
from fastapi import Query  # add to existing imports at top of file

@router.get("/api/exceptions")
def list_exceptions(status: Optional[str] = Query(None)):
    conn = get_db()
    if not conn:
        return []

    if status == "active":
        where_clause = "WHERE e.status NOT IN ('resolved', 'rejected')"
    elif status is not None and "resolved" in status:
        where_clause = "WHERE e.status IN ('resolved', 'rejected')"
    else:
        where_clause = ""

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT e.id, e.msg_id, e.uetr, e.detected_errors, e.status, e.created_at,
                   e.precheck_summary,
                   p.id AS payment_db_id, p.amount, p.currency,
                   p.debtor_name, p.creditor_name, p.sender_bic, p.receiver_bic,
                   p.settlement_date,
                   i.completed_at   AS resolved_at,
                   i.recommendation->>'action' AS recommendation_action
            FROM exceptions e
            LEFT JOIN payments p ON p.msg_id = e.msg_id
            LEFT JOIN LATERAL (
                SELECT completed_at, recommendation
                FROM investigations
                WHERE exception_id = e.id
                ORDER BY created_at DESC
                LIMIT 1
            ) i ON true
            {where_clause}
            ORDER BY p.settlement_date ASC NULLS LAST, e.created_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()

    result = []
    for row in rows:
        (exc_id, msg_id, uetr, detected_errors, status_val, created_at,
         precheck_summary, payment_db_id, amount, currency,
         debtor_name, creditor_name, sender_bic, receiver_bic,
         settlement_date, resolved_at, recommendation_action) = row

        errors = detected_errors if isinstance(detected_errors, list) else []
        first_code = errors[0].get("code", "") if errors else ""
        display_type, type_key = ERROR_TYPE_MAP.get(first_code, ("Unknown", "gray"))

        tx_id = f"TX-{payment_db_id:05d}" if payment_db_id else msg_id

        result.append({
            "tx_id": tx_id,
            "msg_id": msg_id,
            "type": display_type,
            "type_key": type_key,
            "amount": _format_amount(amount, currency) if amount else "—",
            "sender": debtor_name or sender_bic or "—",
            "receiver": creditor_name or receiver_bic or "—",
            "status": status_val,
            "created_at": created_at.isoformat() if created_at else None,
            "settlement_date": settlement_date.isoformat() if settlement_date else None,
            "precheck_summary": precheck_summary,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
            "recommendation_action": recommendation_action,
        })
    return result
```

- [ ] **Step 3: Verify with curl**

With backend running:

```bash
# All exceptions (backward compat)
curl -s http://localhost:8000/api/exceptions | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d), 'rows, first:', d[0].keys() if d else 'empty')"

# Active only
curl -s "http://localhost:8000/api/exceptions?status=active" | python3 -c "import sys,json; d=json.load(sys.stdin); print([r['status'] for r in d])"

# Resolved/rejected
curl -s "http://localhost:8000/api/exceptions?status=resolved,rejected" | python3 -c "import sys,json; d=json.load(sys.stdin); print([r['status'] for r in d])"
```

Expected: active query returns only non-resolved rows; resolved query returns only resolved/rejected rows; all rows include `settlement_date`, `precheck_summary`, `resolved_at`, `recommendation_action` keys.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/exceptions.py backend/routers/resolutions.py
git commit -m "feat(api): exceptions status filter, SLA sort, new fields; fix reject status"
```

---

## Task 3: intake_node — Return usage_metadata

**Files:**
- Modify: `backend/agents/nodes/intake.py`
- Modify: `backend/agents/state.py`

**Interfaces:**
- Produces: `intake_node()` return dict gains key `"usage_metadata": {"input_tokens": int, "output_tokens": int, "total_tokens": int}`. LangGraph ignores unknown state keys, so the full graph is unaffected.
- Produces: `InvestigationState.investigation_id` is `Optional[int]` (needed for pre-check which passes `None`)

- [ ] **Step 1: Make investigation_id Optional in state.py**

In `backend/agents/state.py`, change:

```python
    investigation_id: int
```

to:

```python
    investigation_id: Optional[int]
```

Make sure `Optional` is imported — it already is.

- [ ] **Step 2: Add usage_metadata to intake_node return**

In `backend/agents/nodes/intake.py`, replace the `return` statement:

```python
    return {
        "intake_classification": {
            "error_categories": list(categories),
            "needs_technical": needs_technical,
            "needs_compliance": needs_compliance,
        },
        "steps": state.get("steps", []) + [step],
    }
```

with:

```python
    usage = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = {
            "input_tokens": response.usage_metadata.get("input_tokens", 0),
            "output_tokens": response.usage_metadata.get("output_tokens", 0),
            "total_tokens": response.usage_metadata.get("total_tokens", 0),
        }

    return {
        "intake_classification": {
            "error_categories": list(categories),
            "needs_technical": needs_technical,
            "needs_compliance": needs_compliance,
        },
        "steps": state.get("steps", []) + [step],
        "usage_metadata": usage,
    }
```

- [ ] **Step 3: Write a unit test**

Create `backend/tests/test_precheck.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.nodes.intake import intake_node


@pytest.mark.asyncio
async def test_intake_node_returns_usage_metadata():
    mock_response = MagicMock()
    mock_response.content = "Bad IBAN checksum detected. Routing to Technical Diagnosis."
    mock_response.usage_metadata = {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "payment": {"id": 1, "msg_id": "MSG001", "uetr": "abc", "amount": "1000",
                    "currency": "EUR", "sender_bic": "BICABC", "receiver_bic": "BICDEF",
                    "debtor_bic": None, "creditor_bic": None, "debtor_name": "Alice",
                    "debtor_iban": "DE89370400440532013000", "creditor_name": "Bob",
                    "creditor_iban": "GB29NWBK60161331926819"},
        "detected_errors": [{"code": "IBAN_INVALID_CHECKSUM", "field": "creditor_iban", "value": "bad"}],
        "swift_message": "", "intake_classification": {}, "investigation_context": {},
        "technical_findings": None, "compliance_findings": None,
        "recommendation": None, "steps": [], "investigation_id": None, "msg_id": "MSG001",
    }

    result = await intake_node(state, mock_llm)

    assert "usage_metadata" in result
    assert result["usage_metadata"]["input_tokens"] == 120
    assert result["usage_metadata"]["output_tokens"] == 45
    assert "intake_classification" in result
    assert result["intake_classification"]["needs_technical"] is True
```

- [ ] **Step 4: Run the test**

```bash
cd backend
pip install pytest pytest-asyncio
pytest tests/test_precheck.py -v
```

Expected: `PASSED tests/test_precheck.py::test_intake_node_returns_usage_metadata`

- [ ] **Step 5: Commit**

```bash
git add backend/agents/nodes/intake.py backend/agents/state.py backend/tests/test_precheck.py
git commit -m "feat(agents): intake_node returns usage_metadata; investigation_id Optional"
```

---

## Task 4: Pre-check Worker

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/routers/exceptions.py`

**Interfaces:**
- Consumes: `intake_node(state, llm)` from Task 3
- Consumes: DB columns from Task 1
- Produces: `precheck_queue` importable from `main` — used by `routers/exceptions.py` ingest endpoint
- Produces: exceptions auto-transition `pending → evaluating → pending` with `precheck_summary` populated

- [ ] **Step 1: Add precheck_queue and get_llm to main.py**

In `backend/main.py`, add `import asyncio` and `import json` to existing imports (json is already imported). Add after `_investigation_graph = None`:

```python
import asyncio

_precheck_queue: asyncio.Queue = asyncio.Queue()


def get_llm():
    get_graph()  # ensures _llm is initialised
    return _llm
```

- [ ] **Step 2: Add _run_precheck to main.py**

Add the following function after `get_llm()`:

```python
async def _run_precheck(tx_id: str) -> None:
    """Run intake-only triage for a single exception. Sets evaluating → pending."""
    conn = get_db()
    if not conn:
        logger.warning("Pre-check skipped %s — no DB connection", tx_id)
        return

    if tx_id.startswith("TX-"):
        try:
            id_val = int(tx_id[3:])
            id_clause = "p.id = %s"
        except ValueError:
            return
    else:
        id_val = tx_id
        id_clause = "e.msg_id = %s"

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT e.id, e.detected_errors, p.id AS pid,
                   p.msg_id, p.uetr, p.amount, p.currency,
                   p.sender_bic, p.receiver_bic,
                   p.debtor_bic, p.creditor_bic, p.debtor_name, p.debtor_iban,
                   p.creditor_name, p.creditor_iban, p.raw_xml
            FROM exceptions e
            LEFT JOIN payments p ON p.msg_id = e.msg_id
            WHERE {id_clause} AND e.status = 'pending'
        """, (id_val,))
        row = cur.fetchone()

    if not row:
        return  # already evaluated or not found

    (exc_id, detected_errors, pid, p_msg_id, uetr, amount, currency,
     sender_bic, receiver_bic, debtor_bic, creditor_bic,
     debtor_name, debtor_iban, creditor_name, creditor_iban, raw_xml) = row

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE exceptions SET status='evaluating', updated_at=NOW() WHERE id=%s", (exc_id,)
        )
    conn.commit()

    errors = detected_errors if isinstance(detected_errors, list) else []
    initial_state = {
        "payment": {
            "id": pid, "msg_id": p_msg_id, "uetr": uetr,
            "amount": str(amount) if amount else "0", "currency": currency or "",
            "sender_bic": sender_bic, "receiver_bic": receiver_bic,
            "debtor_bic": debtor_bic, "creditor_bic": creditor_bic,
            "debtor_name": debtor_name, "debtor_iban": debtor_iban,
            "creditor_name": creditor_name, "creditor_iban": creditor_iban,
        },
        "detected_errors": errors,
        "swift_message": raw_xml or "",
        "intake_classification": {},
        "investigation_context": {},
        "technical_findings": None,
        "compliance_findings": None,
        "recommendation": None,
        "steps": [],
        "investigation_id": None,
        "msg_id": p_msg_id or "",
    }

    from agents.nodes.intake import intake_node
    result = await intake_node(initial_state, get_llm())

    usage = result.get("usage_metadata", {})
    intake_cls = result.get("intake_classification", {})
    steps = result.get("steps", [])
    precheck_summary = {
        "needs_technical": intake_cls.get("needs_technical", False),
        "needs_compliance": intake_cls.get("needs_compliance", False),
        "action_hint": steps[0]["text"] if steps else "",
        "error_categories": intake_cls.get("error_categories", []),
    }

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE exceptions
            SET status='pending',
                precheck_summary=%s,
                precheck_input_tokens=%s,
                precheck_output_tokens=%s,
                updated_at=NOW()
            WHERE id=%s
        """, (
            json.dumps(precheck_summary),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            exc_id,
        ))
    conn.commit()
    logger.info("Pre-check done: %s → %s", tx_id, precheck_summary.get("action_hint", "")[:80])
```

- [ ] **Step 3: Add _precheck_worker to main.py**

Add after `_run_precheck`:

```python
async def _precheck_worker() -> None:
    """Drain the precheck queue indefinitely."""
    while True:
        tx_id = await _precheck_queue.get()
        try:
            await _run_precheck(tx_id)
        except Exception as exc:
            logger.error("Pre-check failed for %s: %s", tx_id, exc)
        finally:
            _precheck_queue.task_done()
```

- [ ] **Step 4: Update lifespan to sweep pending exceptions and start worker**

Replace the existing `lifespan` function in `main.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_graph()  # warms _llm and _investigation_graph

    conn = get_db()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT msg_id FROM exceptions WHERE status = 'pending'")
            pending = [row[0] for row in cur.fetchall()]
        for msg_id in pending:
            _precheck_queue.put_nowait(msg_id)
        if pending:
            logger.info("Enqueued %d pending exceptions for pre-check", len(pending))

    worker = asyncio.create_task(_precheck_worker())
    yield
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 5: Enqueue faulty exceptions in _seed_write_db**

In `backend/main.py`, inside `_seed_write_db`, after the `conn.commit()` inside the per-message loop, add enqueue for faulty messages. The faulty insertion block currently ends with:

```python
            if msg["is_faulty"]:
                detected = [...]
                cur.execute("""
                    INSERT INTO exceptions ...
                """, (...))
        conn.commit()
```

Change to:

```python
            if msg["is_faulty"]:
                detected = [
                    {"code": e["code"], "field": "", "value": str(e.get("detail", ""))[:200]}
                    for e in msg.get("errors", [])
                ]
                cur.execute("""
                    INSERT INTO exceptions (msg_id, uetr, detected_errors, payment_id, status)
                    VALUES (%s, %s, %s, %s, 'pending')
                    ON CONFLICT (msg_id) DO UPDATE SET
                        detected_errors = EXCLUDED.detected_errors,
                        updated_at = NOW()
                """, (msg["msg_id"], msg["uetr"], json.dumps(detected), payment_id))
        conn.commit()
        if msg["is_faulty"]:
            _precheck_queue.put_nowait(msg["msg_id"])
```

- [ ] **Step 6: Enqueue on ingest in routers/exceptions.py**

In `backend/routers/exceptions.py`, in the `ingest_exception` function, after `conn.commit()`:

```python
    conn.commit()
    logger.info("Exception created/updated: id=%s msg_id=%s", exception_id, req.msg_id)

    from main import _precheck_queue
    _precheck_queue.put_nowait(req.msg_id)

    return {"exception_id": exception_id}
```

- [ ] **Step 7: Smoke test — seed and watch status**

```bash
# Terminal 1: start backend
cd backend && uvicorn main:app --reload

# Terminal 2: seed some payments
curl -s -X POST http://localhost:8000/api/seed \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "error_rate": 0.6}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('faulty:', d['exceptions_written'])"

# Wait 3 seconds, then check statuses
sleep 3
curl -s "http://localhost:8000/api/exceptions" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(r['tx_id'], r['status'], r.get('precheck_summary', {}).get('action_hint','')[:60] if r.get('precheck_summary') else '') for r in d]"
```

Expected: exceptions show `evaluating` immediately after seed, then `pending` within a few seconds with `precheck_summary` populated.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/routers/exceptions.py
git commit -m "feat(backend): async pre-check worker — auto-triage on ingest and startup"
```

---

## Task 5: Token Tracking in Full Investigations

**Files:**
- Modify: `backend/routers/exceptions.py`

**Interfaces:**
- Consumes: DB columns `investigations.input_tokens`, `investigations.output_tokens` from Task 1
- Produces: `investigations` rows populated with real Bedrock token counts after each full investigation

- [ ] **Step 1: Add token accumulation in event_stream**

In `backend/routers/exceptions.py`, inside the `investigate` endpoint, find the `event_stream` async generator. Replace the current version with:

```python
    async def event_stream():
        accumulated_steps = []
        final_state = {}
        total_input_tokens = 0
        total_output_tokens = 0

        async for event in graph.astream_events(initial_state, version="v2"):
            sse = _normalize_lg_event(event)
            if sse:
                accumulated_steps.append({**sse, "ts": datetime.now(timezone.utc).isoformat()})
                yield f"data: {json.dumps(sse)}\n\n"

            if event.get("event") == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                if output is not None:
                    meta = getattr(output, "usage_metadata", None)
                    if isinstance(meta, dict):
                        total_input_tokens += meta.get("input_tokens", 0)
                        total_output_tokens += meta.get("output_tokens", 0)

            if event.get("event") == "on_chain_end" and event.get("name") == "LangGraph":
                final_state = event.get("data", {}).get("output", {})

        recommendation = final_state.get("recommendation") or {}
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investigations
                SET steps=%s, findings=%s, recommendation=%s,
                    approval_status='pending', completed_at=NOW(),
                    input_tokens=%s, output_tokens=%s
                WHERE id=%s
            """, (
                json.dumps(accumulated_steps),
                json.dumps({
                    "technical": final_state.get("technical_findings"),
                    "compliance": final_state.get("compliance_findings"),
                }),
                json.dumps(recommendation),
                total_input_tokens,
                total_output_tokens,
                inv_id,
            ))
            cur.execute("UPDATE exceptions SET status='awaiting_approval' WHERE id=%s", (exc_id,))
        conn.commit()

        done_event = {
            "type": "done",
            "report_id": report_id,
            "recommendation": {
                "action": recommendation.get("action", "Review required"),
                "rationale": recommendation.get("rationale", ""),
            },
        }
        yield f"data: {json.dumps(done_event)}\n\n"
```

- [ ] **Step 2: Verify tokens are written after investigation**

Run a full investigation via the UI or curl, then check the DB:

```bash
psql $DATABASE_URL -c "SELECT id, input_tokens, output_tokens FROM investigations ORDER BY id DESC LIMIT 3;"
```

Expected: `input_tokens` and `output_tokens` are non-zero after an investigation completes.

- [ ] **Step 3: Commit**

```bash
git add backend/routers/exceptions.py
git commit -m "feat(backend): capture Bedrock token usage in full investigations"
```

---

## Task 6: Metrics — Real Token Cost Data

**Files:**
- Modify: `backend/routers/metrics.py`

**Interfaces:**
- Consumes: `investigations.input_tokens`, `investigations.output_tokens`, `exceptions.precheck_input_tokens`, `exceptions.precheck_output_tokens` from Tasks 1 and 5
- Produces: `GET /api/metrics/token-costs` returns `[{type, precheck_avg_usd, investigation_avg_usd, precheck_avg_tokens, investigation_avg_tokens}]`

- [ ] **Step 1: Add pricing constants near top of metrics.py**

After `_LLM_COST_PER_CASE`, add:

```python
_INPUT_PRICE_PER_1K  = 0.003   # USD per 1k input tokens, claude-sonnet-4-6 us-west-2
_OUTPUT_PRICE_PER_1K = 0.015   # USD per 1k output tokens
```

- [ ] **Step 2: Replace the static get_token_costs function**

Replace `get_token_costs` entirely with:

```python
_STATIC_TOKEN_COSTS = [
    {"type": "Bad IBAN (checksum)",     "precheck_avg_usd": 0.004, "investigation_avg_usd": 0.09,
     "precheck_avg_tokens": 1200,       "investigation_avg_tokens": 5800},
    {"type": "Invalid BIC",             "precheck_avg_usd": 0.003, "investigation_avg_usd": 0.07,
     "precheck_avg_tokens": 1000,       "investigation_avg_tokens": 4500},
    {"type": "Duplicate UETR",          "precheck_avg_usd": 0.004, "investigation_avg_usd": 0.11,
     "precheck_avg_tokens": 1300,       "investigation_avg_tokens": 7000},
    {"type": "Sanctions name hit",      "precheck_avg_usd": 0.004, "investigation_avg_usd": 0.88,
     "precheck_avg_tokens": 1400,       "investigation_avg_tokens": 55000},
    {"type": "Missing mandatory field", "precheck_avg_usd": 0.003, "investigation_avg_usd": 0.14,
     "precheck_avg_tokens": 1100,       "investigation_avg_tokens": 8500},
    {"type": "FX limit breach",         "precheck_avg_usd": 0.004, "investigation_avg_usd": 0.31,
     "precheck_avg_tokens": 1200,       "investigation_avg_tokens": 19000},
]


@router.get("/api/metrics/token-costs")
def get_token_costs():
    conn = get_db()
    if not conn:
        return _STATIC_TOKEN_COSTS

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                (e.detected_errors->0->>'code')         AS first_code,
                AVG(e.precheck_input_tokens)             AS pre_in,
                AVG(e.precheck_output_tokens)            AS pre_out,
                AVG(i.input_tokens)                      AS inv_in,
                AVG(i.output_tokens)                     AS inv_out
            FROM exceptions e
            LEFT JOIN LATERAL (
                SELECT input_tokens, output_tokens
                FROM investigations
                WHERE exception_id = e.id AND input_tokens > 0
                ORDER BY created_at DESC LIMIT 1
            ) i ON true
            WHERE e.precheck_input_tokens > 0
            GROUP BY first_code
        """)
        rows = cur.fetchall()

    if not rows:
        return _STATIC_TOKEN_COSTS

    result = []
    for first_code, pre_in, pre_out, inv_in, inv_out in rows:
        display = _CODE_TO_DISPLAY.get(first_code or "", (first_code or "Unknown").replace("_", " ").title())
        pre_in = float(pre_in or 0)
        pre_out = float(pre_out or 0)
        inv_in = float(inv_in or 0)
        inv_out = float(inv_out or 0)
        result.append({
            "type": display,
            "precheck_avg_usd": round((pre_in * _INPUT_PRICE_PER_1K + pre_out * _OUTPUT_PRICE_PER_1K) / 1000, 4),
            "investigation_avg_usd": round((inv_in * _INPUT_PRICE_PER_1K + inv_out * _OUTPUT_PRICE_PER_1K) / 1000, 4),
            "precheck_avg_tokens": round(pre_in + pre_out),
            "investigation_avg_tokens": round(inv_in + inv_out),
        })
    return result
```

- [ ] **Step 3: Verify**

```bash
curl -s http://localhost:8000/api/metrics/token-costs | python3 -m json.tool
```

Expected: array with `precheck_avg_usd` and `investigation_avg_usd` keys on each item. Falls back to static if no data yet.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/metrics.py
git commit -m "feat(metrics): token-costs endpoint returns real DB averages with static fallback"
```

---

## Task 7: React Router

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/main.jsx`
- Modify: `frontend/src/App.jsx`

**Interfaces:**
- Produces: `/dashboard` renders `OperationsDashboard`, `/exceptions` renders `ExceptionQueue`; tab clicks push route; browser refresh restores correct tab

- [ ] **Step 1: Install react-router-dom**

```bash
cd frontend && npm install react-router-dom
```

Expected: `react-router-dom` appears in `package.json` dependencies.

- [ ] **Step 2: Wrap app in BrowserRouter in main.jsx**

Replace `frontend/src/main.jsx` entirely:

```jsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
```

- [ ] **Step 3: Rewrite App.jsx to use routes**

Replace `frontend/src/App.jsx` entirely:

```jsx
import { useEffect, useState } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import OperationsDashboard from './views/OperationsDashboard.jsx';
import ExceptionQueue from './views/ExceptionQueue.jsx';
import { probeBackend, getKpis, generateDemoPayments } from './api/client.js';

const TABS = [
  { id: 'dashboard', label: 'Operations Dashboard', path: '/dashboard' },
  { id: 'exceptions', label: 'Exception Queue',     path: '/exceptions' },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [backendLive, setBackendLive] = useState(null);
  const [openExceptions, setOpenExceptions] = useState(0);
  const [genState, setGenState] = useState('idle');

  function refreshBadge() {
    getKpis().then(({ data }) => setOpenExceptions(data.exceptions_open ?? 0));
  }

  useEffect(() => {
    probeBackend().then(setBackendLive);
    refreshBadge();
  }, []);

  async function generate() {
    if (genState === 'running') return;
    setGenState('running');
    await generateDemoPayments();
    refreshBadge();
    setGenState('done');
    setTimeout(() => setGenState('idle'), 3000);
  }

  const activeTab = TABS.find((t) => location.pathname.startsWith(t.path))?.id ?? 'dashboard';

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <div className="brand-logo">⚡</div>
          <div>
            PayInvestigator
            <small>AI Payment Exception Investigation · Global PAYplus layer</small>
          </div>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab ${activeTab === t.id ? 'active' : ''}`}
              onClick={() => navigate(t.path)}
            >
              {t.label}
              {t.id === 'exceptions' && openExceptions > 0 && (
                <span className="badge">{openExceptions}</span>
              )}
            </button>
          ))}
        </nav>
        <button
          className="btn primary"
          style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}
          onClick={generate}
          disabled={genState === 'running'}
        >
          {genState === 'idle' && '⚡ Generate Payments'}
          {genState === 'running' && <><span className="spinner" style={{ marginRight: 8 }} />Generating…</>}
          {genState === 'done' && '✓ Payments generated'}
        </button>
        <div className="conn" title="Backend connectivity">
          <span className={`dot ${backendLive ? 'live' : 'mock'}`} />
          {backendLive === null ? 'Connecting…' : backendLive ? 'Backend live' : 'Demo mode (mock data)'}
        </div>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate replace to="/dashboard" />} />
          <Route path="/dashboard" element={<OperationsDashboard />} />
          <Route path="/exceptions" element={<ExceptionQueue />} />
        </Routes>
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Verify routing**

```bash
cd frontend && npm run dev
```

Open http://localhost:5173 — should redirect to /dashboard. Click "Exception Queue" — URL changes to /exceptions. Hit browser refresh — stays on /exceptions.

- [ ] **Step 5: Commit**

```bash
cd frontend
git add package.json package-lock.json src/main.jsx src/App.jsx
git commit -m "feat(frontend): React Router — tab clicks change URL, refresh restores tab"
```

---

## Task 8: Exception Queue — Polling, Status Pills, Pre-check Sub-line, SLA Badge

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/views/ExceptionQueue.jsx`

**Interfaces:**
- Consumes: `GET /api/exceptions?status=active` — `settlement_date`, `precheck_summary` fields from Task 2
- Produces: queue polls every 5s; evaluating/investigating/awaiting_approval/rejected pills rendered; precheck sub-line shown; SLA badge shown within 24h

- [ ] **Step 1: Add status param support to getExceptions in client.js**

In `frontend/src/api/client.js`, replace:

```js
export const getExceptions = () => getJson('/api/exceptions', mock.exceptionQueue);
```

with:

```js
export const getExceptions = (status = 'active') =>
  getJson(`/api/exceptions?status=${status}`, mock.exceptionQueue);
```

- [ ] **Step 2: Update mock/data.js exceptionQueue format**

In `frontend/src/mock/data.js`, find the `exceptionQueue` export. Add `settlement_date` and `precheck_summary` fields to each mock row. Example (keep existing rows, just add these two fields):

```js
export const exceptionQueue = [
  {
    tx_id: 'TX-00142', type: 'Bad IBAN', type_key: 'iban', amount: '€142,500',
    sender: 'Müller GmbH', receiver: 'BNPAFRPP', status: 'pending',
    settlement_date: null,
    precheck_summary: { action_hint: 'IBAN checksum mismatch on creditor account. Route to Technical Diagnosis.', needs_technical: true, needs_compliance: false },
  },
  // ... keep other mock rows, add settlement_date: null, precheck_summary: null to each
];
```

- [ ] **Step 3: Rewrite ExceptionQueue.jsx**

Replace `frontend/src/views/ExceptionQueue.jsx` entirely:

```jsx
import { useEffect, useRef, useState } from 'react';
import { getExceptions, streamInvestigation, submitDecision, sendChat } from '../api/client.js';

const TYPE_PILL = {
  iban: 'blue', sanctions: 'red', iso: 'blue', fx: 'yellow', duplicate: 'gray',
};

const STATUS_PILL = {
  pending:           { cls: 'yellow',  label: 'Pending' },
  evaluating:        { cls: 'yellow',  label: 'Evaluating…', spinner: true },
  investigating:     { cls: 'blue',    label: 'Investigating', spinner: true },
  awaiting_approval: { cls: 'orange',  label: 'Awaiting Approval' },
  resolved:          { cls: 'green',   label: 'Resolved' },
  rejected:          { cls: 'gray',    label: 'Rejected' },
};

const SUGGESTIONS = {
  iban:      ['Why did you flag this IBAN specifically?', 'What is the corrected IBAN?', 'Are there other payments to this receiver this week?'],
  sanctions: ['Why did you recommend holding this payment?', 'Show me the full SDN entry for the match', 'Does the sender have prior compliance flags?'],
  duplicate: ['Which payment is the duplicate?', 'Should I cancel the second one or the first?', 'Was the UETR reused or is this a business duplicate?'],
  fx:        ['What FX limit was breached?', 'What is the approved limit for this corridor?', 'Has this sender exceeded limits before?'],
  iso:       ['Which mandatory field is missing?', 'Can the field be derived from other payment data?', 'What happens if I approve the repair?'],
  default:   ['Why this recommendation?', 'Show related payments from the same sender', 'What is the risk if I approve this?'],
};

function slaWarning(settlementDate) {
  if (!settlementDate) return false;
  const hoursUntil = (new Date(settlementDate) - Date.now()) / 3_600_000;
  return hoursUntil >= 0 && hoursUntil <= 24;
}

export default function ExceptionQueue() {
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(null);
  const [lines, setLines] = useState([]);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);
  const [decision, setDecision] = useState(null);
  const [chat, setChat] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [archive, setArchive] = useState([]);
  const cancelRef = useRef(null);
  const streamRef = useRef(null);
  const chatEndRef = useRef(null);

  function fetchQueue() {
    getExceptions('active').then(({ data }) => setQueue(data));
  }

  useEffect(() => {
    fetchQueue();
    const id = setInterval(fetchQueue, 5000);
    return () => { clearInterval(id); cancelRef.current?.(); };
  }, []);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' });
  }, [lines]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat]);

  function investigate(row) {
    cancelRef.current?.();
    setSelected(row);
    setLines([]);
    setReport(null);
    setDecision(null);
    setChat([]);
    setRunning(true);
    cancelRef.current = streamInvestigation(
      row.tx_id,
      (evt) => setLines((prev) => [...prev, evt]),
      (final) => { setRunning(false); setReport(final); }
    );
  }

  async function decide(kind) {
    if (!report) return;
    setDecision(kind);
    await submitDecision(report.report_id, kind);
    fetchQueue();
  }

  async function ask(text) {
    const message = (text ?? chatInput).trim();
    if (!message || chatBusy || !report) return;
    setChatInput('');
    setChat((c) => [...c, { role: 'user', text: message }]);
    setChatBusy(true);
    const res = await sendChat(report.report_id, selected.tx_id, message);
    setChat((c) => [...c, { role: 'bot', text: res.answer, tool: res.tool }]);
    setChatBusy(false);
  }

  async function loadArchive() {
    const { data } = await getExceptions('resolved,rejected');
    setArchive(data);
    setShowArchive(true);
  }

  const suggestions = selected ? (SUGGESTIONS[selected.type_key] ?? SUGGESTIONS.default) : [];
  const pendingCount = queue.filter((r) => r.status === 'pending' || r.status === 'awaiting_approval').length;

  return (
    <>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 12px' }}>
          <h2 style={{ fontSize: 15 }}>Exception Queue</h2>
          <span className="pill gray">{pendingCount} pending</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>TX ID</th><th>Type</th><th>Amount</th>
              <th>Sender → Receiver</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((row) => {
              const pill = STATUS_PILL[row.status] ?? STATUS_PILL.pending;
              return (
                <tr
                  key={row.tx_id}
                  className={`clickable ${selected?.tx_id === row.tx_id ? 'selected' : ''}`}
                  onClick={() => investigate(row)}
                >
                  <td className="num">
                    {row.tx_id}
                    {slaWarning(row.settlement_date) && (
                      <span className="pill orange" style={{ marginLeft: 6, fontSize: 10 }}>⚠ SLA</span>
                    )}
                  </td>
                  <td>
                    <span className={`pill ${TYPE_PILL[row.type_key] ?? 'gray'}`}>{row.type}</span>
                    {row.precheck_summary?.action_hint && (
                      <div style={{ fontSize: 11, color: '#8fa1c0', marginTop: 2 }}>
                        {row.precheck_summary.action_hint.slice(0, 80)}
                      </div>
                    )}
                  </td>
                  <td className="num">{row.amount}</td>
                  <td style={{ color: '#8fa1c0' }}>{row.sender} → {row.receiver}</td>
                  <td>
                    <span className={`pill ${pill.cls}`}>
                      {pill.spinner && <span className="spinner" style={{ marginRight: 4 }} />}
                      {pill.label}
                    </span>
                  </td>
                </tr>
              );
            })}
            {queue.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: 'center', color: '#8fa1c0', padding: 20 }}>No active exceptions</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>
            Agent Investigation — {selected.tx_id}
            {running && <span className="spinner" style={{ marginLeft: 8 }} />}
          </h3>
          <div className="stream" ref={streamRef}>
            {lines.map((l, i) => (
              <div className="stream-line" key={i}>
                <span className={`agent ${l.cls}`}>{l.agent === 'tool' ? '' : `${l.agent}:`}</span>
                <span className="txt">{l.text}</span>
              </div>
            ))}
            {running && <span className="cursor" />}
          </div>

          {report?.recommendation && (
            <div className={`hitl ${decision === 'approve' ? 'approved' : decision === 'reject' ? 'rejected' : ''}`}>
              <div className="msg">
                {decision === null && <>
                  <strong>⏳ Awaiting human approval</strong>
                  <div style={{ marginTop: 6 }}>{report.recommendation.action}</div>
                  <div className="footnote">{report.recommendation.rationale}</div>
                </>}
                {decision === 'approve' && <>
                  <strong style={{ color: '#34d399' }}>✓ Approved &amp; executed</strong>
                  <div className="footnote">execute_resolution() called with approval token · full trail written to audit log</div>
                </>}
                {decision === 'reject' && <>
                  <strong style={{ color: '#f87171' }}>✖ Rejected</strong>
                  <div className="footnote">Recommendation rejected — case returned to manual queue · decision logged to audit trail</div>
                </>}
              </div>
              {decision === null && (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn approve" onClick={() => decide('approve')}>Approve</button>
                  <button className="btn reject" onClick={() => decide('reject')}>Reject</button>
                </div>
              )}
            </div>
          )}

          {report && (
            <div style={{ marginTop: 18 }}>
              <h3>Ask a question about this investigation</h3>
              <div className="chat">
                {chat.length > 0 && (
                  <div className="chat-history">
                    {chat.map((m, i) => (
                      <div className={`msg-row ${m.role}`} key={i}>
                        <div className="msg-bubble">
                          {m.tool && <span className="tool-note">🔧 [calls {m.tool}]</span>}
                          {m.role === 'bot' ? '🤖 ' : ''}{m.text}
                        </div>
                      </div>
                    ))}
                    {chatBusy && (
                      <div className="msg-row bot">
                        <div className="msg-bubble"><span className="spinner" /> thinking…</div>
                      </div>
                    )}
                    <div ref={chatEndRef} />
                  </div>
                )}
                <div className="suggestions">
                  {suggestions.map((s) => (
                    <button key={s} className="suggestion" onClick={() => ask(s)}>{s}</button>
                  ))}
                </div>
                <div className="chat-input">
                  <input
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && ask()}
                    placeholder="e.g. Why did you flag this IBAN specifically?"
                  />
                  <button className="btn primary" onClick={() => ask()} disabled={chatBusy}>Send</button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title" style={{ margin: '0 0 8px' }}>
          <h2 style={{ fontSize: 15 }}>Resolved Cases</h2>
          <button
            className="btn"
            style={{ fontSize: 12 }}
            onClick={showArchive ? () => setShowArchive(false) : loadArchive}
          >
            {showArchive ? 'Hide archive' : `Show resolved (${archive.length || '…'})`}
          </button>
        </div>
        {showArchive && (
          <table>
            <thead>
              <tr>
                <th>TX ID</th><th>Type</th><th>Amount</th>
                <th>Decision</th><th>Agent Recommendation</th><th>Resolved At</th>
              </tr>
            </thead>
            <tbody>
              {archive.map((row) => (
                <tr key={row.tx_id}>
                  <td className="num">{row.tx_id}</td>
                  <td><span className={`pill ${TYPE_PILL[row.type_key] ?? 'gray'}`}>{row.type}</span></td>
                  <td className="num">{row.amount}</td>
                  <td>
                    <span className={`pill ${row.status === 'resolved' ? 'green' : 'gray'}`}>
                      {row.status === 'resolved' ? 'Approved' : 'Rejected'}
                    </span>
                  </td>
                  <td style={{ color: '#8fa1c0', fontSize: 12 }}>
                    {row.recommendation_action ? row.recommendation_action.slice(0, 80) : '—'}
                  </td>
                  <td style={{ color: '#8fa1c0', fontSize: 12 }}>
                    {row.resolved_at ? new Date(row.resolved_at).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
              {archive.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: '#8fa1c0', padding: 16 }}>No resolved cases yet</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Verify in browser**

```bash
cd frontend && npm run dev
```

- Navigate to /exceptions
- Seed some payments: click "⚡ Generate Payments"
- Confirm queue updates every 5 seconds (rows appear/disappear from active list)
- Confirm evaluating spinner pill appears on new rows
- Confirm SLA badge appears for rows with settlement_date within 24h (may not show without test data)
- Click "Show resolved" — archive table appears with resolved cases

- [ ] **Step 5: Commit**

```bash
cd frontend
git add src/api/client.js src/mock/data.js src/views/ExceptionQueue.jsx
git commit -m "feat(frontend): exception queue polling, status pills, precheck sub-line, SLA badge, archive"
```

---

## Task 9: Dashboard — Dual-Bar Token Cost Chart

**Files:**
- Modify: `frontend/src/mock/data.js`
- Modify: `frontend/src/views/OperationsDashboard.jsx`

**Interfaces:**
- Consumes: `GET /api/metrics/token-costs` returns `{type, precheck_avg_usd, investigation_avg_usd}` from Task 6
- Produces: bar chart shows two bars per exception type (precheck vs full investigation cost in USD)

- [ ] **Step 1: Update tokenCostPerType mock in data.js**

In `frontend/src/mock/data.js`, replace the `tokenCostPerType` export with:

```js
export const tokenCostPerType = [
  { type: 'Bad IBAN (checksum)',     precheck_avg_usd: 0.004, investigation_avg_usd: 0.09 },
  { type: 'Invalid BIC',             precheck_avg_usd: 0.003, investigation_avg_usd: 0.07 },
  { type: 'Duplicate UETR',          precheck_avg_usd: 0.004, investigation_avg_usd: 0.11 },
  { type: 'Sanctions name hit',      precheck_avg_usd: 0.004, investigation_avg_usd: 0.88 },
  { type: 'Missing mandatory field', precheck_avg_usd: 0.003, investigation_avg_usd: 0.14 },
  { type: 'FX limit breach',         precheck_avg_usd: 0.004, investigation_avg_usd: 0.31 },
];
```

- [ ] **Step 2: Update the token cost chart in OperationsDashboard.jsx**

In `frontend/src/views/OperationsDashboard.jsx`, find the section that renders the token-cost bar chart. It currently uses a single `Bar dataKey="avg_token_cost_usd"`. Replace that `<div className="card">` block with:

```jsx
        <div className="card">
          <h3>AI Cost per Exception Type (USD)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={tokenCosts} layout="vertical" margin={{ left: 160 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis type="number" stroke="#8fa1c0" fontSize={11} tickFormatter={(v) => `$${v.toFixed(3)}`} />
              <YAxis type="category" dataKey="type" stroke="#8fa1c0" fontSize={11} width={160} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name) => [`$${value.toFixed(4)}`, name]}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="precheck_avg_usd"      name="Pre-check"         fill="#3d5578" radius={[0,3,3,0]} />
              <Bar dataKey="investigation_avg_usd" name="Full investigation" fill="#4f8ef7" radius={[0,3,3,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
```

- [ ] **Step 3: Verify in browser**

Open /dashboard. The "AI Cost per Exception Type" chart should show horizontal bars (layout="vertical") with two bars per exception type — a light one for pre-check and a darker one for full investigation.

- [ ] **Step 4: Commit**

```bash
cd frontend
git add src/mock/data.js src/views/OperationsDashboard.jsx
git commit -m "feat(frontend): token cost chart shows precheck vs full investigation cost side-by-side"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Exception queue polls DB — Task 8 (setInterval 5s)
- ✅ Pre-check auto-triggers on new failures — Tasks 3+4 (worker + ingest enqueue)
- ✅ Pre-check `evaluating` status — Task 4 (`_run_precheck` sets evaluating)
- ✅ Back to `pending` after pre-check — Task 4 (sets pending + precheck_summary)
- ✅ Token cost tracking vs comparison — Tasks 5+6+9
- ✅ React Router on tabs — Task 7
- ✅ SLA sort (sooner first) — Task 2 (ORDER BY settlement_date ASC NULLS LAST)
- ✅ Resolution archive — Tasks 2 (backend fields) + 8 (frontend archive section)
- ✅ Startup sweep of pending exceptions — Task 4 (lifespan)
- ✅ Seed flow enqueues new exceptions — Task 4 (_seed_write_db + ingest endpoint)
