"""Lambda handler: SQS-triggered pacs.008 XML ingest into PostgreSQL.

SQS receives S3 ObjectCreated events for the payments/ prefix.
Downloads each XML from S3, parses key pacs.008 fields, runs business-error
detection (error_rules.py, ported from
jobs/pacs008-generator/agent_error_knowledge.yaml), upserts into the payments
table (ON CONFLICT msg_id DO NOTHING for idempotency), writes/upserts an
`exceptions` row for faulty payments, and can optionally POST
{payment_id, error_msg} to a configurable endpoint.

Configuration (environment variables, set in infra/lambda.tf):
  DATABASE_URL            - Postgres connection string (required)
  REFERENCE_DATA_S3_URI   - s3://bucket/prefix/ for optional bic_directory.json /
                            watchlist.json / closed_accounts.json (optional -
                            missing files just disable the corresponding check)
  ERROR_NOTIFY_ENDPOINT_URL - POST target for detected errors (optional - if
                            unset, no POST is ever attempted)
"""
import json
import logging
import os
import re
import urllib.request
from decimal import Decimal

import boto3
import defusedxml.ElementTree as ET
import psycopg2

import error_rules
import reference_data
from notifier import notify_payment_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

HEAD_NS = 'urn:iso:std:iso:20022:tech:xsd:head.001.001.02'
PACS_NS = 'urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08'

_db_conn = None


def _get_db():
    global _db_conn
    if _db_conn is None or _db_conn.closed:
        _db_conn = psycopg2.connect(os.environ['DATABASE_URL'])
        _ensure_schema(_db_conn)
    return _db_conn


def _ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id              SERIAL PRIMARY KEY,
                s3_key          TEXT NOT NULL,
                msg_id          TEXT UNIQUE,
                uetr            TEXT,
                instr_id        TEXT,
                e2e_id          TEXT,
                amount          NUMERIC(20, 5),
                currency        VARCHAR(3),
                settlement_date DATE,
                sender_bic      TEXT,
                receiver_bic    TEXT,
                debtor_bic      TEXT,
                creditor_bic    TEXT,
                debtor_name     TEXT,
                debtor_iban     TEXT,
                creditor_name   TEXT,
                creditor_iban   TEXT,
                is_faulty       BOOLEAN DEFAULT FALSE,
                raw_xml         TEXT,
                ingested_at     TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_events (
                id            SERIAL PRIMARY KEY,
                event_id      TEXT UNIQUE NOT NULL,
                uetr          TEXT NOT NULL,
                msg_id        TEXT,
                event_type    TEXT NOT NULL,
                status_code   TEXT,
                source_system TEXT,
                actor         TEXT,
                detail        TEXT,
                occurred_at   TIMESTAMPTZ NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_uetr ON payment_events(uetr)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_msg_id ON payment_events(msg_id)")
        # Idempotent migration for tables created before error detection existed.
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS has_error BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS error_msg TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_has_error ON payments (has_error)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS exceptions (
                id              SERIAL PRIMARY KEY,
                payment_id      INTEGER,
                msg_id          TEXT UNIQUE NOT NULL,
                uetr            TEXT NOT NULL,
                detected_errors JSONB NOT NULL DEFAULT '[]',
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.commit()


def _parse_pacs008(xml_content):
    """Parse a two-fragment pacs.008 file (AppHdr + Document) into a dict."""
    clean = re.sub(r'<\?xml[^?]*\?>', '', xml_content)
    clean = re.sub(r'<!--.*?-->', '', clean, flags=re.DOTALL).strip()
    wrapped = (
        f'<root xmlns:head="{HEAD_NS}" xmlns:pacs="{PACS_NS}">'
        + clean
        + '</root>'
    )
    root = ET.fromstring(wrapped)
    h, p = f'{{{HEAD_NS}}}', f'{{{PACS_NS}}}'

    def tx(path):
        el = root.find(path)
        return el.text.strip() if el is not None and el.text else None

    tx_path = f'.//{p}CdtTrfTxInf'
    pmt_id = f'{tx_path}/{p}PmtId'
    dbtr_acct = f'{tx_path}/{p}DbtrAcct/{p}Id'
    cdtr_acct = f'{tx_path}/{p}CdtrAcct/{p}Id'

    amt_el = root.find(f'{tx_path}/{p}IntrBkSttlmAmt')
    amount = Decimal(amt_el.text.strip()) if amt_el is not None and amt_el.text else None
    currency = amt_el.get('Ccy') if amt_el is not None else None

    # Optional FX fields (only present when InstdAmt currency differs from
    # settlement currency) - used by error_rules.check_xchg_rate_inconsistent,
    # not persisted to the payments table.
    instd_amt_el = root.find(f'{tx_path}/{p}InstdAmt')
    instd_amt = Decimal(instd_amt_el.text.strip()) if instd_amt_el is not None and instd_amt_el.text else None
    instd_amt_ccy = instd_amt_el.get('Ccy') if instd_amt_el is not None else None

    return {
        'msg_id':         tx(f'.//{h}BizMsgIdr'),
        'uetr':           tx(f'{pmt_id}/{p}UETR'),
        'instr_id':       tx(f'{pmt_id}/{p}InstrId'),
        'e2e_id':         tx(f'{pmt_id}/{p}EndToEndId'),
        'amount':         amount,
        'currency':       currency,
        'instd_amt':      instd_amt,
        'instd_amt_ccy':  instd_amt_ccy,
        'xchg_rate':      tx(f'{tx_path}/{p}XchgRate'),
        'settlement_date': tx(f'{tx_path}/{p}IntrBkSttlmDt'),
        'sender_bic':     tx(f'.//{h}Fr/{h}FIId/{h}FinInstnId/{h}BICFI'),
        'receiver_bic':   tx(f'.//{h}To/{h}FIId/{h}FinInstnId/{h}BICFI'),
        'debtor_bic':     tx(f'{tx_path}/{p}DbtrAgt/{p}FinInstnId/{p}BICFI'),
        'creditor_bic':   tx(f'{tx_path}/{p}CdtrAgt/{p}FinInstnId/{p}BICFI'),
        'debtor_name':    tx(f'{tx_path}/{p}Dbtr/{p}Nm'),
        'debtor_iban':    tx(f'{dbtr_acct}/{p}IBAN') or tx(f'{dbtr_acct}/{p}Othr/{p}Id'),
        'creditor_name':  tx(f'{tx_path}/{p}Cdtr/{p}Nm'),
        'creditor_iban':  tx(f'{cdtr_acct}/{p}IBAN') or tx(f'{cdtr_acct}/{p}Othr/{p}Id'),
        # Address fields, used only by error_rules.check_address_incomplete.
        'creditor_ctry':    tx(f'{tx_path}/{p}Cdtr/{p}PstlAdr/{p}Ctry'),
        'creditor_twn_nm':  tx(f'{tx_path}/{p}Cdtr/{p}PstlAdr/{p}TwnNm'),
        'creditor_strt_nm': tx(f'{tx_path}/{p}Cdtr/{p}PstlAdr/{p}StrtNm'),
    }


_ref_data = None


def _get_reference_data():
    global _ref_data
    if _ref_data is None:
        _ref_data = reference_data.load_reference_data()
    return _ref_data


def _ingest_record(s3_key, parsed, raw_xml):
    """Runs error detection, upserts the row, and returns
    (payment_id, has_error, error_msg). payment_id is None if the msg_id was
    already ingested (ON CONFLICT DO NOTHING - no new notification in that
    case, since it would have already fired on the original insert)."""
    is_faulty = 'FAULTY' in s3_key.upper()
    conn = _get_db()
    ref = _get_reference_data()

    existing_uetrs = []
    if parsed.get('uetr'):
        with conn.cursor() as cur:
            cur.execute("SELECT uetr FROM payments WHERE uetr = %s", (parsed['uetr'],))
            existing_uetrs = [row[0] for row in cur.fetchall()]

    hits = error_rules.detect_errors(
        parsed,
        known_bics=ref['known_bics'],
        watchlist=ref['watchlist'],
        closed_accounts=ref['closed_accounts'],
        existing_uetrs=existing_uetrs,
    )
    error_msg = error_rules.format_error_msg(hits)
    has_error = bool(hits)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payments (
                s3_key, msg_id, uetr, instr_id, e2e_id,
                amount, currency, settlement_date,
                sender_bic, receiver_bic, debtor_bic, creditor_bic,
                debtor_name, debtor_iban, creditor_name, creditor_iban,
                is_faulty, raw_xml, has_error, error_msg
            ) VALUES (
                %(s3_key)s, %(msg_id)s, %(uetr)s, %(instr_id)s, %(e2e_id)s,
                %(amount)s, %(currency)s, %(settlement_date)s,
                %(sender_bic)s, %(receiver_bic)s, %(debtor_bic)s, %(creditor_bic)s,
                %(debtor_name)s, %(debtor_iban)s, %(creditor_name)s, %(creditor_iban)s,
                %(is_faulty)s, %(raw_xml)s, %(has_error)s, %(error_msg)s
            )
            ON CONFLICT (msg_id) DO NOTHING
            RETURNING id
        """, {
            **parsed, 's3_key': s3_key, 'is_faulty': is_faulty, 'raw_xml': raw_xml,
            'has_error': has_error, 'error_msg': error_msg,
        })
        row = cur.fetchone()
        payment_id = row[0] if row else None
    conn.commit()
    return payment_id, has_error, error_msg, hits


def _notify_backend_exceptions(msg_id: str, uetr: str, hits: list):
    """Fire-and-forget POST to backend /api/ingest/exceptions with structured error hits."""
    backend_url = os.environ.get("BACKEND_URL", "")
    if not backend_url or not hits:
        return
    try:
        detected = [{"code": h.code, "field": "", "value": h.message} for h in hits]
        payload = json.dumps({"msg_id": msg_id, "uetr": uetr, "detected_errors": detected}).encode("utf-8")
        req = urllib.request.Request(
            f"{backend_url}/api/ingest/exceptions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
        logger.info("Notified backend of exception: msg_id=%s errors=%s", msg_id, [h.code for h in hits])
    except Exception as exc:
        logger.warning("Backend exception notification failed (non-fatal): %s", exc)


def _upsert_exception(payment_id: int, msg_id: str, uetr: str, hits: list) -> None:
    conn = _get_db()
    detected = [{"code": h.code, "field": "", "value": h.message} for h in hits]
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO exceptions (msg_id, uetr, detected_errors, payment_id, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (msg_id) DO UPDATE SET
                detected_errors = EXCLUDED.detected_errors,
                payment_id = EXCLUDED.payment_id,
                updated_at = NOW()
        """, (msg_id, uetr, json.dumps(detected), payment_id))
    conn.commit()


def lambda_handler(event, context):
    s3 = boto3.client('s3')
    processed = failed = skipped = 0

    for sqs_record in event.get('Records', []):
        try:
            body = json.loads(sqs_record['body'])
        except (json.JSONDecodeError, KeyError):
            logger.warning("unparseable SQS record: %s", sqs_record.get('body', '')[:200])
            skipped += 1
            continue

        for s3_event in body.get('Records', []):
            if not s3_event.get('eventName', '').startswith('ObjectCreated'):
                skipped += 1
                continue
            bucket = s3_event['s3']['bucket']['name']
            key = s3_event['s3']['object']['key']
            if not key.endswith('.xml'):
                logger.info("skipping non-xml key: %s", key)
                skipped += 1
                continue
            try:
                obj = s3.get_object(Bucket=bucket, Key=key)
                raw_xml = obj['Body'].read().decode('utf-8')
                parsed = _parse_pacs008(raw_xml)
                payment_id, has_error, error_msg, hits = _ingest_record(key, parsed, raw_xml)
                logger.info(
                    "ingested %s (msg_id=%s, payment_id=%s, has_error=%s)",
                    key, parsed.get('msg_id'), payment_id, has_error,
                )
                if payment_id is not None and has_error:
                    notify_payment_error(payment_id, error_msg)
                    _upsert_exception(payment_id, parsed.get('msg_id', ''), parsed.get('uetr', ''), hits)
                    _notify_backend_exceptions(parsed.get('msg_id', ''), parsed.get('uetr', ''), hits)
                processed += 1
            except Exception as exc:
                logger.error("failed to ingest %s: %s", key, exc, exc_info=True)
                failed += 1

    return {
        'statusCode': 200,
        'body': json.dumps({'processed': processed, 'failed': failed, 'skipped': skipped}),
    }
