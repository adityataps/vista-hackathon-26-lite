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
| Data store | SQLite seeded from S3 on startup; RDS PostgreSQL for payment events |
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
  main.py                              FastAPI routes incl. POST /api/seed, POST /api/investigate
  agents/
    guardrail.py                       Bedrock converse() wrapper with guardrailConfig injection
    graph.py                           LangGraph orchestration graph (intake → investigation → parallel specialists → resolution)
    nodes/                             Individual agent node implementations
    tools/
      knowledge_base_tool.py           search_knowledge_base(query) — Bedrock KB retrieve()
      <other tools>                    get_payment_record, get_swift_message, check_sanctions, etc.

frontend/                              React + Recharts — 3-tab dashboard, agent stream, HITL gate, chatbot

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
  pacs008-generator/                   pacs.008 CBPR+ SR2025 XML generator with error injection
  payment-ingest/                      SQS-triggered Lambda — XML → PostgreSQL

docs/                                  Implementation plan, idea bank, FAQ
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
pytest tests/test_agents.py -k "intake"  # run single test
```

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

Agents call mock tools to retrieve data — not external APIs. Implemented tools:
- `get_payment_record(tx_id)` → transaction details
- `get_swift_message(tx_id)` → raw SWIFT/pacs.008 message
- `get_error_description(error_code)` → error catalog entry
- `check_sanctions(entity_name)` → sanctions list match + score
- `get_bic_info(bic)` → bank/counterparty details
- `get_resolution_history(error_code)` → similar past cases
- `get_payment_events(tx_id)` → full payment lifecycle event log (PAYMENT_RECEIVED → SETTLEMENT_CONFIRMED)
- `search_knowledge_base(query)` → semantic search over KB docs via Bedrock KB `retrieve()`, returns top-5 results with content + score + S3 source URI

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
