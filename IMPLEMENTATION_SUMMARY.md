# Implementation Summary: Agent Recommendations & SQL Approval

## Overview

The PayInvestigator system now supports storing and executing agent-recommended SQL statements when a human analyst approves a recommendation.

## What Changed

### 1. Database Schema
**File:** `backend/db.py`

Two new columns added to the `exceptions` table via `ALTER TABLE`:
```python
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommendation JSONB;
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommended_sql TEXT;
```

### 2. Resolution Agent Prompt
**File:** `backend/agents/nodes/resolution.py`

Updated `SYSTEM` prompt to guide Claude to optionally include SQL statements:
- Agent can recommend safe, straightforward payment corrections
- SQL only included when fix is unambiguous and safe
- Agent includes examples (IBAN fixes, duplicate cancellations, etc.)
- `sql` field is **optional** — agent omits it if human review is safer

### 3. Investigation Completion
**File:** `backend/routers/exceptions.py`

When investigations finish:
1. Full recommendation (JSONB) stored in `exceptions.recommendation`
2. SQL extracted from `recommendation.sql` and stored in `exceptions.recommended_sql`
3. Exception status set to `'awaiting_approval'`
4. Frontend can now display recommended action + optional SQL preview

### 4. Approval & Execution
**File:** `backend/routers/resolutions.py`

Enhanced `POST /api/resolutions/{report_id}/approve` endpoint:
1. Fetches the `recommended_sql` from the `exceptions` table
2. Executes it in a database transaction (if present)
3. Returns execution status to the frontend:
   ```json
   {
     "status": "approved",
     "report_id": "RPT-0042",
     "sql_execution": {
       "executed": true,
       "rows_affected": 1,
       "message": "Recommended action executed successfully"
     }
   }
   ```

---

## Data Flow

```
┌─────────────────┐
│  Exception      │
│  Detected       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Investigation  │
│  Runs           │
│  (Multi-agent)  │
└────────┬────────┘
         │
         ▼
┌──────────────────────┐
│  Resolution Agent    │  ← Updated: can include "sql" in recommendation
│  Synthesizes         │
│  Findings            │
└────────┬─────────────┘
         │
         ▼
┌────────────────────────────────────┐
│  Store in DB                       │
│  • exceptions.recommendation (JSON)│
│  • exceptions.recommended_sql      │  ← NEW
│  • exceptions.status = 'awaiting'  │
└────────┬───────────────────────────┘
         │
         ▼
┌────────────────────────────────────┐
│  Frontend displays:                │
│  [Analyst reviews recommendation]  │
│  [Optional: preview recommended    │
│   SQL before approval]             │
└────────┬───────────────────────────┘
         │
         ▼
    [User clicks APPROVE]
         │
         ▼
┌─────────────────────────────────────┐
│  POST /api/resolutions/RPT-{id}/    │
│  approve                            │
│                                     │
│  1. Fetch recommended_sql           │
│  2. BEGIN transaction               │
│  3. Execute SQL                     │
│  4. Fetch result status             │
│  5. COMMIT or ROLLBACK              │
│  6. Return execution report         │  ← NEW
└─────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────┐
│  Frontend displays result:                │
│  ✓ "Successfully applied: UPDATE 1 row"  │
│  ✗ "Failed: SQL error message"           │
└───────────────────────────────────────────┘
```

---

## Example Scenarios

### Scenario 1: IBAN Typo Detection & Auto-Fix

**Investigation finds:** Debtor IBAN has invalid checksum (detected: `DE12345678901234567890` → actual: `DE89370400440532013000`)

**Agent recommendation:**
```json
{
  "action": "Correct the debtor IBAN to valid checksum and release payment",
  "rationale": "IBAN validation tool confirmed correct IBAN. Payment data matches beneficiary records.",
  "confidence": 0.95,
  "requires_human_approval": true,
  "sql": "UPDATE payments SET debtor_iban='DE89370400440532013000' WHERE msg_id='MSG-12345' AND status NOT IN ('completed','cancelled')"
}
```

**User action:** Clicks "Approve"  
**Result:** 
```json
{
  "status": "approved",
  "sql_execution": {
    "executed": true,
    "rows_affected": 1,
    "message": "Recommended action executed successfully"
  }
}
```

### Scenario 2: Duplicate Payment (No Safe SQL)

**Investigation finds:** Payment MSG-12346 is identical to MSG-12345, submitted twice

**Agent recommendation:**
```json
{
  "action": "Cancel the duplicate payment (MSG-12346) — original payment MSG-12345 already approved for settlement",
  "rationale": "Technical analysis confirmed identical UETR, amount, and parties. Temporal analysis shows MSG-12346 submitted 2 minutes later.",
  "confidence": 0.98,
  "requires_human_approval": true
  // NO sql field — analyst must manually verify which to keep
}
```

**User action:** Clicks "Approve" → must manually cancel/reject via payment system  
**No auto-execution** because analyst needs to interact with payment workflow

### Scenario 3: Sanctions Screening (Conditional Fix)

**Investigation finds:** Beneficiary name partially matches SDN list. Compliance Agent verified: no actual match (false positive)

**Agent recommendation:**
```json
{
  "action": "Release payment — compliance cleared after detailed beneficiary verification",
  "rationale": "Initial SDN hit was name similarity only. Compliance Agent cross-referenced beneficiary ID, address, and registry. Not matched on any sanctioned entity.",
  "confidence": 0.92,
  "requires_human_approval": true,
  "sql": "UPDATE payments SET has_error=false, error_msg=NULL WHERE msg_id='MSG-12348' AND has_error=true"
}
```

**User action:** Clicks "Approve"  
**Result:** Payment is unmarked as faulty and proceeds to settlement

---

## Files Modified

| File | Changes |
|------|---------|
| `backend/db.py` | Added 2 ALTER TABLE statements for new columns |
| `backend/agents/nodes/resolution.py` | Updated SYSTEM prompt + example SQL patterns |
| `backend/routers/exceptions.py` | Populate `recommendation` and `recommended_sql` on investigation completion; include in list response |
| `backend/routers/resolutions.py` | Execute `recommended_sql` on approval; return execution status |

## Files Created (Documentation)

| File | Purpose |
|------|---------|
| `SCHEMA_UPDATES.md` | Detailed schema changes, workflow, and integration guide |
| `SQL_PATTERNS.md` | Common SQL patterns for different exception types + safety guidelines |

---

## Testing Checklist

Before pushing to production:

- [ ] Run database migrations (psycopg2 will auto-create columns on startup)
- [ ] Test in dev: seed exceptions → investigate → review recommendation → approve with SQL
- [ ] Verify SQL executes correctly and updates the target payment
- [ ] Check logs for any SQL execution errors
- [ ] Verify response includes `sql_execution` status
- [ ] Test rejection flow (should NOT execute SQL)
- [ ] Test investigation with NO recommended_sql (agent omits unsafe fixes)
- [ ] Load test: ensure SQL execution doesn't block other investigations
- [ ] Audit trail: verify all approvals + executions logged

---

## Rollback Plan

If SQL execution causes issues:

1. **Immediate:** Disable SQL execution by removing the approval logic (revert `resolutions.py`)
2. **Investigation:** Check PostgreSQL logs for failed SQL; identify root cause
3. **Recovery:** Restore from backup or manually execute inverse SQL
4. **Fix:** Update agent prompt to be more conservative about recommending SQL
5. **Re-deployment:** Test thoroughly in staging before re-enabling

---

## Future Enhancements

- [ ] **SQL validation:** Parse + validate agent's SQL before storing (use `sqlparse`)
- [ ] **Whitelisting:** Only allow certain table/column combinations for UPDATE
- [ ] **Sandboxing:** Execute SQL in read-only replica first, then apply to main DB
- [ ] **Dry-run:** Show analyst impact (row preview) before approval
- [ ] **Audit logging:** Track all SQL executions in a separate `audit_log` table
- [ ] **Notifications:** Send alert when agent recommends SQL (extra approval step?)
- [ ] **Batch approval:** Allow approving multiple exceptions' SQL in a single transaction

---

## Questions?

Refer to:
- `SCHEMA_UPDATES.md` — Technical implementation details
- `SQL_PATTERNS.md` — Common scenarios and SQL examples
- `CLAUDE.md` — Project architecture and agent flow


