# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PayInvestigator** — AI-powered multi-agent system that autonomously investigates and triages payment exceptions. Built for the Vista Hackathon 2026 (July 13–15, Scottsdale AZ). Submission deadline: July 15, 8:00 AM.

The core value prop: replace 15–45 minutes of manual analyst work (clicking through 5+ systems) with a structured, auditable AI investigation in seconds. Target integration: Finastra Global PAYplus.

## Tech Stack

| Layer | Choice |
|---|---|
| AI / LLM | Claude `claude-sonnet-4-6` via AWS Bedrock |
| Agent framework | LangGraph (Python) — graph-based multi-agent orchestration |
| Responsible AI | Amazon Bedrock Guardrails — topic denial, PII redaction, content filtering |
| Knowledge Base | Amazon Bedrock KB + OpenSearch Serverless VECTORSEARCH (AOSS) — `OGQEU4WHIQ` |
| Backend | FastAPI (Python) |
| Data store | RDS PostgreSQL (psycopg2); `seed_db.py` seeds from S3 pacs.008 manifest on container startup |
| Frontend | React + Recharts |
| Infra | Terraform (AWS) |

## Agent Architecture

```
Exception Event
      │
      ▼
┌─────────────────┐
│  Intake Agent   │  ← Classifies exception type, extracts key fields
└────────┬────────┘
         ▼
┌─────────────────┐
│ Investigation   │  ← Pulls payment record, SWIFT message, error code
│    Agent        │    Queries counterparty directory, account status
└────────┬────────┘
    ┌────┴────┐
    ▼         ▼
┌───────┐  ┌──────────┐
│Compli-│  │Technical │  ← Parallel sub-agents based on exception type
│ance   │  │Diagnosis │
│Agent  │  │Agent     │
└───┬───┘  └────┬─────┘
    └─────┬─────┘
          ▼
┌─────────────────┐
│ Resolution      │  ← Synthesizes findings, recommends action
│ Agent           │    REQUIRES human approval before any execution
└─────────────────┘
```

**Human-in-the-loop is non-negotiable**: the Resolution Agent recommends only — no payment action executes without explicit human approval. This is a judging criterion and a Responsible AI requirement.

## Repo Structure

```
backend/
  main.py                              FastAPI app, lifespan, /api/seed, background pre-check worker
  db.py                                PostgreSQL connection helper (psycopg2)
  seed_db.py                           Container startup script — seeds DB from S3 manifest
  requirements.txt
  Dockerfile

  agents/
    graph.py                           LangGraph StateGraph: START→intake→investigate→[technical‖compliance]→resolution→END
    state.py                           InvestigationState TypedDict
    guardrail.py                       Bedrock Guardrail wrapper (topic denial, PII redaction)
    nodes/
      intake.py                        Intake Agent — classifies exception, sets needs_technical/needs_compliance
      investigate.py                   Investigation Agent — pulls payment record, events, error details
      dispatch.py                      Conditional edge — returns list[Send] to route technical/compliance in parallel
      technical.py                     Technical Diagnosis Agent — validate IBAN/BIC, detect duplicate, check FX
      compliance.py                    Compliance Agent — sanctions screening, address completeness
      resolution.py                    Resolution Agent — synthesises findings, recommends action + SQL
    tools/
      knowledge_base_tool.py           search_knowledge_base(query) — Bedrock KB retrieve(), KB ID OGQEU4WHIQ
      payment_tools.py                 get_payment_record, get_payment_events, get_resolution_history
      technical_tools.py               validate_iban_tool, validate_bic_tool, check_duplicate_tool, check_fx_tool
      compliance_tools.py              screen_entity_tool (fuzzy SDN match), check_address_completeness_tool

  routers/
    exceptions.py                      GET /api/exceptions, POST /api/ingest/exceptions,
                                       POST /api/exceptions/{tx_id}/investigate (SSE stream)
    resolutions.py                     POST /api/resolutions/{report_id}/approve|reject,
                                       POST /api/reports/{report_id}/chat (KB-backed Q&A)
    metrics.py                         GET /api/metrics/kpis|volume|savings|exceptions|correspondents|
                                           ai|throughput|token-costs
                                       GET /api/monitoring/inflight|alerts|heatmap
                                       POST /api/demo/generate

  db/
    schema.sql                         SQLite schema reference (MINF + NEWJOURNAL — GPP naming)
    seed.py                            Seed helper
  eval/
    runner.py                          Evaluation harness
    scorer.py                          Scoring logic
    report.py                          Report generation
    fixtures/                          bad_iban.json, duplicate_payment.json, sanctions_hit.json
  tests/
    test_agent_routing.py
    test_precheck.py
    test_tools.py

frontend/                              React + Recharts
  src/                                 3-tab dashboard: Ops, Exceptions, Monitoring
                                       Agent SSE stream panel, HITL approve/reject, Report chatbot

infra/                                 Terraform (us-west-2)
  main.tf                              AWS + OpenSearch + Cloudflare providers
  bedrock.tf                           Bedrock Guardrail (topic denial, PII, content filters)
  bedrock_kb.tf                        Bedrock Knowledge Base + AOSS collection + opensearch_index
  s3.tf                                mockdata bucket + knowledge_base bucket
  assets/                              KB reference docs (uploaded to KB S3 bucket)
    error-code-catalog.md
    iban-format-registry.md
    sanctions-screening-procedure.md
    duplicate-payment-resolution.md
    swift-pacs008-field-guide.md
    payment-sla-and-escalation.md
  ecs.tf / rds.tf / alb.tf / iam.tf / ...

jobs/
  pacs008-generator/                   pacs.008 CBPR+ SR2025 XML generator with error injection + IBAN validator
  payment-ingest/                      SQS-triggered Lambda — XML → PostgreSQL

docs/
  openapi.yaml                         Full OpenAPI 3.1 spec for all API endpoints
  Vista Hackathon Implementation Plan.md
  Vista Hackathon 2026 - Ideas.md
  2026 Vista Hackathons FAQ.md
```

## Demo Scenarios (must work for submission)

Three pre-crafted scenarios the live demo must nail:

1. **Bad IBAN checksum** — Technical Diagnosis → agent corrects and re-proposes in ~10 seconds
2. **Sanctions screening hit** — sender name partial-matches SDN list → Compliance Agent researches, recommends hold + rationale
3. **Duplicate payment** — same payment submitted twice → agent detects, recommends cancel on the second

## Mock Data Sets

All data lives as JSON files (no real PII). Generate ~30 records each:

- `payment_transactions` — `tx_id`, `sender_bic`, `receiver_bic`, `sender_iban`, `receiver_iban`, `amount`, `currency`, `status`, `error_code`, `timestamp`
- `error_code_catalog` — `error_code`, `description`, `plain_english_explanation`, `standard_remediation`
- `bic_directory` — `bic`, `bank_name`, `country`, `correspondent_banks[]`
- `sanctions_list` — `entity_name`, `aliases[]`, `country`, `list_type` (simplified OFAC SDN)
- `resolution_history` — past cases + resolutions (agent context/memory)

## Backend Commands

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload          # dev server on :8000
pytest                             # run all tests
pytest tests/test_precheck.py      # pre-check worker tests
pytest tests/test_tools.py         # tool unit tests

# Seed demo data via API (after server starts)
curl -X POST http://localhost:8000/api/seed \
  -H 'Content-Type: application/json' \
  -d '{"count": 15, "error_rate": 0.4, "stuck_rate": 0.1}'
```

**Background pre-check worker**: on startup, `_precheck_worker` drains `_precheck_queue` (asyncio.Queue). Every seeded/ingested faulty exception is enqueued and the Intake Agent runs in isolation to populate `precheck_summary` on the exception row before any full investigation is triggered.

## Frontend Commands

```bash
cd frontend
npm install
npm run dev       # dev server
npm run build     # production build
```

## Infra Commands

```bash
cd infra
terraform init
terraform plan

# Standard apply (no KB changes):
terraform apply

# When changes touch bedrock_kb.tf or opensearch_index — the opensearch provider
# doesn't inherit the AWS SSO session, so export credentials first:
eval "$(aws configure export-credentials --profile AdministratorAccess-446643829639 --format env)" && terraform apply -auto-approve
```

`terraform.tfvars` must include `aws_profile = "AdministratorAccess-446643829639"`.

## Key Constraints

- **Every team member must commit throughout the event** — Vista runs an automated agent to assess commit distribution and timestamps. Don't batch commits at the end.
- **No pre-built application logic** — environment setup only was allowed before July 14, 8 AM start.
- **Responsible AI Assessment required for submission** — audit trail, human approval gate, explainability, and false-positive/escalation handling must be demonstrable.
- **Agentic AI orchestration must be the core** — parallel specialist agents coordinated by an orchestration layer is the architectural requirement.

## Agent Tool Calls (function calling pattern)

Agents call LangChain `@tool`-decorated functions against the RDS PostgreSQL DB and external services.
No real external APIs — all data is synthetic.

**Payment tools** (`agents/tools/payment_tools.py`):
- `get_payment_record(msg_id)` → full payment row from `payments` table
- `get_payment_events(uetr)` → chronological lifecycle events (PAYMENT_RECEIVED → SETTLEMENT_CONFIRMED)
- `get_resolution_history(error_code)` → last 5 approved investigation recommendations for same error code

**Technical tools** (`agents/tools/technical_tools.py`):
- `validate_iban_tool(iban)` → ISO 7064 mod-97 check via `pacs008_generator.iban_validator`
- `validate_bic_tool(bic)` → BIC length + ISO 3166 country code (positions 5–6)
- `check_duplicate_tool(uetr, msg_id)` → detects existing payment with same UETR
- `check_fx_tool(instd_amt, sttlm_amt, rate)` → flags > 1 % deviation between instructed and settlement amounts

**Compliance tools** (`agents/tools/compliance_tools.py`):
- `screen_entity_tool(name)` → fuzzy match (SequenceMatcher, threshold 0.70) against in-memory OFAC SDN list
- `check_address_completeness_tool(address_json)` → FATF Travel Rule field check (Ctry required, TwnNm/StrtNm recommended)

**Knowledge base** (`agents/tools/knowledge_base_tool.py`):
- `search_knowledge_base(query)` → Bedrock KB `retrieve()`, top-5 results with content + score + S3 source URI

## Key Live Resource IDs (us-west-2)

| Resource | ID |
|---|---|
| Bedrock Knowledge Base | `OGQEU4WHIQ` |
| KB S3 bucket | `payinvestigator-kb-446643829639` |
| Bedrock Guardrail | `elu2okf0di0w` |
| Guardrail ARN | `arn:aws:bedrock:us-west-2:446643829639:guardrail/elu2okf0di0w` |
| AOSS Collection | `0y4c0p3nto6tzm5zrmof` |

## Judging Criteria

Judges evaluate: completeness of the challenge prompt, quality of live demo, feasibility of productization, and technical architecture. Judges include Vista MDs/SVPs — pitch at executive level.
