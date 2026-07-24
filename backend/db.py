import logging
import os

import psycopg2
from pgvector.psycopg2 import register_vector

logger = logging.getLogger(__name__)

_db_conn = None
_schema_done = False


def _connect_db():
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    register_vector(conn)
    return conn


def get_db():
    global _db_conn, _schema_done
    if not os.environ.get("DATABASE_URL"):
        return None
    try:
        if _db_conn is None or _db_conn.closed:
            _db_conn = _connect_db()
            _schema_done = False
        else:
            try:
                _db_conn.rollback()
            except Exception:
                try:
                    _db_conn.close()
                except Exception:
                    pass
                _db_conn = _connect_db()
                _schema_done = False
        if not _schema_done:
            _ensure_schema(_db_conn)
            _schema_done = True
    except Exception as exc:
        logger.warning("DB connect failed: %s", exc)
        return None
    return _db_conn


def _ensure_schema(conn):
    old_autocommit = conn.autocommit
    conn.autocommit = True

    def _run(stmt):
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
        except Exception as exc:
            logger.debug("Schema stmt skipped (%s): %.60s", type(exc).__name__, stmt.strip()[:60])

    _run("SET lock_timeout = '5s'")
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

    conn.autocommit = old_autocommit
