import json
import logging
import os
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger(__name__)

_schema_done = False
_data_api_client: Any = None

_NAMED_PARAM_RE = re.compile(r"%\(([^)]+)\)s")
_POSITIONAL_PARAM_RE = re.compile(r"%s")


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

    def close(self):
        try:
            self.rollback()
        finally:
            self.closed = True

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

    def execute(self, sql: str, params: Any = None):
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
    database = os.environ.get("DB_NAME", "payinvestigator")
    if not resource_arn or not secret_arn:
        return None
    return resource_arn, secret_arn, database


def _translate_sql(sql: str, params: Any = None) -> tuple[str, list[dict]]:
    if params is None:
        return sql, []

    if isinstance(params, dict):
        used_keys: list[str] = []
        seen: set[str] = set()

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in params:
                raise KeyError(f"Missing SQL parameter: {key}")
            if key not in seen:
                seen.add(key)
                used_keys.append(key)
            return f":{key}"

        translated = _NAMED_PARAM_RE.sub(replace, sql)
        return translated, [_to_data_api_param(key, params[key]) for key in used_keys]

    values = list(params) if isinstance(params, (list, tuple)) else [params]
    index = 0

    def replace(_match: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f":p{index}"

    translated = _POSITIONAL_PARAM_RE.sub(replace, sql)
    if index != len(values):
        raise ValueError(f"SQL placeholder mismatch: expected {index} params, got {len(values)}")
    return translated, [_to_data_api_param(f"p{i + 1}", value) for i, value in enumerate(values)]


def _to_data_api_param(name: str, value: Any) -> dict[str, Any]:
    param: dict[str, Any] = {"name": name}
    if value is None:
        param["value"] = {"isNull": True}
        return param

    if isinstance(value, bool):
        param["value"] = {"booleanValue": value}
        return param

    if isinstance(value, int) and not isinstance(value, bool):
        param["value"] = {"longValue": value}
        return param

    if isinstance(value, Decimal):
        param["value"] = {"stringValue": format(value, "f")}
        param["typeHint"] = "DECIMAL"
        return param

    if isinstance(value, float):
        param["value"] = {"doubleValue": value}
        return param

    if isinstance(value, datetime):
        param["value"] = {"stringValue": value.isoformat(sep=" ", timespec="seconds")}
        param["typeHint"] = "TIMESTAMP"
        return param

    if isinstance(value, date):
        param["value"] = {"stringValue": value.isoformat()}
        param["typeHint"] = "DATE"
        return param

    if isinstance(value, (dict, list)):
        param["value"] = {"stringValue": json.dumps(value)}
        param["typeHint"] = "JSON"
        return param

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
        if normalized in {"date"}:
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
    if "arrayValue" in field:
        array_value = field["arrayValue"]
        return [_decode_field(item, type_name) for item in array_value.get("stringValues", [])]
    return None


def get_db():
    global _schema_done
    settings = _db_settings()
    if not settings:
        return None
    conn = DataAPIConnection(*settings)
    if not _schema_done:
        try:
            _schema_done = _ensure_schema(conn)
        except Exception as exc:
            logger.warning("DB connect failed: %s", exc)
            return None
    return conn


def _ensure_schema(conn) -> bool:
    """Create/verify the schema. Retries transient failures (e.g. Aurora
    Serverless v2 cold-starting from 0 ACU on the very first connection).
    Returns True only if every statement ultimately succeeded, so callers
    can avoid permanently caching a half-applied schema as "done"."""
    old_autocommit = conn.autocommit
    conn.autocommit = True
    all_ok = True

    def _run(stmt: str, attempts: int = 3, delay: float = 2.0):
        nonlocal all_ok
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(delay)
        all_ok = False
        logger.warning("Schema stmt failed after retries: %.80s: %s", stmt.strip()[:80], last_exc)

    _run("CREATE EXTENSION IF NOT EXISTS vector")

    _run("""
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
    _run("CREATE INDEX IF NOT EXISTS idx_payment_events_uetr ON payment_events(uetr)")
    _run("CREATE INDEX IF NOT EXISTS idx_payment_events_msg_id ON payment_events(msg_id)")

    _run("""
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
            has_error       BOOLEAN NOT NULL DEFAULT FALSE,
            error_msg       TEXT,
            ingested_at     TIMESTAMP DEFAULT NOW()
        )
    """)
    _run("ALTER TABLE payments ADD COLUMN IF NOT EXISTS has_error BOOLEAN NOT NULL DEFAULT FALSE")
    _run("ALTER TABLE payments ADD COLUMN IF NOT EXISTS error_msg TEXT")

    _run("""
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
    _run("CREATE INDEX IF NOT EXISTS idx_exceptions_msg_id ON exceptions(msg_id)")

    _run("""
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

    _run("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_summary JSONB")
    _run("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_input_tokens INTEGER DEFAULT 0")
    _run("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS precheck_output_tokens INTEGER DEFAULT 0")
    _run("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommendation JSONB")
    _run("ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommended_sql TEXT")
    _run("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0")
    _run("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0")
    _run("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS report_content JSONB")

    _run("""
        CREATE TABLE IF NOT EXISTS investigation_steps (
            id        SERIAL PRIMARY KEY,
            inv_id    INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            seq       INTEGER NOT NULL,
            agent     TEXT,
            cls       TEXT,
            step_text TEXT,
            ts        TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    _run("CREATE INDEX IF NOT EXISTS idx_inv_steps ON investigation_steps(inv_id, seq)")

    _run("""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id          SERIAL PRIMARY KEY,
            doc_name    TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text  TEXT NOT NULL,
            embedding   vector(1024) NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (doc_name, chunk_index)
        )
    """)
    _run("CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc_name ON kb_chunks(doc_name)")
    _run(
        "CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding "
        "ON kb_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 8)"
    )

    _run("""
        CREATE TABLE IF NOT EXISTS bedrock_usage (
            usage_date DATE PRIMARY KEY,
            call_count INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.autocommit = old_autocommit
    return all_ok
