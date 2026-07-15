# Schema Updates: Agent Recommendations & SQL Execution

## Summary

Added support for storing agent recommendations and executable SQL statements in the `exceptions` table, with automatic execution on user approval.

## Changes Made

### 1. Database Schema (backend/db.py)

Added two new columns to the `exceptions` table:

```sql
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommendation JSONB;
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommended_sql TEXT;
```

**Column Descriptions:**
- `recommendation` (JSONB): Stores the complete agent recommendation with:
  - `action`: Human-readable action description
  - `rationale`: Explanation with supporting evidence
  - `confidence`: Confidence score (0.0–1.0)
  - `requires_human_approval`: Always true (human-in-the-loop requirement)
  - `sql`: (Optional) SQL statement if auto-fix is applicable
  
- `recommended_sql` (TEXT): Extracted SQL statement from recommendation for easy execution. Populated when investigations complete.

### 2. Resolution Node Updates (backend/agents/nodes/resolution.py)

**Updated SYSTEM prompt** to guide Claude to optionally include SQL statements in recommendations:
- Only when the fix is straightforward and safe
- For payment corrections using UPDATE statements on `payments` or `exceptions` tables
- Uses payment identifiers (`msg_id`, `uetr`) for targeting

**Example SQL patterns the agent can recommend:**

```sql
-- Fix IBAN typo
UPDATE payments SET debtor_iban='DE89370400440532013000' WHERE msg_id='MSG-12345';

-- Mark duplicate as cancelled
UPDATE exceptions SET status='cancelled' WHERE msg_id='MSG-12346';

-- Correct creditor name
UPDATE payments SET creditor_name='ACME CORPORATION' WHERE msg_id='MSG-12347';

-- Release held payment after compliance clearance
UPDATE payments SET has_error=false WHERE msg_id='MSG-12348';
```

### 3. Investigation Completion (backend/routers/exceptions.py)

When investigations complete, the system now:
1. Extracts the `sql` field from the agent's recommendation
2. Stores the full recommendation in `exceptions.recommendation` (JSONB)
3. Stores the SQL in `exceptions.recommended_sql` (TEXT) for easy access

Updated the `/api/exceptions` endpoint to return recommendation and recommended_sql in the response.

### 4. Approval & Execution (backend/routers/resolutions.py)

Enhanced `/api/resolutions/{report_id}/approve` endpoint to:

1. **Fetch the recommended SQL** from the exceptions record
2. **Execute it automatically** (if present) within a transaction
3. **Report execution status** back to the caller with:
   - `executed`: boolean indicating success/failure
   - `rows_affected`: number of rows modified
   - `error`: error message if execution failed
   - `message`: human-readable status

**Example approval response:**
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

## Workflow

```
1. Exception detected
   ↓
2. Investigation runs (Intake → Investigation → Technical/Compliance → Resolution)
   ↓
3. Resolution Agent synthesizes findings and recommends action
   - Optionally includes safe auto-fix SQL statement
   ↓
4. Recommendations stored in exceptions table
   - exceptions.recommendation (full JSON)
   - exceptions.recommended_sql (extracted SQL)
   ↓
5. Analyst reviews in UI
   ↓
6. User clicks "Approve" → /api/resolutions/{report_id}/approve
   ↓
7. Backend executes recommended_sql (if present)
   ↓
8. Returns execution result to frontend
```

## Safety Features

✓ **Human-in-the-loop**: Agent never auto-executes; requires human approval  
✓ **Transactional**: SQL executes within a transaction; can be rolled back  
✓ **Logged**: All approvals and SQL executions logged  
✓ **Optional**: Agent can omit SQL if human review of each detail is safer  
✓ **Validated**: SQL runs only after explicit user approval in UI  

## Example Integration in Frontend

```javascript
// Call the approve endpoint
const response = await fetch(`/api/resolutions/${reportId}/approve`, {
  method: 'POST'
});

const result = await response.json();

if (result.sql_execution) {
  if (result.sql_execution.executed) {
    console.log(`✓ Applied: ${result.sql_execution.message}`);
    console.log(`  Rows affected: ${result.sql_execution.rows_affected}`);
  } else {
    console.error(`✗ Failed: ${result.sql_execution.error}`);
  }
}
```

## Testing the Feature

### 1. Seed a faulty payment
```bash
curl -X POST http://localhost:8000/api/seed \
  -H "Content-Type: application/json" \
  -d '{"count":5, "error_rate":0.5}'
```

### 2. Investigate an exception
```
Frontend → Select exception → Click "Investigate"
```

### 3. Review recommendation
```
Agent recommends action with optional SQL
Frontend displays: "Recommended SQL: UPDATE payments SET ..."
```

### 4. Approve with SQL execution
```bash
curl -X POST http://localhost:8000/api/resolutions/RPT-0001/approve
# → Returns: { "status": "approved", "sql_execution": { "executed": true, ... } }
```

### 5. Verify in PostgreSQL
```sql
SELECT msg_id, status, recommendation, recommended_sql 
FROM exceptions 
WHERE status = 'resolved' 
LIMIT 5;
```

## Database Schema (Full exceptions table)

```
exceptions (
  id                 SERIAL PRIMARY KEY,
  payment_id         INTEGER,
  msg_id             TEXT UNIQUE NOT NULL,
  uetr               TEXT NOT NULL,
  detected_errors    JSONB NOT NULL DEFAULT '[]',
  status             TEXT NOT NULL DEFAULT 'pending',
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW(),
  precheck_summary   JSONB,
  precheck_input_tokens   INTEGER DEFAULT 0,
  precheck_output_tokens  INTEGER DEFAULT 0,
  recommendation     JSONB,              ← NEW
  recommended_sql    TEXT                ← NEW
)
```

## Notes

- The `recommendation` field stores the **full agent output** (all keys from resolution node)
- The `recommended_sql` field is **extracted from `recommendation.sql`** for easy querying/execution
- SQL execution is **not sandboxed** — ensure the agent's recommendations are trustworthy before approving
- For **very large updates**, the SQL execution timeout may need adjustment in production
- The **resolution node's SYSTEM prompt** can be tuned to be more/less aggressive about recommending SQL auto-fixes


