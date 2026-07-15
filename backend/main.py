import json
import logging
import os
import re
import defusedxml.ElementTree as ET
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
    get_db()  # opens connection + runs _ensure_schema if DATABASE_URL is set
    yield


from agents.graph import build_graph, make_llm as _make_llm

_llm = None
_investigation_graph = None


def get_graph():
    global _llm, _investigation_graph
    if _investigation_graph is None:
        _llm = _make_llm()
        _investigation_graph = build_graph(_llm)
    return _investigation_graph


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
