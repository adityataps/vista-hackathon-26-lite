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

    exception_id = None
    recommended_sql = None

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE investigations SET approval_status='approved' WHERE id=%s RETURNING exception_id",
            (inv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Investigation not found")
        exception_id = row[0]

        # Fetch the recommended SQL from the exception
        cur.execute(
            "SELECT recommended_sql FROM exceptions WHERE id=%s",
            (exception_id,)
        )
        sql_row = cur.fetchone()
        if sql_row and sql_row[0]:
            recommended_sql = sql_row[0]

        cur.execute("UPDATE exceptions SET status='resolved' WHERE id=%s", (exception_id,))

    # Execute the recommended SQL if it exists
    sql_execution_result = None
    if recommended_sql:
        try:
            with conn.cursor() as cur:
                cur.execute(recommended_sql)
                # Fetch result if it's a SELECT query
                sql_execution_result = {
                    "executed": True,
                    "rows_affected": cur.rowcount,
                    "message": "Recommended action executed successfully"
                }
            conn.commit()
            logger.info("Investigation %s approved and recommended SQL executed", inv_id)
        except Exception as exc:
            logger.error("Failed to execute recommended SQL for investigation %s: %s", inv_id, exc)
            sql_execution_result = {
                "executed": False,
                "error": str(exc),
                "message": "Failed to execute recommended action"
            }
    else:
        logger.info("Investigation %s approved (no recommended SQL)", inv_id)

    response = {"status": "approved", "report_id": report_id}
    if sql_execution_result:
        response["sql_execution"] = sql_execution_result

    return response


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
