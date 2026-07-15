# Recommended SQL Patterns for Common Payment Exceptions

This guide shows example SQL statements that the Resolution Agent can recommend for auto-execution when users approve recommendations.

## Pattern 1: IBAN Validation Errors

### Error: IBAN_INVALID_CHECKSUM

**Problem:** Bad IBAN checksum detected  
**Action:** Correct the IBAN to valid checksum

```sql
-- If agent identified the correct IBAN during investigation:
UPDATE payments 
SET debtor_iban = 'DE89370400440532013000'
WHERE msg_id = 'MSG-12345'
AND status NOT IN ('completed', 'cancelled');
```

### Error: IBAN_WRONG_LENGTH

```sql
-- Trim or pad IBAN to correct length (e.g., 22 chars for German IBAN)
UPDATE payments 
SET debtor_iban = SUBSTRING(debtor_iban, 1, 22)
WHERE msg_id = 'MSG-12346'
AND LENGTH(debtor_iban) > 22
AND status NOT IN ('completed', 'cancelled');
```

## Pattern 2: Duplicate Payment Detection

### Error: DUPLICATE_UETR

**Problem:** Payment submitted twice  
**Action:** Cancel the duplicate

```sql
-- Mark the duplicate exception as cancelled (analyst already approved the original)
UPDATE exceptions 
SET status = 'cancelled'
WHERE uetr = 'UETR-12345' 
AND msg_id = 'MSG-duplicate'
AND status != 'resolved';

-- Optionally also mark the payment as faulty/ignored:
UPDATE payments 
SET is_faulty = true, error_msg = 'Duplicate of MSG-original'
WHERE msg_id = 'MSG-duplicate';
```

## Pattern 3: BIC/Country Mismatches

### Error: BIC_IBAN_COUNTRY_MISMATCH

```sql
-- Correct BIC based on IBAN country code
-- (Agent identifies correct BIC from reference data during investigation)
UPDATE payments 
SET creditor_bic = 'DEUTDEDBBER'
WHERE msg_id = 'MSG-12347'
AND creditor_iban LIKE 'DE%';
```

## Pattern 4: Sanctions Screening Hits

### Error: Sanctions screening partial match (Compliance finding)

```sql
-- Release payment if compliance agent cleared it after review
UPDATE exceptions 
SET status = 'resolved', recommendation = jsonb_set(
  recommendation, 
  '{cleared_compliance}', 
  'true'::jsonb)
WHERE msg_id = 'MSG-12348'
AND status = 'awaiting_approval';

UPDATE payments 
SET has_error = false
WHERE msg_id = 'MSG-12348';
```

## Pattern 5: Missing/Incomplete ISO 20022 Fields

### Error: BENEFICIARY_NAME_INCOMPLETE

```sql
-- If agent identified the complete beneficiary name during investigation:
UPDATE payments 
SET creditor_name = 'ACME CORPORATION GMBH'
WHERE msg_id = 'MSG-12349'
AND creditor_name IS NOT NULL
AND LENGTH(creditor_name) < 10;
```

## Pattern 6: FX / Exchange Rate Issues

### Error: XCHG_RATE_INCONSISTENT

```sql
-- Update with correct exchange rate after technical verification
UPDATE payments 
SET amount = (amount * 1.0856)  -- Example: corrected EUR-to-GBP rate
WHERE msg_id = 'MSG-12350'
AND currency = 'EUR';
```

## Advanced: Multi-Table Corrections

### Scenario: Technical diagnosis found duplicate records

```sql
-- Example: if duplicate payment slipped through to the payments table
BEGIN;

-- Mark the newer one as cancelled/faulty
UPDATE payments 
SET is_faulty = true, error_msg = 'Duplicate of payment ID 12345'
WHERE msg_id = 'MSG-newer-duplicate'
AND id != 12345;

-- Link both to the same exception for audit
UPDATE exceptions 
SET detected_errors = jsonb_set(
  detected_errors, 
  '{0, related_payment_ids}', 
  '[12345, 12346]'::jsonb)
WHERE msg_id = 'MSG-newer-duplicate';

-- Create audit trail
INSERT INTO payment_events (
  event_id, uetr, msg_id, event_type, status_code,
  source_system, actor, detail, occurred_at
) VALUES (
  'EVT-' || gen_random_uuid(),
  'UETR-newer-duplicate',
  'MSG-newer-duplicate',
  'DUPLICATE_MARKED',
  'CANCELLED',
  'PayInvestigator',
  'Resolution Agent',
  'Duplicate payment marked as cancelled after approval',
  NOW()
);

COMMIT;
```

## Safety Guidelines for Agent SQL Generation

✅ **Safe patterns:**
- `UPDATE` with `WHERE` clause targeting single payment by `msg_id` or `uetr`
- Column corrections (IBAN, BIC, name, amount)
- Status updates on `exceptions` or `payments` tables
- Simple arithmetic (e.g., FX rate application)

⚠️ **Use with caution:**
- `DELETE` statements (use soft-delete via status flags instead)
- Bulk updates without limiting by payment ID
- Calculations without verification
- Cross-table deletes or cascading changes

❌ **Not permitted:**
- `DROP TABLE`, `TRUNCATE`, `ALTER TABLE`
- Transactions without explicit `BEGIN/COMMIT` pair
- Updates to `precheck_summary`, `precheck_input_tokens`, `precheck_output_tokens` (system-managed)
- Batch updates without a limiting WHERE clause

## Testing SQL in Agent Recommendations

### 1. Test the SQL independently:
```bash
# Connect to DB and test the SQL before enabling approval
psql $DATABASE_URL
```

### 2. Check the recommendation JSON:
```bash
curl http://localhost:8000/api/exceptions \
  | jq '.[] | select(.msg_id == "MSG-12345") | .recommendation'
```

### 3. Verify execution:
```bash
# After approval, check if payment was updated
curl http://localhost:8000/api/exceptions \
  | jq '.[] | select(.msg_id == "MSG-12345") | {status, recommended_sql}'
```

## Validation Checklist for Agent SQL

Before the Resolution Agent includes a `sql` field in a recommendation, verify:

1. ✓ SQL is syntactically valid PostgreSQL
2. ✓ Targets a specific payment (WHERE clause with `msg_id` or `uetr`)
3. ✓ Does not modify system-managed columns
4. ✓ Effect is reversible or justified
5. ✓ Matches the stated `action` in the recommendation
6. ✓ Will not cause a cascade of downstream errors

## Audit & Rollback

If an approved SQL execution causes unintended consequences:

### View execution history:
```sql
-- Exceptions with approved recommendations
SELECT msg_id, status, recommended_sql, updated_at 
FROM exceptions 
WHERE status = 'resolved' 
AND recommended_sql IS NOT NULL 
ORDER BY updated_at DESC;
```

### Rollback procedure:
```sql
-- Restore from backup or re-run the inverse correction
-- Example: if IBAN was incorrectly updated
BEGIN;
UPDATE payments 
SET debtor_iban = 'ORIGINAL_IBAN_HERE'
WHERE msg_id = 'MSG-12345';
UPDATE exceptions 
SET status = 'pending', recommended_sql = NULL
WHERE msg_id = 'MSG-12345';
COMMIT;
```

---

**Next:** When implementing the agent's recommendation generation, ensure the Resolution Agent validates SQL before including it in the JSON output. Consider adding a SQL validation step using `sqlparse` or PostgreSQL's parser before persisting to the database.


