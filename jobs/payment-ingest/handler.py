"""Lambda handler: SQS-triggered pacs.008 XML ingest into Aurora via the RDS Data API.

SQS receives S3 ObjectCreated events for the payments/ prefix.
Downloads each XML from S3, parses key pacs.008 fields, runs business-error
checking, upserts into the payments table, writes/upserts an `exceptions` row
for faulty payments, and can optionally POST structured error hits to the
backend API.

Configuration (environment variables, set in infra/lambda.tf):
  DB_CLUSTER_ARN            - Aurora cluster ARN for the Data API (required)
  DB_SECRET_ARN             - Secrets Manager ARN with DB credentials (required)
  DB_NAME                   - Database name (required)
  REFERENCE_DATA_S3_URI     - s3://bucket/prefix/ for optional reference data
  ERROR_NOTIFY_ENDPOINT_URL - Optional backend endpoint for structured error POSTs
"""
import json
import logging
import os
import re
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import boto3
import defusedxml.ElementTree as ET

import error_rules
import reference_data
from notifier import notify_payment_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

HEAD_NS = 'urn:iso:std:iso:20022:tech:xsd:head.001.001.02'
PACS_NS = 'urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08'

_db_ready = False
_data_api_client: Any = None
_ref_data = None


class DataAPIConnection:
    def __init__(self, resource_arn: str, secret_arn: str, database: str):
        self.resource_arn = resource_arn
        self.secret_arn = secret_arn
        self.database = database
        self.autocommit = False
        self.closed = False
        self._transaction_id: str | None = None

    def cursor(self):
        return DataAPICursor(self)

    def commit(self):
        if self._transaction_id:
            _data_api().commit_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self._transaction_id,
            )
            self._transaction_id = None

    def rollback(self):
        if self._transaction_id:
            _data_api().rollback_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self._transaction_id,
            )
            self._transaction_id = None

    def _begin_transaction(self):
        if self._transaction_id is None:
            response = _data_api().begin_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                database=self.database,
            )
            self._transaction_id = response["transactionId"]

    def _execute(self, sql: str, parameters: list[dict] | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "resourceArn": self.resource_arn,
            "secretArn": self.secret_arn,
            "database": self.database,
            "sql": sql,
            "includeResultMetadata": True,
        }
        if parameters:
            kwargs["parameters"] = parameters
        if not self.autocommit:
            self._begin_transaction()
            kwargs["transactionId"] = self._transaction_id
        return _data_api().execute_statement(**kwargs)


class DataAPICursor:
    def __init__(self, conn: DataAPIConnection):
        self.conn = conn
        self._rows: list[tuple] = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple | list | dict | None = None):
        translated_sql, translated_params = _translate_sql(sql, params)
        response = self.conn._execute(translated_sql, translated_params)
        self._rows = _decode_rows(response)
        self.rowcount = len(self._rows) if self._rows else response.get("numberOfRecordsUpdated", 0)
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _data_api():
    global _data_api_client
    if _data_api_client is None:
        _data_api_client = boto3.client(
            "rds-data",
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _data_api_client


def _db_settings() -> tuple[str, str, str] | None:
    resource_arn = os.environ.get("DB_CLUSTER_ARN", "")
    secret_arn = os.environ.get("DB_SECRET_ARN", "")
    database = os.environ.get("DB_NAME", "")
    if not resource_arn or not secret_arn or not database:
        return None
    return resource_arn, secret_arn, database


def _translate_sql(sql: str, params=None) -> tuple[str, list[dict]]:
    if params is None:
        return sql, []
    if isinstance(params, dict):
        used = []
        seen = set()
        for match in re.finditer(r"%\(([^)]+)\)s", sql):
            key = match.group(1)
            if key not in seen:
                seen.add(key)
                used.append(key)
        translated = re.sub(r"%\(([^)]+)\)s", lambda m: f":{m.group(1)}", sql)
        return translated, [_to_data_api_param(key, params[key]) for key in used]
    values = list(params) if isinstance(params, (list, tuple)) else [params]
    index = 0

    def repl(_match):
        nonlocal index
        index += 1
        return f":p{index}"

    translated = re.sub(r"%s", repl, sql)
    return translated, [_to_data_api_param(f"p{i + 1}", value) for i, value in enumerate(values)]


def _to_data_api_param(name: str, value: Any) -> dict[str, Any]:
    param: dict[str, Any] = {"name": name}
    if value is None:
        param["value"] = {"isNull": True}
    elif isinstance(value, bool):
        param["value"] = {"booleanValue": value}
    elif isinstance(value, int) and not isinstance(value, bool):
        param["value"] = {"longValue": value}
    elif isinstance(value, Decimal):
        param["value"] = {"stringValue": format(value, "f")}
        param["typeHint"] = "DECIMAL"
    elif isinstance(value, float):
        param["value"] = {"doubleValue": value}
    elif isinstance(value, datetime):
        param["value"] = {"stringValue": value.isoformat(sep=" ", timespec="seconds")}
        param["typeHint"] = "TIMESTAMP"
    elif isinstance(value, date):
        param["value"] = {"stringValue": value.isoformat()}
        param["typeHint"] = "DATE"
    elif isinstance(value, (dict, list)):
        param["value"] = {"stringValue": json.dumps(value)}
        param["typeHint"] = "JSON"
    else:
        param["value"] = {"stringValue": str(value)}
    return param


def _decode_rows(response: dict[str, Any]) -> list[tuple]:
    metadata = response.get("columnMetadata") or []
    type_names = [column.get("typeName", "") for column in metadata]
    rows = []
    for record in response.get("records", []):
        rows.append(tuple(_decode_field(field, type_names[idx] if idx < len(type_names) else "") for idx, field in enumerate(record)))
    return rows


def _decode_field(field: dict[str, Any], type_name: str) -> Any:
    if field.get("isNull"):
        return None
    if "stringValue" in field:
        value = field["stringValue"]
        normalized = type_name.lower()
        if normalized in {"json", "jsonb"}:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        if normalized == "date":
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        if normalized in {"timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone"}:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
        if normalized in {"numeric", "decimal"}:
            try:
                return Decimal(value)
            except Exception:
                return value
        return value
    if "longValue" in field:
        return field["longValue"]
    if "doubleValue" in field:
        return field["doubleValue"]
    if "booleanValue" in field:
        return field["booleanValue"]
    return None


def _get_db():
    global _db_ready
    settings = _db_settings()
    if not settings:
        raise RuntimeError("DB_CLUSTER_ARN, DB_SECRET_ARN, and DB_NAME are required")
    conn = DataAPIConnection(*settings)
    if not _db_ready:
        _ensure_schema(conn)
        _db_ready = True
    return conn


def _ensure_schema(conn):
    old_autocommit = conn.autocommit
    conn.autocommit = True
    statements = [
        """
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
        """,
        """
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_payment_events_uetr ON payment_events(uetr)",
        "CREATE INDEX IF NOT EXISTS idx_payment_events_msg_id ON payment_events(msg_id)",
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS has_error BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS error_msg TEXT",
        "CREATE INDEX IF NOT EXISTS idx_payments_has_error ON payments (has_error)",
        """
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
        """,
    ]
    for statement in statements:
        with conn.cursor() as cur:
            cur.execute(statement)
    conn.autocommit = old_autocommit


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
        'creditor_ctry':    tx(f'{tx_path}/{p}Cdtr/{p}PstlAdr/{p}Ctry'),
        'creditor_twn_nm':  tx(f'{tx_path}/{p}Cdtr/{p}PstlAdr/{p}TwnNm'),
        'creditor_strt_nm': tx(f'{tx_path}/{p}Cdtr/{p}PstlAdr/{p}StrtNm'),
    }


def _get_reference_data():
    global _ref_data
    if _ref_data is None:
        _ref_data = reference_data.load_reference_data()
    return _ref_data


def _ingest_record(s3_key, parsed, raw_xml):
    """Runs error detection, upserts the row, and returns payment details."""
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
    backend_url = os.environ.get("ERROR_NOTIFY_ENDPOINT_URL") or os.environ.get("BACKEND_URL", "")
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
        """, (msg_id, uetr, detected, payment_id))
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
