# Quick Start: Agent Recommendations & SQL Approval

## What's New

The PayInvestigator backend now supports storing agent-recommended SQL statements and executing them when analysts approve recommendations.

## Files Changed

```
backend/
  ├── db.py                          (added 2 ALTER TABLE statements)
  ├── agents/nodes/resolution.py     (updated agent prompt)
  ├── routers/
  │   ├── exceptions.py              (populate recommendation fields)
  │   └── resolutions.py             (execute SQL on approval)
```

## Rolling Out

### 1. Pull the changes
```bash
git pull origin <your-branch>
```

### 2. Database auto-migration
On startup, the backend will automatically add the two missing columns to PostgreSQL:
```sql
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommendation JSONB;
ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS recommended_sql TEXT;
```

No manual migration needed — it's baked into `backend/db.py` startup.

### 3. Test it

#### A. Seed some exceptions
```bash
curl -X POST http://localhost:8000/api/seed \
  -H "Content-Type: application/json" \
  -d '{
    "count": 10,
    "error_rate": 0.4,
    "error_codes": ["IBAN_INVALID_CHECKSUM", "DUPLICATE_UETR"]
  }'
```

#### B. Investigate one
```bash
# Via frontend: select an exception and click "Investigate"
# OR via curl:
curl -X POST http://localhost:8000/api/exceptions/TX-00001/investigate \
  --header "Accept: text/event-stream"
```

#### C. Review recommendation
```bash
# Fetch the exception details
curl http://localhost:8000/api/exceptions | jq '.[] | select(.msg_id == "MSG-12345")'
```

Look for these new fields in the response:
```json
{
  "msg_id": "MSG-12345",
  "status": "awaiting_approval",
  "recommendation": {
    "action": "Correct the debtor IBAN...",
    "rationale": "...",
    "confidence": 0.95,
    "sql": "UPDATE payments SET debtor_iban='...' WHERE msg_id='MSG-12345'"
  },
  "recommended_sql": "UPDATE payments SET debtor_iban='...' WHERE msg_id='MSG-12345'"
}
```

#### D. Approve & execute
```bash
curl -X POST http://localhost:8000/api/resolutions/RPT-0001/approve
```

Response:
```json
{
  "status": "approved",
  "report_id": "RPT-0001",
  "sql_execution": {
    "executed": true,
    "rows_affected": 1,
    "message": "Recommended action executed successfully"
  }
}
```

### 4. Verify in database
```bash
psql $DATABASE_URL
SELECT msg_id, status, recommendation, recommended_sql 
FROM exceptions 
WHERE status = 'resolved' 
LIMIT 5;
```

## Key Points

✓ **Backward compatible**: Old investigations continue to work (no `sql` field in older recommendations)  
✓ **Human-in-the-loop**: Agent never auto-executes; analyst must approve first  
✓ **Optional**: Agent can omit `sql` field if human review is safer  
✓ **Transactional**: SQL executes within a database transaction  
✓ **Logged**: All approvals and executions are logged  

## Common Issues

### Q: SQL didn't execute, but approval shows "executed: true"
**A:** Check the agent's recommendation — it may not have included a `sql` field. Review the investigation report.

### Q: I got an error: "recommended_sql" column not found
**A:** The database columns weren't created. Restart the backend — the `_ensure_schema()` function will auto-create them.

### Q: The agent's SQL is wrong or unsafe
**A:** Review the SYSTEM prompt in `backend/agents/nodes/resolution.py`. You can:
1. Tighten the prompt to be more conservative
2. Add validation before stored (validate with `sqlparse`)
3. Require an extra approval step for SQL recommendations

### Q: Can I reject a recommendation without executing SQL?
**A:** Yes, use the existing `/api/resolutions/{report_id}/reject` endpoint. It won't execute SQL.

## API Reference

### GET /api/exceptions
Returns list of exceptions with new fields:
```json
{
  "status": "awaiting_approval",
  "recommendation": { /* full agent recommendation */ },
  "recommended_sql": "UPDATE ... WHERE ...",
  ...
}
```

### POST /api/resolutions/{report_id}/approve
Approves an investigation and executes recommended SQL (if present):
```json
{
  "status": "approved",
  "sql_execution": {
    "executed": true|false,
    "rows_affected": N,
    "error": null|"error message",
    "message": "status message"
  }
}
```

### POST /api/resolutions/{report_id}/reject
Rejects without executing SQL:
```json
{
  "status": "rejected",
  "report_id": "RPT-0001"
}
```

## Documentation

- **Implementation details:** see `SCHEMA_UPDATES.md`
- **SQL patterns & examples:** see `SQL_PATTERNS.md`
- **Full summary:** see `IMPLEMENTATION_SUMMARY.md`
- **Agent architecture:** see `CLAUDE.md`

## Team Tasks

- [ ] Frontend team: Update resolution UI to preview recommended SQL before approval
- [ ] QA team: Test approval flow with SQL execution
- [ ] DevOps team: Add SQL execution monitoring/alerting
- [ ] Docs team: Update API docs with new `recommendation` and `recommended_sql` fields

---

**Questions?** Check the docs above or reach out to the team.


