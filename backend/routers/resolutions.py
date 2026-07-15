import json
import logging
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from db import get_db

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    _REPORTLAB = True
except ImportError:
    _REPORTLAB = False

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


# ── PDF Report ────────────────────────────────────────────────────────────────

def _build_pdf(d: dict) -> bytes:
    """Render a professional investigation report PDF using reportlab Platypus."""
    buf = BytesIO()
    W, _ = A4

    NAVY   = colors.HexColor("#0d2137")
    BLUE   = colors.HexColor("#1a56db")
    LBG    = colors.HexColor("#f5f7fa")   # light background
    BORDER = colors.HexColor("#d1d5db")
    TEXT   = colors.HexColor("#111827")
    MUTED  = colors.HexColor("#6b7280")
    GREEN  = colors.HexColor("#059669")
    RED    = colors.HexColor("#dc2626")
    YELLOW = colors.HexColor("#d97706")

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    s_white_bold = ps("wb", fontSize=14, fontName="Helvetica-Bold", textColor=colors.white, leading=20)
    s_white_sub  = ps("ws", fontSize=9,  fontName="Helvetica", textColor=colors.HexColor("#93c5fd"), leading=13)
    s_h2         = ps("h2", fontSize=11, fontName="Helvetica-Bold", textColor=BLUE,
                       spaceBefore=10, spaceAfter=3)
    s_body       = ps("bd", fontSize=10, fontName="Helvetica", textColor=TEXT, leading=15, spaceAfter=4)
    s_fact       = ps("fc", fontSize=10, fontName="Helvetica", textColor=TEXT, leading=14,
                       leftIndent=8, spaceAfter=2)
    s_muted      = ps("mu", fontSize=9,  fontName="Helvetica", textColor=MUTED, leading=13)
    s_footer     = ps("ft", fontSize=8,  fontName="Helvetica", textColor=MUTED,
                       alignment=TA_CENTER, leading=12)
    s_code       = ps("co", fontSize=9,  fontName="Courier", textColor=TEXT, leading=13)

    rc           = d.get("report_content") or {}
    payment      = d.get("payment") or {}
    rec          = d.get("recommendation") or {}
    approval     = d.get("approval_status") or "pending"
    report_id    = d.get("report_id", "RPT-????")
    risk_level   = rc.get("risk_level", "MEDIUM")
    error_codes  = d.get("error_codes") or []
    completed_at = d.get("completed_at") or "N/A"
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    risk_color = {"HIGH": "#dc2626", "MEDIUM": "#d97706", "LOW": "#059669"}.get(risk_level, "#6b7280")
    dec_label  = {"approved": "✓  APPROVED", "rejected": "✗  REJECTED", "pending": "⏳  AWAITING APPROVAL"}.get(approval, "PENDING")
    dec_hex    = {"approved": "#059669", "rejected": "#dc2626", "pending": "#d97706"}.get(approval, "#6b7280")

    content_w = W - 36 * mm

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title=f"{report_id} — PayInvestigator",
        author="PayInvestigator AI",
    )

    story = []

    # ── Header banner ────────────────────────────────────────────────────────
    hdr = Table(
        [[
            Paragraph("⚡  PayInvestigator", s_white_bold),
            Paragraph(f"<font color='{risk_color}'><b>RISK: {risk_level}</b></font>",
                      ps("rh", fontSize=11, fontName="Helvetica-Bold", textColor=colors.white,
                         alignment=TA_RIGHT, leading=16)),
        ], [
            Paragraph("PAYMENT EXCEPTION INVESTIGATION REPORT", s_white_sub),
            Paragraph("CONFIDENTIAL",
                      ps("cf", fontSize=8, fontName="Helvetica-Bold",
                         textColor=colors.HexColor("#93c5fd"), alignment=TA_RIGHT, leading=12)),
        ]],
        colWidths=[content_w * 0.70, content_w * 0.30],
    )
    hdr.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 5))

    # ── Metadata strip ───────────────────────────────────────────────────────
    meta = Table(
        [[
            Paragraph(f"<b>Report ID</b><br/>{report_id}", s_muted),
            Paragraph(f"<b>Generated</b><br/>{generated_at}", s_muted),
            Paragraph(f"<b>Investigation Completed</b><br/>{completed_at[:19] if len(completed_at) > 19 else completed_at}", s_muted),
            Paragraph(f"<b>Decision</b><br/><font color='{dec_hex}'><b>{dec_label}</b></font>", s_muted),
        ]],
        colWidths=[content_w / 4] * 4,
    )
    meta.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), LBG),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    story.append(meta)
    story.append(Spacer(1, 12))

    def section(title, content_paragraphs):
        story.append(Paragraph(title, s_h2))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=5))
        for p in content_paragraphs:
            story.append(p)
        story.append(Spacer(1, 6))

    # ── Executive Summary ────────────────────────────────────────────────────
    section("EXECUTIVE SUMMARY", [
        Paragraph(rc.get("executive_summary", "See findings below."), s_body),
    ])

    # ── Payment Details ──────────────────────────────────────────────────────
    def row(label, value):
        return [
            Paragraph(label, ps("lbl", fontSize=9, fontName="Helvetica-Bold", textColor=MUTED, leading=13)),
            Paragraph(str(value) if value else "—", s_code),
        ]

    cw = [content_w * 0.28, content_w * 0.72]
    pay_tbl = Table(
        [
            row("Transaction ID",   d.get("tx_id", "—")),
            row("MSG ID",           payment.get("msg_id")),
            row("UETR",             payment.get("uetr")),
            row("Amount",           f"{payment.get('amount', '—')} {payment.get('currency', '')}".strip()),
            row("Debtor (name)",    payment.get("debtor_name")),
            row("Debtor IBAN",      payment.get("debtor_iban")),
            row("Creditor (name)",  payment.get("creditor_name")),
            row("Creditor IBAN",    payment.get("creditor_iban")),
            row("Sender BIC",       payment.get("sender_bic")),
            row("Receiver BIC",     payment.get("receiver_bic")),
            row("Settlement Date",  d.get("settlement_date")),
        ],
        colWidths=cw,
    )
    pay_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), LBG),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, LBG]),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.3, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story.append(Paragraph("PAYMENT DETAILS", s_h2))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=5))
    story.append(pay_tbl)
    story.append(Spacer(1, 10))

    # ── Exception Classification ─────────────────────────────────────────────
    exc_tbl = Table(
        [
            row("Error Code(s)",    ", ".join(error_codes) if error_codes else "—"),
            row("Exception Type",   d.get("exception_type", "—")),
        ],
        colWidths=cw,
    )
    exc_tbl.setStyle(TableStyle([
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, LBG]),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.3, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story.append(Paragraph("EXCEPTION CLASSIFICATION", s_h2))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=5))
    story.append(exc_tbl)
    story.append(Spacer(1, 10))

    # ── Investigation Findings ───────────────────────────────────────────────
    section("INVESTIGATION FINDINGS", [
        Paragraph(rc.get("exception_narrative", "—"), s_body),
        Paragraph(rc.get("findings_narrative", "—"), s_body),
    ])

    # ── Key Facts ────────────────────────────────────────────────────────────
    facts = rc.get("key_facts") or []
    if facts:
        section("KEY FACTS", [Paragraph(f"• {f}", s_fact) for f in facts])

    # ── AI Recommendation ────────────────────────────────────────────────────
    try:
        conf_pct = f"{float(rec.get('confidence', 0)):.0%}"
    except (TypeError, ValueError):
        conf_pct = "N/A"

    rec_tbl = Table(
        [
            row("Recommended Action", rec.get("action")),
            row("Rationale",          rec.get("rationale")),
            row("Confidence",         conf_pct),
            row("Human Approval",     "Required — no action executed without analyst decision"),
        ],
        colWidths=cw,
    )
    rec_tbl.setStyle(TableStyle([
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, LBG]),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.3, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story.append(Paragraph("AI RECOMMENDATION", s_h2))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=5))
    story.append(rec_tbl)
    story.append(Spacer(1, 10))

    # ── Resolution ───────────────────────────────────────────────────────────
    section("RESOLUTION", [
        Paragraph(
            f"<font color='{dec_hex}'><b>{dec_label}</b></font>",
            ps("dec", fontSize=11, fontName="Helvetica-Bold", textColor=TEXT, leading=16),
        ),
        Paragraph(rc.get("recommendation_narrative", "—"), s_body),
    ])

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"This report was produced by <b>PayInvestigator</b> (Finastra Global PAYplus AI layer). "
        f"AI investigation provides decision support only — human approval is required before any "
        f"payment action is executed. "
        f"Report ID: {report_id}  |  Generated: {generated_at}",
        s_footer,
    ))

    doc.build(story)
    return buf.getvalue()


@router.get("/api/reports/{report_id}/pdf")
def download_pdf(report_id: str):
    if not _REPORTLAB:
        raise HTTPException(status_code=501, detail="PDF generation unavailable (reportlab not installed)")

    inv_id = _inv_id_from_report(report_id)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="DB unavailable")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                i.recommendation, i.approval_status, i.completed_at, i.report_content,
                e.detected_errors,
                p.id AS payment_db_id,
                p.msg_id, p.uetr, p.amount, p.currency, p.settlement_date,
                p.debtor_name, p.debtor_iban, p.creditor_name, p.creditor_iban,
                p.sender_bic, p.receiver_bic
            FROM investigations i
            JOIN exceptions e ON e.id = i.exception_id
            LEFT JOIN payments p ON p.msg_id = e.msg_id
            WHERE i.id = %s
        """, (inv_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Investigation not found")

    (rec, approval_status, completed_at, report_content,
     detected_errors, payment_db_id,
     msg_id, uetr, amount, currency, settlement_date,
     debtor_name, debtor_iban, creditor_name, creditor_iban,
     sender_bic, receiver_bic) = row

    errors = detected_errors if isinstance(detected_errors, list) else []
    error_codes = [e.get("code") for e in errors if e.get("code")]

    # Derive exception type label (mirrors routers/exceptions.py)
    ERROR_TYPE_MAP = {
        "IBAN_INVALID_CHECKSUM":       "Bad IBAN",
        "IBAN_WRONG_LENGTH":           "Bad IBAN",
        "BIC_IBAN_COUNTRY_MISMATCH":   "Bad IBAN",
        "BIC_INVALID_COUNTRY":         "Bad IBAN",
        "BENEFICIARY_NAME_INCOMPLETE": "ISO 20022 field",
        "ADDRESS_INCOMPLETE":          "ISO 20022 field",
        "DUPLICATE_UETR":              "Duplicate ref",
        "XCHG_RATE_INCONSISTENT":      "FX limit breach",
    }
    exception_type = ERROR_TYPE_MAP.get(error_codes[0], "Unknown") if error_codes else "Unknown"

    tx_id = f"TX-{payment_db_id:05d}" if payment_db_id else msg_id

    inv_data = {
        "report_id":      report_id,
        "tx_id":          tx_id,
        "approval_status": approval_status or "pending",
        "completed_at":   completed_at.isoformat() if completed_at else "N/A",
        "settlement_date": settlement_date.isoformat() if settlement_date else None,
        "report_content": report_content if isinstance(report_content, dict) else {},
        "recommendation": rec if isinstance(rec, dict) else {},
        "error_codes":    error_codes,
        "exception_type": exception_type,
        "payment": {
            "msg_id":        msg_id,
            "uetr":          uetr,
            "amount":        str(amount) if amount else "—",
            "currency":      currency or "",
            "debtor_name":   debtor_name or "—",
            "debtor_iban":   debtor_iban or "—",
            "creditor_name": creditor_name or "—",
            "creditor_iban": creditor_iban or "—",
            "sender_bic":    sender_bic or "—",
            "receiver_bic":  receiver_bic or "—",
        },
    }

    try:
        pdf_bytes = _build_pdf(inv_data)
    except Exception as exc:
        logger.error("PDF generation failed for %s: %s", report_id, exc)
        raise HTTPException(status_code=500, detail=f"PDF generation error: {exc}")

    filename = f"{report_id}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
