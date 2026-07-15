import json
import logging

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
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

    conn.commit()  # always commit the status updates before attempting optional SQL

    # Execute the recommended SQL if it exists
    sql_execution_result = None
    if recommended_sql:
        try:
            with conn.cursor() as cur:
                cur.execute(recommended_sql)
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
investigation report and asking follow-up questions. Answer concisely using the investigation
context provided. When you need to look up policy documents, compliance rules, SWIFT guidelines,
or error resolution procedures, call the search_knowledge_base tool."""

_KNOWN_TOOLS = {"search_knowledge_base"}


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

    from main import get_llm
    from agents.tools.knowledge_base_tool import search_knowledge_base

    llm = get_llm().bind_tools([search_knowledge_base])

    messages = [
        SystemMessage(content=CHAT_SYSTEM),
        HumanMessage(content=f"Investigation context:\n{context}\n\nAnalyst question: {body.message}"),
    ]

    tool_used = None
    response = None

    for _ in range(3):
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tc in response.tool_calls:
            if tc["name"] in _KNOWN_TOOLS:
                tool_used = tc["name"]
                result = search_knowledge_base.invoke(tc["args"])
            else:
                result = json.dumps({"error": f"Unknown tool: {tc['name']}"})
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

    answer = response.content if response else ""
    if isinstance(answer, list):
        answer = " ".join(b.get("text", "") for b in answer if isinstance(b, dict))

    return {"answer": answer, "tool": tool_used}
