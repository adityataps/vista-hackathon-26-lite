# UI Enhancements — Design Spec

**Date:** 2026-07-14  
**Scope:** Exception queue polling, backend pre-check worker, token cost tracking, React Router, SLA sorting

---

## 1. Backend — Pre-check Worker

### Overview
A module-level `asyncio.Queue` in `main.py` feeds a single long-running `asyncio.Task` started in the FastAPI `lifespan` context manager. The worker runs the **intake node only** (not the full LangGraph pipeline) to produce a lightweight triage classification for each new exception.

### Status transitions
```
pending → evaluating  (worker picks up the task)
        → pending     (pre-check complete, summary stored, awaiting human)
```
The human then clicks the row to launch a full investigation, which continues:
```
pending → investigating → awaiting_approval → resolved | rejected
```

### Startup behaviour
On FastAPI startup, the lifespan handler queries `SELECT id, msg_id FROM exceptions WHERE status = 'pending'` and enqueues all results. This ensures pre-checks run for all existing unprocessed exceptions even without seeding activity.

### New exception ingestion
`POST /api/ingest/exceptions` enqueues the new exception's `tx_id` immediately after the DB write, before returning.

### Pre-check execution
The worker calls a new async function `run_precheck(tx_id)` that:
1. Loads the exception + payment row from DB.
2. Builds the same `initial_state` dict used by the full investigation.
3. Calls `await intake_node(initial_state, llm)` directly (imported from `agents.nodes.intake`) — no full graph traversal.
4. `intake_node` is modified to also return `usage_metadata` (`input_tokens`, `output_tokens`) in its result dict, extracted from `response.usage_metadata` on the `AIMessage` returned by `llm.ainvoke()`.
5. Writes `precheck_summary` (JSONB: `{classification, needs_technical, needs_compliance, action_hint}`), `precheck_input_tokens`, and `precheck_output_tokens` to the `exceptions` row.
6. Sets `status = 'pending'` (was `'evaluating'` during steps 3–5).

If the worker task raises, it logs the error and continues to the next queue item — no retry.

### DB schema changes (exceptions table)
```sql
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_summary       JSONB;
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_input_tokens  INTEGER DEFAULT 0;
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_output_tokens INTEGER DEFAULT 0;
```

---

## 2. Backend — Token Tracking for Full Investigations

### Capture
During the SSE investigation stream, `astream_events` emits `on_chat_model_end` events. Each carries `event["data"]["output"].usage_metadata` with `input_tokens` and `output_tokens` integers. Accumulate across all nodes into two running counters; write totals to `investigations` at the `on_chain_end` / completion step.

### DB schema changes (investigations table)
```sql
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS input_tokens  INTEGER DEFAULT 0;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;
```

### Pricing constants (us-west-2 Bedrock, claude-sonnet-4-6)
- Input:  $0.003 / 1k tokens  
- Output: $0.015 / 1k tokens

### Updated `/api/metrics/token-costs`
Returns real averages from DB instead of static values:
```json
[
  {
    "type": "Bad IBAN (checksum)",
    "precheck_avg_usd": 0.004,
    "investigation_avg_usd": 0.09,
    "precheck_avg_tokens": 1200,
    "investigation_avg_tokens": 5800
  }
]
```
Query joins `exceptions` (pre-check tokens) with `investigations` (full investigation tokens), grouped by `detected_errors[0].code` mapped to display type.

Falls back to static estimates if no completed investigations exist yet.

---

## 3. Backend — SLA Sorting

`GET /api/exceptions` query changes:
- `ORDER BY` becomes `p.settlement_date ASC NULLS LAST, e.created_at DESC`
- Response adds `settlement_date` (ISO date string, nullable) and `precheck_summary` (nullable JSONB object) to each row.

No new endpoint needed.

---

## 4. Frontend — React Router

### Install
```
npm install react-router-dom
```

### Route map
| Path | Component |
|---|---|
| `/` | Redirect → `/dashboard` |
| `/dashboard` | `OperationsDashboard` |
| `/exceptions` | `ExceptionQueue` |

### Wiring
- `main.jsx`: wrap `<App />` in `<BrowserRouter>`
- `App.jsx`: import `useNavigate`, `useLocation` from `react-router-dom`; tab `onClick` calls `navigate(path)`; active tab derived from `location.pathname` instead of local state
- Remove the `useState('dashboard')` tab state — routing is the source of truth
- Render `<Routes>` + `<Route>` in the `<main>` element; `<Navigate replace to="/dashboard" />` for `/`

---

## 5. Frontend — Exception Queue Polling

### Polling
`setInterval` every 5 000 ms in a `useEffect` with an empty deps array. Clears on unmount. On each tick, calls `getExceptions()` and replaces `queue` state.

The header badge in `App.jsx` (open exception count) also refreshes on the same interval — lifted to a shared `useEffect` or driven by the queue state via a count prop callback.

### Status pills
| Status value | Pill style | Label |
|---|---|---|
| `pending` | yellow | Pending |
| `evaluating` | yellow + spinner | Evaluating… |
| `investigating` | blue | Investigating |
| `awaiting_approval` | orange | Awaiting Approval |
| `resolved` | green | Resolved |
| `rejected` | gray | Rejected |

### Pre-check summary sub-line
When `precheck_summary` is present on a queue row, render a subtle sub-line below the type pill:
```
"Bad IBAN · 94% confidence — Correct IBAN checksum"
```
Format: `{classification} · {confidence}% confidence — {action_hint}`

### SLA badge
If `settlement_date` is within 24 hours of now, render a `⚠ SLA` orange badge in the TX ID cell.

---

## 6. Frontend — Dashboard Token Cost Chart

The existing "AI Token Cost by Exception Type" bar chart (`BarChart` in `OperationsDashboard.jsx`) is updated to show **two bars per exception type**:
- Light bar: pre-check average cost (USD)
- Dark bar: full investigation average cost (USD)

Data source: `GET /api/metrics/token-costs` (updated endpoint above).  
Tooltip shows: avg USD + avg token count for each tier.

---

---

## 7. Frontend — Resolution Archive

### What it is
A collapsible section below the active exception queue on the `/exceptions` page, showing all `resolved` and `rejected` cases with their investigation outcomes.

### Data source
`GET /api/exceptions` already returns resolved rows. Add a query param `?status=resolved,rejected` so the archive fetch is a separate call from the active queue poll (active queue fetches `?status=active` which filters to `pending`, `evaluating`, `investigating`, `awaiting_approval`). The backend splits on this param.

### Archive row columns
| Column | Content |
|---|---|
| TX ID | With SLA badge if applicable |
| Type | Exception type pill |
| Amount | Formatted amount |
| Decision | Green "Approved" or gray "Rejected" pill |
| Agent recommendation | `recommendation.action` truncated to 80 chars |
| Resolved at | Human-readable timestamp |
| AI investigation time | `avg_investigation_seconds` for this case (from investigations join) |

### Behaviour
- Collapsed by default, toggled open by a "Show resolved (N)" button above it
- Not polled — static fetch on expand, manual refresh button
- Sorted by `resolved_at` / `updated_at` DESC (most recently resolved first)
- Maximum 100 rows

### Backend changes
- `GET /api/exceptions` gains an optional `?status` query param. If omitted, returns all (current behaviour for backward compat). If `status=active`, returns exceptions where status not in `('resolved', 'rejected')`. If `status=resolved,rejected`, returns only those.
- Response adds `resolved_at` (from `investigations.completed_at` or `exceptions.updated_at`) and `recommendation_action` (from `investigations.recommendation->>'action'`) fields for archive rows.

---

## Out of Scope

- Retry logic for failed pre-checks
- Concurrency limit on the worker (single-consumer queue is sufficient for demo scale)
- Pre-check result shown inside the full investigation panel (pre-check summary is queue-level only)
- Persistent queue across server restarts (in-memory only)
