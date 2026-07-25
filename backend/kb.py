import json
import logging
import os
from pathlib import Path
from typing import Any

import boto3

from bedrock_guard import BedrockDailyLimitExceeded, check_and_increment_bedrock_usage
from db import get_db

logger = logging.getLogger(__name__)

_EMBEDDING_DIMENSIONS = 1024
_EMBEDDING_MODEL_ID = os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")

_bedrock_runtime: Any = None


def _bedrock():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _bedrock_runtime


def _resolve_docs_dir() -> Path | None:
    base = Path(__file__).resolve().parent
    candidates = [
        base / "infra" / "assets",
        base.parent / "infra" / "assets",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def chunk_markdown(text: str, max_chars: int = 1400) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)

        if len(paragraph) <= max_chars:
            current = paragraph
            continue

        words = paragraph.split()
        current = ""
        for word in words:
            word_candidate = word if not current else f"{current} {word}"
            if len(word_candidate) <= max_chars:
                current = word_candidate
            else:
                chunks.append(current)
                current = word

    if current:
        chunks.append(current)
    return chunks


def _embed_text(text: str) -> list[float]:
    check_and_increment_bedrock_usage()
    response = _bedrock().invoke_model(
        modelId=_EMBEDDING_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "inputText": text,
                "dimensions": _EMBEDDING_DIMENSIONS,
                "normalize": True,
            }
        ),
    )
    payload = json.loads(response["body"].read())
    embedding = payload.get("embedding") or payload.get("embeddingsByType", {}).get("float")
    if not embedding:
        raise ValueError("Titan embedding response did not include an embedding vector")
    return embedding


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.12g}" for value in values) + "]"


def _table_has_rows(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM kb_chunks LIMIT 1)")
        return bool(cur.fetchone()[0])


def seed_knowledge_base(conn=None) -> dict[str, int | list[str] | str]:
    conn = conn or get_db()
    if not conn:
        return {"seeded_docs": 0, "skipped_docs": 0, "docs": [], "status": "db-unavailable"}

    docs_dir = _resolve_docs_dir()
    if docs_dir is None:
        logger.warning("Knowledge-base docs directory not found")
        return {"seeded_docs": 0, "skipped_docs": 0, "docs": [], "status": "docs-missing"}

    seeded_docs = 0
    skipped_docs = 0
    processed_docs: list[str] = []

    for doc_path in sorted(docs_dir.glob("*.md")):
        doc_name = doc_path.name
        processed_docs.append(doc_name)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM kb_chunks WHERE doc_name = %s", (doc_name,))
            existing = cur.fetchone()[0]
        if existing:
            skipped_docs += 1
            continue

        text = doc_path.read_text(encoding="utf-8")
        chunks = chunk_markdown(text)
        if not chunks:
            skipped_docs += 1
            continue

        try:
            with conn.cursor() as cur:
                for index, chunk in enumerate(chunks):
                    embedding = _embed_text(chunk)
                    cur.execute(
                        """
                        INSERT INTO kb_chunks (doc_name, chunk_index, chunk_text, embedding)
                        VALUES (%s, %s, %s, CAST(%s AS vector))
                        ON CONFLICT (doc_name, chunk_index)
                        DO UPDATE SET chunk_text = EXCLUDED.chunk_text, embedding = EXCLUDED.embedding
                        """,
                        (doc_name, index, chunk, _vector_literal(embedding)),
                    )
            conn.commit()
            seeded_docs += 1
        except BedrockDailyLimitExceeded:
            raise
        except Exception as exc:
            conn.rollback()
            logger.warning("KB seed failed for %s: %s", doc_name, exc)

    return {
        "seeded_docs": seeded_docs,
        "skipped_docs": skipped_docs,
        "docs": processed_docs,
        "status": "ok",
    }


def search_chunks(query: str, limit: int = 5) -> list[dict[str, Any]]:
    conn = get_db()
    if not conn:
        return []

    if not _table_has_rows(conn):
        seed_knowledge_base(conn)

    query_vector = _vector_literal(_embed_text(query))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT doc_name, chunk_text, 1 - (embedding <=> CAST(%s AS vector)) AS score
            FROM kb_chunks
            ORDER BY embedding <=> CAST(%s AS vector)
            LIMIT %s
            """,
            (query_vector, query_vector, limit),
        )
        rows = cur.fetchall()

    return [
        {
            "content": chunk_text,
            "score": round(float(score or 0.0), 4),
            "source": doc_name,
        }
        for doc_name, chunk_text, score in rows
    ]
