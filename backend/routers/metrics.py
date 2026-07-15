import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter

from db import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# Static mock rails added on top of real CBPR+ volume
_SEPA_SCT_CONSTANT = 120
_FEDWIRE_CONSTANT = 45
_MANUAL_COST_BASELINE = 27.50   # USD per case — industry midpoint $15–$40
_LLM_COST_PER_CASE = 0.29       # USD — average across exception types
_INPUT_PRICE_PER_1K = 0.003     # USD per 1k input tokens, claude-sonnet-4-6 us-west-2
_OUTPUT_PRICE_PER_1K = 0.015    # USD per 1k output tokens


# ── KPIs ──────────────────────────────────────────────────────────────────────

@router.get("/api/metrics/kpis")
def get_kpis():
    conn = get_db()
    if not conn:
        return {"in_flight": 0, "exceptions_open": 0, "settlement_risk": 0,
                "mttr_before": "38m", "mttr_now": "—"}

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM payments")
        in_flight = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM exceptions
            WHERE status NOT IN ('resolved', 'cancelled')
        """)
        exceptions_open = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM exceptions e
            JOIN payments p ON p.msg_id = e.msg_id
            WHERE e.status NOT IN ('resolved', 'cancelled')
              AND p.settlement_date IS NOT NULL
              AND p.settlement_date <= CURRENT_DATE + 1
        """)
        settlement_risk = cur.fetchone()[0]

        cur.execute("""
            SELECT AVG(EXTRACT(EPOCH FROM (completed_at - created_at)))
            FROM investigations WHERE completed_at IS NOT NULL
        """)
        avg_secs = cur.fetchone()[0]

    mttr_now = f"{int(avg_secs)}s" if avg_secs and avg_secs < 60 else (
        f"{int(avg_secs // 60)}m {int(avg_secs % 60)}s" if avg_secs else "—"
    )

    return {
        "in_flight": in_flight,
        "exceptions_open": exceptions_open,
        "settlement_risk": settlement_risk,
        "mttr_before": "38m",
        "mttr_now": mttr_now,
    }


# ── Transaction volume (hourly, today) ────────────────────────────────────────

@router.get("/api/metrics/volume")
def get_volume():
    conn = get_db()
    hours = [f"{h:02d}:00" for h in range(8, 18)]

    if not conn:
        return [{"hour": h, "sepa_sct": _SEPA_SCT_CONSTANT,
                 "fedwire": _FEDWIRE_CONSTANT, "swift_cbpr": 0, "exceptions": 0}
                for h in hours]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE_TRUNC('hour', ingested_at) AS hr, COUNT(*) AS cnt
            FROM payments
            WHERE ingested_at >= CURRENT_DATE
            GROUP BY hr ORDER BY hr
        """)
        cbpr_by_hour = {r[0].strftime("%H:00"): r[1] for r in cur.fetchall()}

        cur.execute("""
            SELECT DATE_TRUNC('hour', created_at) AS hr, COUNT(*) AS cnt
            FROM exceptions
            WHERE created_at >= CURRENT_DATE
            GROUP BY hr ORDER BY hr
        """)
        exc_by_hour = {r[0].strftime("%H:00"): r[1] for r in cur.fetchall()}

    return [
        {
            "hour": h,
            "sepa_sct": _SEPA_SCT_CONSTANT,
            "fedwire": _FEDWIRE_CONSTANT,
            "swift_cbpr": cbpr_by_hour.get(h, 0),
            "exceptions": exc_by_hour.get(h, 0),
        }
        for h in hours
    ]


# ── Cost savings (hourly, today) ───────────────────────────────────────────────

@router.get("/api/metrics/savings")
def get_savings():
    conn = get_db()
    hours = [f"{h:02d}:00" for h in range(8, 18)]

    if not conn:
        return [{"hour": h, "swift_cbpr": _MANUAL_COST_BASELINE - _LLM_COST_PER_CASE,
                 "sepa_sct": 27.1, "fedwire": 26.9, "baseline": _MANUAL_COST_BASELINE}
                for h in hours]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE_TRUNC('hour', completed_at) AS hr, COUNT(*) AS resolved
            FROM investigations
            WHERE completed_at >= CURRENT_DATE
            GROUP BY hr ORDER BY hr
        """)
        resolved_by_hour = {r[0].strftime("%H:00"): r[1] for r in cur.fetchall()}

    return [
        {
            "hour": h,
            "swift_cbpr": round(_MANUAL_COST_BASELINE - _LLM_COST_PER_CASE, 2)
                          if resolved_by_hour.get(h, 0) > 0
                          else _MANUAL_COST_BASELINE,
            "sepa_sct": 27.1,
            "fedwire": 26.9,
            "baseline": _MANUAL_COST_BASELINE,
        }
        for h in hours
    ]


# ── Exception breakdown by type ────────────────────────────────────────────────

_CODE_TO_DISPLAY = {
    "IBAN_INVALID_CHECKSUM":      "Bad IBAN (checksum)",
    "IBAN_WRONG_LENGTH":          "Bad IBAN (checksum)",
    "BIC_IBAN_COUNTRY_MISMATCH":  "Bad IBAN (checksum)",
    "BIC_INVALID_COUNTRY":        "Invalid BIC",
    "DUPLICATE_UETR":             "Duplicate UETR",
    "BENEFICIARY_NAME_INCOMPLETE":"Missing mandatory field",
    "ADDRESS_INCOMPLETE":         "Missing mandatory field",
    "XCHG_RATE_INCONSISTENT":     "FX limit breach",
}


@router.get("/api/metrics/exceptions")
def get_exception_breakdown():
    conn = get_db()
    if not conn:
        return []

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                e.detected_errors,
                e.status,
                i.completed_at,
                i.created_at
            FROM exceptions e
            LEFT JOIN investigations i ON i.exception_id = e.id
        """)
        rows = cur.fetchall()

    buckets: dict[str, dict] = {}
    for detected_errors, status, completed_at, created_at in rows:
        errors = detected_errors if isinstance(detected_errors, list) else []
        code = errors[0].get("code", "Unknown") if errors else "Unknown"
        display = _CODE_TO_DISPLAY.get(code, code.replace("_", " ").title())

        b = buckets.setdefault(display, {"type": display, "count": 0, "approved": 0,
                                          "rejected": 0, "total_secs": 0, "resolved": 0})
        b["count"] += 1
        if status == "resolved":
            b["approved"] += 1
        elif status == "rejected":
            b["rejected"] += 1

        if completed_at and created_at:
            secs = (completed_at - created_at).total_seconds()
            b["total_secs"] += secs
            b["resolved"] += 1

    result = []
    for b in buckets.values():
        avg_min = round(b["total_secs"] / 60 / max(b["resolved"], 1), 1)
        result.append({
            "type": b["type"],
            "count": b["count"],
            "approved": b["approved"],
            "rejected": b["rejected"],
            "avg_resolution_min": avg_min,
        })
    return sorted(result, key=lambda x: -x["count"])


# ── Correspondent health ───────────────────────────────────────────────────────

@router.get("/api/metrics/correspondents")
def get_correspondents():
    conn = get_db()
    if not conn:
        return []

    with conn.cursor() as cur:
        # Average time between PAYMENT_RECEIVED and SETTLEMENT_CONFIRMED per receiver BIC
        cur.execute("""
            SELECT
                p.receiver_bic AS bic,
                COUNT(DISTINCT p.id) AS total,
                AVG(
                    EXTRACT(EPOCH FROM (
                        settled.occurred_at - received.occurred_at
                    )) / 60
                ) AS avg_min,
                COUNT(DISTINCT CASE WHEN pe_delay.msg_id IS NOT NULL THEN p.id END) AS delayed
            FROM payments p
            LEFT JOIN payment_events received
                ON received.msg_id = p.msg_id AND received.event_type = 'PAYMENT_RECEIVED'
            LEFT JOIN payment_events settled
                ON settled.msg_id = p.msg_id AND settled.event_type = 'SETTLEMENT_CONFIRMED'
            LEFT JOIN payment_events pe_delay
                ON pe_delay.msg_id = p.msg_id AND pe_delay.event_type = 'PROCESSING_DELAYED'
            WHERE p.receiver_bic IS NOT NULL
            GROUP BY p.receiver_bic
            ORDER BY delayed DESC, total DESC
            LIMIT 10
        """)
        rows = cur.fetchall()

    result = []
    for bic, total, avg_min, delayed in rows:
        status = "normal"
        if delayed and delayed > 0:
            status = "outage" if delayed >= 3 else "degraded"
        result.append({
            "bic": bic,
            "bank": bic,  # BIC as stand-in; BIC directory lookup would resolve this
            "country": bic[4:6] if len(bic) >= 6 else "—",
            "status": status,
            "avg_processing_min": round(avg_min, 1) if avg_min else 0,
            "delayed": delayed or 0,
        })
    return result


# ── AI stats ──────────────────────────────────────────────────────────────────

@router.get("/api/metrics/ai")
def get_ai_stats():
    conn = get_db()
    if not conn:
        return {"total_investigations": 0, "auto_resolved": 0,
                "escalated_to_human": 0, "recommendation_acceptance_rate": 0,
                "avg_investigation_seconds": 0}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(completed_at) AS completed,
                AVG(EXTRACT(EPOCH FROM (completed_at - created_at)))
                    FILTER (WHERE completed_at IS NOT NULL) AS avg_secs,
                COUNT(*) FILTER (WHERE approval_status = 'approved') AS approved,
                COUNT(*) FILTER (WHERE approval_status IN ('approved', 'rejected')) AS decided
            FROM investigations
        """)
        total, completed, avg_secs, approved, decided = cur.fetchone()

    acceptance = round(approved / decided, 2) if decided else 0.0

    return {
        "total_investigations": total or 0,
        "auto_resolved": completed or 0,
        "escalated_to_human": (total or 0) - (completed or 0),
        "recommendation_acceptance_rate": acceptance,
        "avg_investigation_seconds": round(avg_secs or 0),
    }


# ── Hourly throughput ─────────────────────────────────────────────────────────

@router.get("/api/metrics/throughput")
def get_throughput():
    conn = get_db()
    hours = [f"{h:02d}:00" for h in range(8, 18)]

    if not conn:
        return [{"hour": h, "detected": 0, "resolved": 0} for h in hours]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE_TRUNC('hour', created_at) AS hr, COUNT(*) AS cnt
            FROM exceptions WHERE created_at >= CURRENT_DATE
            GROUP BY hr ORDER BY hr
        """)
        detected = {r[0].strftime("%H:00"): r[1] for r in cur.fetchall()}

        cur.execute("""
            SELECT DATE_TRUNC('hour', completed_at) AS hr, COUNT(*) AS cnt
            FROM investigations WHERE completed_at >= CURRENT_DATE
            GROUP BY hr ORDER BY hr
        """)
        resolved = {r[0].strftime("%H:00"): r[1] for r in cur.fetchall()}

    return [{"hour": h, "detected": detected.get(h, 0), "resolved": resolved.get(h, 0)}
            for h in hours]


# ── Token costs ──────────────────────────────────────────────────────────────

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


# ── Demo payment generator ────────────────────────────────────────────────────

@router.post("/api/demo/generate")
def demo_generate():
    """Thin wrapper over /api/seed for the frontend Generate button."""
    from main import app as _app
    from pacs008_generator.generator import generate_batch
    from db import get_db as _get_db
    import main as _main

    manifest = generate_batch(count=15, error_rate=0.4, write_files=False)
    conn = _get_db()
    generated = len(manifest["messages"])
    if conn:
        try:
            _main._write_events(conn, manifest["messages"])
            _main._seed_write_db(conn, manifest["messages"], "")
        except Exception as exc:
            logger.warning("demo/generate DB write failed: %s", exc)
    return {"generated": generated}


# ── Monitoring — in-flight payments ───────────────────────────────────────────

@router.get("/api/monitoring/inflight")
def get_inflight():
    conn = get_db()
    if not conn:
        return []

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.id, p.msg_id, p.uetr, p.amount, p.currency,
                p.sender_bic, p.receiver_bic,
                MIN(recv.occurred_at) AS received_at,
                MAX(pe_delay.occurred_at) AS delay_at,
                BOOL_OR(pe_delay.msg_id IS NOT NULL) AS is_stuck
            FROM payments p
            JOIN payment_events recv
                ON recv.msg_id = p.msg_id AND recv.event_type = 'PAYMENT_RECEIVED'
            LEFT JOIN payment_events settled
                ON settled.msg_id = p.msg_id AND settled.event_type = 'SETTLEMENT_CONFIRMED'
            LEFT JOIN payment_events pe_delay
                ON pe_delay.msg_id = p.msg_id AND pe_delay.event_type = 'PROCESSING_DELAYED'
            WHERE settled.id IS NULL
            GROUP BY p.id
            ORDER BY received_at ASC
            LIMIT 50
        """)
        rows = cur.fetchall()

    now = datetime.now(timezone.utc)
    result = []
    for pid, msg_id, uetr, amount, currency, sender_bic, receiver_bic, received_at, delay_at, is_stuck in rows:
        elapsed_min = round((now - received_at).total_seconds() / 60, 1) if received_at else 0
        if is_stuck or elapsed_min > 360:
            risk = "breached"
        elif elapsed_min > 120:
            risk = "at-risk"
        else:
            risk = "on-track"

        result.append({
            "tx_id": f"TX-{pid:05d}",
            "msg_id": msg_id,
            "uetr": uetr,
            "amount": f"{amount} {currency}" if amount else "—",
            "sender_bic": sender_bic,
            "receiver_bic": receiver_bic,
            "elapsed_min": elapsed_min,
            "risk": risk,
            "is_stuck": bool(is_stuck),
        })
    return result


# ── Monitoring — active alerts ─────────────────────────────────────────────────

@router.get("/api/monitoring/alerts")
def get_alerts():
    conn = get_db()
    if not conn:
        return []

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                e.id, e.msg_id, e.status, e.created_at, e.detected_errors,
                p.amount, p.currency, p.receiver_bic
            FROM exceptions e
            LEFT JOIN payments p ON p.msg_id = e.msg_id
            WHERE e.status NOT IN ('resolved', 'cancelled')
              AND e.created_at < NOW() - INTERVAL '30 minutes'
            ORDER BY e.created_at ASC
            LIMIT 20
        """)
        rows = cur.fetchall()

    now = datetime.now(timezone.utc)
    result = []
    for exc_id, msg_id, status, created_at, detected_errors, amount, currency, receiver_bic in rows:
        errors = detected_errors if isinstance(detected_errors, list) else []
        code = errors[0].get("code", "Unknown") if errors else "Unknown"
        age_min = round((now - created_at).total_seconds() / 60) if created_at else 0
        result.append({
            "alert_id": f"ALT-{exc_id:04d}",
            "msg_id": msg_id,
            "error_code": code,
            "status": status,
            "age_min": age_min,
            "severity": "high" if age_min > 120 else "medium",
            "amount": f"{amount} {currency}" if amount else "—",
            "receiver_bic": receiver_bic,
        })
    return result


# ── Monitoring — corridor latency heatmap ─────────────────────────────────────

@router.get("/api/monitoring/heatmap")
def get_heatmap():
    conn = get_db()
    if not conn:
        return []

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.currency AS from_ccy,
                p.receiver_bic,
                AVG(EXTRACT(EPOCH FROM (settled.occurred_at - recv.occurred_at)) / 60) AS avg_min,
                COUNT(*) AS count
            FROM payments p
            JOIN payment_events recv
                ON recv.msg_id = p.msg_id AND recv.event_type = 'PAYMENT_RECEIVED'
            JOIN payment_events settled
                ON settled.msg_id = p.msg_id AND settled.event_type = 'SETTLEMENT_CONFIRMED'
            WHERE p.currency IS NOT NULL AND p.receiver_bic IS NOT NULL
            GROUP BY p.currency, p.receiver_bic
            ORDER BY avg_min DESC NULLS LAST
            LIMIT 30
        """)
        rows = cur.fetchall()

    result = []
    for from_ccy, receiver_bic, avg_min, count in rows:
        avg_min = round(avg_min or 0, 1)
        status = "breached" if avg_min > 360 else ("at-risk" if avg_min > 120 else "on-track")
        result.append({
            "corridor": f"{from_ccy} → {receiver_bic[4:6] if receiver_bic and len(receiver_bic) >= 6 else '??'}",
            "from_currency": from_ccy,
            "receiver_bic": receiver_bic,
            "avg_processing_min": avg_min,
            "payment_count": count,
            "status": status,
        })
    return result
