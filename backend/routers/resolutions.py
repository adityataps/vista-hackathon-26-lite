import json
import logging
import os
import re

from fastapi import APIRouter, HTTPException
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from db import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


def _inv_id_from_report(report_id: str) -> int:
    """Extract investigation DB id from 'RPT-0042' → 42."""
    try:
        return int(report_id.replace("RPT-", "").lstrip("0") or "0")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid report_id: {report_id}")


@router.post("/api/resolutions/{report_id}/approve")
def approve(report_id: str):
    inv_id = _inv_id_from_report(report_id)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="DB unavailable")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE investigations SET approval_status='approved' WHERE id=%s RETURNING exception_id",
            (inv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Investigation not found")
        cur.execute("UPDATE exceptions SET status='resolved' WHERE id=%s", (row[0],))
    conn.commit()
    logger.info("Investigation %s approved", inv_id)
    return {"status": "approved", "report_id": report_id}


@router.post("/api/resolutions/{report_id}/reject")
def reject(report_id: str):
    inv_id = _inv_id_from_report(report_id)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="DB unavailable")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE investigations SET approval_status='rejected' WHERE id=%s RETURNING exception_id",
            (inv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Investigation not found")
        cur.execute("UPDATE exceptions SET status='rejected' WHERE id=%s", (row[0],))
    conn.commit()
    logger.info("Investigation %s rejected", inv_id)
    return {"status": "rejected", "report_id": report_id}


class ChatRequest(BaseModel):
    message: str


CHAT_SYSTEM = """You are a payment investigation assistant. The analyst is reviewing a completed
investigation report and asking follow-up questions. Answer concisely using only information
from the investigation context provided. If you need to describe a tool call you would make,
prefix it with [calls <tool_name>]."""


@router.post("/api/reports/{report_id}/chat")
async def chat(report_id: str, body: ChatRequest):
    inv_id = _inv_id_from_report(report_id)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="DB unavailable")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT steps, findings, recommendation FROM investigations WHERE id=%s",
            (inv_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Investigation not found")

    steps, findings, recommendation = row

    context = (
        f"Investigation report for {report_id}:\n\n"
        f"Steps taken:\n{json.dumps(steps, indent=2)}\n\n"
        f"Findings:\n{json.dumps(findings, indent=2)}\n\n"
        f"Recommendation:\n{json.dumps(recommendation, indent=2)}"
    )

    llm = ChatBedrock(
        model_id="anthropic.claude-sonnet-4-6",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
    )
    response = await llm.ainvoke([
        SystemMessage(content=CHAT_SYSTEM),
        HumanMessage(content=f"Investigation context:\n{context}\n\nAnalyst question: {body.message}"),
    ])

    answer = response.content
    tool_used = None
    m = re.search(r"\[calls ([^\]]+)\]", answer)
    if m:
        tool_used = m.group(1)

    return {"answer": answer, "tool": tool_used}
