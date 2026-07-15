import asyncio
import json
import logging
import os
import re
import defusedxml.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import boto3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import get_db, _ensure_schema
from pacs008_generator.generator import generate_batch
from routers.exceptions import router as exceptions_router
from routers.resolutions import router as resolutions_router
from routers.metrics import router as metrics_router

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET", "")

_HEAD_NS = 'urn:iso:std:iso:20022:tech:xsd:head.001.001.02'
_PACS_NS = 'urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08'


def _parse_pacs008_fields(xml_str: str) -> dict:
    """Extract display fields from pacs.008 XML for the payments table."""
    clean = re.sub(r'<\?xml[^?]*\?>', '', xml_str)
    clean = re.sub(r'<!--.*?-->', '', clean, flags=re.DOTALL).strip()
    wrapped = (
        f'<root xmlns:head="{_HEAD_NS}" xmlns:pacs="{_PACS_NS}">'
        + clean + '</root>'
    )
    root = ET.fromstring(wrapped)
    h, p = f'{{{_HEAD_NS}}}', f'{{{_PACS_NS}}}'

    def tx(path):
        el = root.find(path)
        return el.text.strip() if el is not None and el.text else None

    tx_path = f'.//{p}CdtTrfTxInf'
    dbtr_acct = f'{tx_path}/{p}DbtrAcct/{p}Id'
    cdtr_acct = f'{tx_path}/{p}CdtrAcct/{p}Id'
    amt_el = root.find(f'{tx_path}/{p}IntrBkSttlmAmt')

    return {
        'amount': amt_el.text.strip() if amt_el is not None and amt_el.text else None,
        'currency': amt_el.get('Ccy') if amt_el is not None else None,
        'settlement_date': tx(f'{tx_path}/{p}IntrBkSttlmDt'),
        'sender_bic': tx(f'.//{h}Fr/{h}FIId/{h}FinInstnId/{h}BICFI'),
        'receiver_bic': tx(f'.//{h}To/{h}FIId/{h}FinInstnId/{h}BICFI'),
        'debtor_bic': tx(f'{tx_path}/{p}DbtrAgt/{p}FinInstnId/{p}BICFI'),
        'creditor_bic': tx(f'{tx_path}/{p}CdtrAgt/{p}FinInstnId/{p}BICFI'),
        'debtor_name': tx(f'{tx_path}/{p}Dbtr/{p}Nm'),
        'debtor_iban': (tx(f'{dbtr_acct}/{p}IBAN') or tx(f'{dbtr_acct}/{p}Othr/{p}Id')),
        'creditor_name': tx(f'{tx_path}/{p}Cdtr/{p}Nm'),
        'creditor_iban': (tx(f'{cdtr_acct}/{p}IBAN') or tx(f'{cdtr_acct}/{p}Othr/{p}Id')),
    }


def _seed_write_db(conn, messages: list, s3_prefix: str):
    """Write payments + exceptions directly from the seed manifest (no Lambda needed)."""
    for msg in messages:
        try:
            fields = _parse_pacs008_fields(msg["xml"])
        except Exception as exc:
            logger.warning("XML parse failed for %s: %s", msg.get("msg_id"), exc)
            fields = {}

        s3_key = s3_prefix + msg["file"] if s3_prefix else msg["file"]
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO payments (
                    s3_key, msg_id, uetr, amount, currency, settlement_date,
                    sender_bic, receiver_bic, debtor_bic, creditor_bic,
                    debtor_name, debtor_iban, creditor_name, creditor_iban,
                    is_faulty, raw_xml
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (msg_id) DO UPDATE SET
                    is_faulty = EXCLUDED.is_faulty,
                    raw_xml   = EXCLUDED.raw_xml
                RETURNING id
            """, (
                s3_key, msg["msg_id"], msg["uetr"],
                fields.get("amount"), fields.get("currency"), fields.get("settlement_date"),
                fields.get("sender_bic"), fields.get("receiver_bic"),
                fields.get("debtor_bic"), fields.get("creditor_bic"),
                fields.get("debtor_name"), fields.get("debtor_iban"),
                fields.get("creditor_name"), fields.get("creditor_iban"),
                msg["is_faulty"], msg["xml"],
            ))
            payment_id = cur.fetchone()[0]

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


def _write_events(conn, messages):
    """Bulk-upsert all events from the manifest messages list."""
    rows = []
    for msg in messages:
        for evt in msg.get("events", []):
            rows.append({
                **evt,
                "msg_id": msg["msg_id"],
                "uetr": msg["uetr"],
            })
    if not rows:
        return
    with conn.cursor() as cur:
        for row in rows:
            cur.execute("""
                INSERT INTO payment_events
                    (event_id, uetr, msg_id, event_type, status_code,
                     source_system, actor, detail, occurred_at)
                VALUES
                    (%(event_id)s, %(uetr)s, %(msg_id)s, %(event_type)s, %(status_code)s,
                     %(source_system)s, %(actor)s, %(detail)s, %(occurred_at)s)
                ON CONFLICT (event_id) DO NOTHING
            """, row)
    conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_graph()  # warms _llm and _investigation_graph

    conn = get_db()
    if conn:
        with conn.cursor() as cur:
            # Reset any exceptions interrupted mid-run by a previous server restart
            cur.execute(
                "UPDATE exceptions SET status='pending'"
                " WHERE status IN ('investigating', 'evaluating')"
            )
            cur.execute(
                "SELECT msg_id, precheck_summary FROM exceptions WHERE status = 'pending'"
            )
            rows = cur.fetchall()
        conn.commit()
        for msg_id, precheck_summary in rows:
            if precheck_summary:
                _auto_investigate_queue.put_nowait(msg_id)
            else:
                _precheck_queue.put_nowait(msg_id)
        if rows:
            logger.info(
                "Startup: enqueued %d exceptions (%d need precheck)",
                len(rows),
                sum(1 for _, ps in rows if not ps),
            )

    precheck_worker = asyncio.create_task(_precheck_worker())
    auto_investigate_worker = asyncio.create_task(_auto_investigate_worker())
    yield
    for task in (precheck_worker, auto_investigate_worker):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


from agents.graph import build_graph, make_llm as _make_llm

_llm = None
_investigation_graph = None

_precheck_queue: asyncio.Queue = asyncio.Queue()
_auto_investigate_queue: asyncio.Queue = asyncio.Queue()


def get_graph():
    global _llm, _investigation_graph
    if _investigation_graph is None:
        _llm = _make_llm()
        _investigation_graph = build_graph(_llm)
    return _investigation_graph


def get_llm():
    get_graph()  # ensures _llm is initialised
    return _llm


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

    try:
        result = await intake_node(initial_state, get_llm())

        usage = result.get("usage_metadata", {})
        intake_cls = result.get("intake_classification", {})
        steps = result.get("steps", [])
        precheck_summary = {
            "needs_technical": intake_cls.get("needs_technical", False),
            "needs_compliance": intake_cls.get("needs_compliance", False),
            "action_hint": steps[0].get("text", "") if steps else "",
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
        _auto_investigate_queue.put_nowait(tx_id)

    except Exception:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exceptions SET status='pending', updated_at=NOW() WHERE id=%s",
                (exc_id,)
            )
        conn.commit()
        raise  # re-raises into _precheck_worker's outer try/except for logging


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


async def _run_full_investigation_bg(tx_id: str) -> None:
    """Run the full investigation graph in the background, store results in DB."""
    from routers.exceptions import _normalize_lg_event

    conn = get_db()
    if not conn:
        logger.warning("Auto-investigation skipped %s — no DB connection", tx_id)
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
        return  # already being investigated or not found

    (exc_id, detected_errors, pid, p_msg_id, uetr, amount, currency,
     sender_bic, receiver_bic, debtor_bic, creditor_bic,
     debtor_name, debtor_iban, creditor_name, creditor_iban, raw_xml) = row

    exc_msg_id = p_msg_id or tx_id
    errors = detected_errors if isinstance(detected_errors, list) else []
    payment = {
        "id": pid, "msg_id": p_msg_id, "uetr": uetr,
        "amount": str(amount) if amount else "0", "currency": currency or "",
        "sender_bic": sender_bic, "receiver_bic": receiver_bic,
        "debtor_bic": debtor_bic, "creditor_bic": creditor_bic,
        "debtor_name": debtor_name, "debtor_iban": debtor_iban,
        "creditor_name": creditor_name, "creditor_iban": creditor_iban,
    }

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO investigations (exception_id, msg_id, steps) VALUES (%s, %s, '[]') RETURNING id",
            (exc_id, exc_msg_id),
        )
        inv_id = cur.fetchone()[0]
        cur.execute("UPDATE exceptions SET status='investigating' WHERE id=%s", (exc_id,))
    conn.commit()

    initial_state = {
        "payment": payment, "detected_errors": errors, "swift_message": raw_xml or "",
        "intake_classification": {}, "investigation_context": {},
        "technical_findings": None, "compliance_findings": None,
        "recommendation": None, "steps": [], "investigation_id": inv_id, "msg_id": exc_msg_id,
    }

    accumulated_steps, final_state = [], {}
    total_input_tokens = total_output_tokens = 0

    # Incremental DB writes so the /stream endpoint can tail steps live.
    # Tool events are flushed immediately; text chunks are merged per agent
    # turn and flushed when the agent changes or a tool call interrupts.
    _inc_seq = 0
    _inc_pending = None  # {"agent", "cls", "text"} being accumulated

    def _inc_flush():
        nonlocal _inc_seq, _inc_pending
        step = _inc_pending
        _inc_pending = None  # clear before any await/raise so we never retry
        if step is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO investigation_steps (inv_id, seq, agent, cls, step_text)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (inv_id, _inc_seq, step["agent"], step["cls"], step["text"]),
                )
            conn.commit()
            _inc_seq += 1
        except Exception as e:
            logger.debug("investigation_steps flush skipped: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

    def _inc_write(sse):
        nonlocal _inc_seq, _inc_pending
        if sse["cls"] == "tool":
            _inc_flush()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO investigation_steps (inv_id, seq, agent, cls, step_text)"
                        " VALUES (%s,%s,%s,%s,%s)",
                        (inv_id, _inc_seq, sse["agent"], sse["cls"], sse["text"]),
                    )
                conn.commit()
                _inc_seq += 1
            except Exception as e:
                logger.debug("investigation_steps tool write skipped: %s", e)
                try:
                    conn.rollback()
                except Exception:
                    pass
        else:
            if _inc_pending and _inc_pending["agent"] == sse["agent"] and _inc_pending["cls"] == sse["cls"]:
                _inc_pending["text"] += sse["text"]
            else:
                _inc_flush()
                _inc_pending = {"agent": sse["agent"], "cls": sse["cls"], "text": sse["text"]}

    try:
        async for event in get_graph().astream_events(initial_state, version="v2"):
            sse = _normalize_lg_event(event)
            if sse:
                accumulated_steps.append({**sse, "ts": datetime.now(timezone.utc).isoformat()})
                _inc_write(sse)

            if event.get("event") == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                if output is not None:
                    meta = getattr(output, "usage_metadata", None)
                    if isinstance(meta, dict):
                        total_input_tokens += meta.get("input_tokens", 0)
                        total_output_tokens += meta.get("output_tokens", 0)

            if event.get("event") == "on_chain_end" and event.get("name") == "LangGraph":
                final_state = event.get("data", {}).get("output", {})

        _inc_flush()  # write any remaining buffered agent text
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
                total_input_tokens, total_output_tokens, inv_id,
            ))
            cur.execute("""
                UPDATE exceptions SET status='awaiting_approval', recommendation=%s,
                    recommended_sql=%s WHERE id=%s
            """, (json.dumps(recommendation), recommendation.get("sql"), exc_id))
        conn.commit()
        logger.info("Auto-investigation complete: %s (inv=%d)", tx_id, inv_id)

    except Exception as exc:
        try:
            conn.rollback()  # clear any aborted transaction before attempting status reset
        except Exception:
            pass
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE exceptions SET status='pending' WHERE id=%s", (exc_id,))
            conn.commit()
        except Exception:
            pass  # best-effort; connection may be unrecoverable
        logger.error("Auto-investigation failed for %s: %s", tx_id, exc)
        raise


async def _auto_investigate_worker() -> None:
    """Drain the auto-investigation queue indefinitely (one at a time)."""
    while True:
        tx_id = await _auto_investigate_queue.get()
        try:
            await _run_full_investigation_bg(tx_id)
        except Exception as exc:
            logger.error("Auto-investigation error for %s: %s", tx_id, exc)
        finally:
            _auto_investigate_queue.task_done()


app = FastAPI(title="PayInvestigator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(exceptions_router)
app.include_router(resolutions_router)
app.include_router(metrics_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/ping")
def ping():
    return {"message": "pong"}


# ── Pacs.008 generation ────────────────────────────────────────────────────────

class SeedRequest(BaseModel):
    count: int = 10
    error_rate: float = 0.3
    seed: Optional[int] = None
    error_codes: Optional[list[str]] = None
    stuck_rate: float = 0.0


@app.post("/api/seed")
def seed(req: SeedRequest):
    manifest = generate_batch(
        count=req.count,
        error_rate=req.error_rate,
        seed=req.seed,
        error_codes=req.error_codes or None,
        write_files=False,
        stuck_rate=req.stuck_rate,
    )

    run_id = manifest["run_id"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"payments/{ts}-{run_id}/"

    # S3 upload is optional — skip gracefully when S3_BUCKET is not set (local dev)
    uploaded = []
    s3_ok = bool(S3_BUCKET)
    if s3_ok:
        s3 = boto3.client("s3")
        for msg in manifest["messages"]:
            key = prefix + msg["file"]
            try:
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=key,
                    Body=msg["xml"].encode("utf-8"),
                    ContentType="application/xml",
                )
                uploaded.append(key)
            except Exception as exc:
                logger.warning("S3 upload failed for %s: %s", key, exc)
                s3_ok = False
                break

    conn = get_db()
    events_written = 0
    payments_written = 0
    exceptions_written = 0
    if conn:
        try:
            _write_events(conn, manifest["messages"])
            events_written = sum(len(m.get("events", [])) for m in manifest["messages"])
        except Exception as exc:
            logger.warning("Event write failed (non-fatal): %s", exc)
        try:
            _seed_write_db(conn, manifest["messages"], prefix if s3_ok else "")
            payments_written = len(manifest["messages"])
            exceptions_written = sum(1 for m in manifest["messages"] if m["is_faulty"])
        except Exception as exc:
            logger.warning("Payment/exception DB write failed (non-fatal): %s", exc)

    messages_summary = [
        {
            "file": msg["file"],
            "s3_key": (prefix + msg["file"]) if s3_ok else None,
            "uetr": msg["uetr"],
            "is_faulty": msg["is_faulty"],
            "is_stuck": msg.get("is_stuck", False),
            "errors": msg["errors"],
        }
        for msg in manifest["messages"]
    ]

    return {
        "run_id": run_id,
        "count": len(messages_summary),
        "faulty": sum(1 for m in messages_summary if m["is_faulty"]),
        "stuck": sum(1 for m in messages_summary if m["is_stuck"]),
        "events_written": events_written,
        "payments_written": payments_written,
        "exceptions_written": exceptions_written,
        "s3_prefix": f"s3://{S3_BUCKET}/{prefix}" if s3_ok else None,
        "messages": messages_summary,
    }
