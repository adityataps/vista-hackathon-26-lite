import json
import os
import sys

from langchain_core.tools import tool

# Add backend/ to path so `db` is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from db import get_db


@tool
def get_payment_record(msg_id: str) -> str:
    """Fetch full payment record from the database by message ID."""
    conn = get_db()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, msg_id, uetr, instr_id, e2e_id, amount, currency,
                   settlement_date, sender_bic, receiver_bic, debtor_bic,
                   creditor_bic, debtor_name, debtor_iban, creditor_name,
                   creditor_iban, is_faulty, ingested_at
            FROM payments WHERE msg_id = %s
        """, (msg_id,))
        row = cur.fetchone()
    if not row:
        return json.dumps({"error": f"No payment found for msg_id={msg_id}"})
    cols = [
        "id", "msg_id", "uetr", "instr_id", "e2e_id", "amount", "currency",
        "settlement_date", "sender_bic", "receiver_bic", "debtor_bic",
        "creditor_bic", "debtor_name", "debtor_iban", "creditor_name",
        "creditor_iban", "is_faulty", "ingested_at",
    ]
    return json.dumps(dict(zip(cols, [str(v) if v is not None else None for v in row])))


@tool
def get_payment_events(uetr: str) -> str:
    """Fetch the full lifecycle event log for a payment by UETR.
    Returns chronological events: STP steps, status transitions, source systems, actors, and details.
    Use this to understand what happened to the payment before and after the error was detected."""
    conn = get_db()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    with conn.cursor() as cur:
        cur.execute("""
            SELECT event_type, status_code, source_system, actor, detail, occurred_at
            FROM payment_events
            WHERE uetr = %s
            ORDER BY occurred_at ASC
        """, (uetr,))
        rows = cur.fetchall()
    if not rows:
        return json.dumps({"events": [], "note": "No lifecycle events found for this UETR"})
    events = [
        {
            "event_type": r[0],
            "status_code": r[1],
            "source_system": r[2],
            "actor": r[3],
            "detail": r[4],
            "occurred_at": str(r[5]),
        }
        for r in rows
    ]
    return json.dumps({"uetr": uetr, "events": events})


@tool
def get_resolution_history(error_code: str) -> str:
    """Fetch prior resolved investigation cases for the same error code."""
    conn = get_db()
    if not conn:
        return json.dumps([])
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.msg_id, i.recommendation, i.completed_at
            FROM investigations i
            JOIN exceptions e ON e.id = i.exception_id
            WHERE i.approval_status = 'approved'
              AND e.detected_errors @> CAST(%s AS jsonb)
            ORDER BY i.completed_at DESC
            LIMIT 5
        """, ([{"code": error_code}],))
        rows = cur.fetchall()
    history = [
        {"msg_id": r[0], "recommendation": r[1], "resolved_at": str(r[2])}
        for r in rows
    ]
    return json.dumps(history)
