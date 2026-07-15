import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# Maps error code → (display type, type_key) for frontend pill colours
ERROR_TYPE_MAP = {
    "IBAN_INVALID_CHECKSUM":      ("Bad IBAN",        "iban"),
    "IBAN_WRONG_LENGTH":          ("Bad IBAN",        "iban"),
    "BIC_IBAN_COUNTRY_MISMATCH":  ("Bad IBAN",        "iban"),
    "BIC_INVALID_COUNTRY":        ("Bad IBAN",        "iban"),
    "BENEFICIARY_NAME_INCOMPLETE":("ISO 20022 field", "iso"),
    "ADDRESS_INCOMPLETE":         ("ISO 20022 field", "iso"),
    "DUPLICATE_UETR":             ("Duplicate ref",   "duplicate"),
    "XCHG_RATE_INCONSISTENT":     ("FX limit breach", "fx"),
}


def _format_amount(amount, currency) -> str:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "CHF": "CHF "}
    sym = symbols.get(currency, f"{currency} ")
    try:
        val = float(amount)
        return f"{sym}{val:,.0f}"
    except Exception:
        return f"{sym}{amount}"


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


class IngestExceptionRequest(BaseModel):
    msg_id: str
    uetr: str
    detected_errors: list
    payment_id: Optional[int] = None


@router.post("/api/ingest/exceptions")
def ingest_exception(req: IngestExceptionRequest):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="DB unavailable")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO exceptions (msg_id, uetr, detected_errors, payment_id, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (msg_id) DO UPDATE SET
                detected_errors = EXCLUDED.detected_errors,
                updated_at = NOW()
            RETURNING id
        """, (req.msg_id, req.uetr, json.dumps(req.detected_errors), req.payment_id))
        exception_id = cur.fetchone()[0]
    conn.commit()
    logger.info("Exception created/updated: id=%s msg_id=%s", exception_id, req.msg_id)
    return {"exception_id": exception_id}


def _normalize_lg_event(event: dict) -> dict | None:
    """Convert a LangGraph astream_events event to {agent, cls, text} SSE shape."""
    kind = event.get("event", "")
    node = event.get("metadata", {}).get("langgraph_node", "")

    NODE_META = {
        "intake":      ("Intake Agent",        "intake"),
        "investigate": ("Investigation Agent",  "investigation"),
        "technical":   ("Technical Diagnosis",  "technical"),
        "compliance":  ("Compliance Agent",     "compliance"),
        "resolution":  ("Resolution Agent",     "resolution"),
    }

    if kind == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        text = ""
        if chunk and hasattr(chunk, "content"):
            text = chunk.content if isinstance(chunk.content, str) else ""
        if not text:
            return None
        agent_name, cls = NODE_META.get(node, ("Agent", "agent"))
        return {"agent": agent_name, "cls": cls, "text": text}

    if kind == "on_tool_start":
        tool_name = event.get("name", "tool")
        tool_input = event.get("data", {}).get("input", {})
        args_str = ", ".join(f"{k}={repr(v)}" for k, v in tool_input.items())
        return {"agent": "tool", "cls": "tool", "text": f"🔧 {tool_name}({args_str})"}

    if kind == "on_tool_end":
        tool_name = event.get("name", "tool")
        output = str(event.get("data", {}).get("output", ""))[:150]
        return {"agent": "tool", "cls": "tool", "text": f"↳ {output}"}

    return None


@router.post("/api/exceptions/{tx_id}/investigate")
async def investigate(tx_id: str):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="DB unavailable")

    # Support both TX-00001 (from frontend) and raw msg_id formats
    if tx_id.startswith("TX-"):
        try:
            payment_id = int(tx_id[3:])
            id_clause = "p.id = %s"
            id_val = payment_id
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid TX-id format")
    else:
        id_clause = "e.msg_id = %s"
        id_val = tx_id

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT e.id, e.detected_errors, p.id as pid,
                   p.msg_id, p.uetr, p.amount, p.currency,
                   p.sender_bic, p.receiver_bic,
                   p.debtor_bic, p.creditor_bic, p.debtor_name, p.debtor_iban,
                   p.creditor_name, p.creditor_iban, p.is_faulty, p.raw_xml
            FROM exceptions e
            LEFT JOIN payments p ON p.msg_id = e.msg_id
            WHERE {id_clause}
        """, (id_val,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Exception not found: {tx_id}")

    (exc_id, detected_errors, pid, p_msg_id, uetr, amount, currency,
     sender_bic, receiver_bic, debtor_bic, creditor_bic,
     debtor_name, debtor_iban, creditor_name, creditor_iban,
     is_faulty, raw_xml) = row

    # Use e.msg_id for all subsequent DB operations
    exc_msg_id = p_msg_id or tx_id

    payment = {
        "id": pid, "msg_id": p_msg_id, "uetr": uetr,
        "amount": str(amount) if amount else "0", "currency": currency,
        "sender_bic": sender_bic, "receiver_bic": receiver_bic,
        "debtor_bic": debtor_bic, "creditor_bic": creditor_bic,
        "debtor_name": debtor_name, "debtor_iban": debtor_iban,
        "creditor_name": creditor_name, "creditor_iban": creditor_iban,
    }

    errors = detected_errors if isinstance(detected_errors, list) else []

    # Create investigations row
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO investigations (exception_id, msg_id, steps)
            VALUES (%s, %s, '[]') RETURNING id
        """, (exc_id, exc_msg_id))
        inv_id = cur.fetchone()[0]
        cur.execute("UPDATE exceptions SET status='investigating' WHERE id=%s", (exc_id,))
    conn.commit()

    report_id = f"RPT-{inv_id:04d}"

    initial_state = {
        "payment": payment,
        "detected_errors": errors,
        "swift_message": raw_xml or "",
        "intake_classification": {},
        "investigation_context": {},
        "technical_findings": None,
        "compliance_findings": None,
        "recommendation": None,
        "steps": [],
        "investigation_id": inv_id,
        "msg_id": exc_msg_id,
    }

    from main import get_graph
    graph = get_graph()

    async def event_stream():
        accumulated_steps = []
        final_state = {}

        async for event in graph.astream_events(initial_state, version="v2"):
            sse = _normalize_lg_event(event)
            if sse:
                accumulated_steps.append({**sse, "ts": datetime.now(timezone.utc).isoformat()})
                yield f"data: {json.dumps(sse)}\n\n"

            if event.get("event") == "on_chain_end" and event.get("name") == "LangGraph":
                final_state = event.get("data", {}).get("output", {})

        recommendation = final_state.get("recommendation") or {}
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investigations
                SET steps=%s, findings=%s, recommendation=%s,
                    approval_status='pending', completed_at=NOW()
                WHERE id=%s
            """, (
                json.dumps(accumulated_steps),
                json.dumps({
                    "technical": final_state.get("technical_findings"),
                    "compliance": final_state.get("compliance_findings"),
                }),
                json.dumps(recommendation),
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

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
