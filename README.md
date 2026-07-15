# PayInvestigator

AI-powered multi-agent system that autonomously investigates and triages payment exceptions. Built for the **Vista Hackathon 2026** (July 13–15, Scottsdale AZ).

**The core value prop:** replace 15–45 minutes of manual analyst work — clicking through 5+ systems — with a structured, auditable AI investigation in seconds. Target integration: Finastra Global PAYplus.

---

## Problem

Payment ops teams fight on two fronts simultaneously:

1. **Exceptions** — when a payment fails, analysts manually investigate across 5+ systems. Each case takes 15–45 minutes. A mid-size bank handles hundreds per day.
2. **Bottlenecks** — slow in-flight payments sit undetected until a customer calls. By then, the SLA window has closed.

Both problems share the same root cause: no system connects the dots across payment lifecycle events, correspondent data, and business rules automatically.

---

## Agent Architecture

Three tracks, shared toolset.

### Track 1 — Reactive (Exception Resolution)

```
Failed Payment Event
      │
      ▼
┌─────────────────┐
│  Intake Agent   │  ← Classifies exception type, extracts key fields
└────────┬────────┘
         ▼
┌─────────────────┐
│ Investigation   │  ← Pulls payment record, SWIFT message, error code,
│    Agent        │    counterparty directory, account status
└────────┬────────┘
    ┌────┴────┐
    ▼         ▼
┌──────────┐  ┌──────────────┐
│Compliance│  │  Technical   │  ← Parallel sub-agents based on exception type
│  Agent   │  │  Diagnosis   │
└────┬─────┘  └──────┬───────┘
     └────────┬───────┘
              ▼
      ┌───────────────┐
      │  Resolution   │  ← Synthesizes findings, recommends action
      │    Agent      │    HUMAN APPROVAL REQUIRED before execution
      └───────────────┘
```

Exception types handled:
- Bad IBAN checksum → Technical Diagnosis → auto-correctable
- Duplicate payment reference → Technical Diagnosis → recommend cancel
- Sanctions screening hit → Compliance Agent → recommend hold + escalate
- Missing mandatory ISO 20022 field → Technical Diagnosis → field repair suggestion
- FX limit breach → Technical + Compliance → recommend review

### Track 2 — Proactive (Bottleneck Detection)

```
Payment Lifecycle Event Stream
      │
      ▼
┌─────────────────┐
│  Monitor Agent  │  ← Watches in-flight payments vs SLA benchmarks
└────────┬────────┘
         ▼
┌──────────────────────┐
│ Bottleneck Analysis  │  ← Identifies WHERE the delay is occurring
│       Agent          │
└────────┬─────────────┘
         ▼
┌─────────────────┐
│  Pattern Agent  │  ← Detects systemic issues across correspondents/rails
└────────┬────────┘
         ▼
┌─────────────────┐
│   Alert Agent   │  ← Notifies ops with context pre-populated
└─────────────────┘
```

### Track 3 — Conversational (Report Q&A)

After Track 1 or 2 produces an investigation report, analysts can ask follow-up questions in natural language. The chatbot has the full report as context and can call the same tools to fetch additional detail.

---

## Demo Scenarios

Four pre-scripted scenarios the live demo must nail:

| # | Scenario | Track | Agent Path |
|---|---|---|---|
| 1 | Bad IBAN checksum — `GB29NWBK60161331926819` fails mod-97 | Track 1 | Investigation → Technical Diagnosis → corrected IBAN proposed in ~10s |
| 2 | Sanctions screening hit — sender partial-matches OFAC SDN list | Track 1 | Investigation → Compliance → hold recommendation with rationale |
| 3 | Stuck payment — USD→SGD at Deutsche Bank for 6h (expected 2h), 3 other payments same correspondent | Track 2 | Monitor → Bottleneck Analysis → Pattern → systemic flag |
| 4 | Cut-off risk — payment approaching correspondent cut-off in 20 min, no confirmation yet | Track 2 | Monitor → Alert → proactive notification before SLA breach |

---

## Tech Stack

| Layer | Choice |
|---|---|
| AI / LLM | Claude `claude-sonnet-4-6` via AWS Bedrock |
| Agent framework | LangGraph (Python) — graph-based multi-agent orchestration, parallel node execution, built-in checkpointing |
| Responsible AI | Amazon Bedrock Guardrails — topic denial, PII anonymization, content filtering |
| Knowledge Base | Amazon Bedrock KB + OpenSearch Serverless (AOSS VECTORSEARCH) — semantic retrieval over payment ops docs |
| Backend | FastAPI (Python) |
| Data store | SQLite seeded from S3 on startup; RDS PostgreSQL for payment events |
| Frontend | React + Recharts |
| CI/CD | GitHub Actions → ECR → ECS Fargate (path-gated parallel jobs) |
| Cloud | AWS (ECS Fargate, ECR, ALB, RDS PostgreSQL, SQS, Lambda, S3, Bedrock, AOSS, ACM) |
| DNS / TLS | `vistahack26.tapshalkar.com` (Cloudflare CNAME → ALB, ACM cert) |
| IaC | Terraform (`infra/`) |

---

## Infrastructure

```
GitHub push to main
        │
        ▼
GitHub Actions (OIDC — no long-lived keys)
  ├── deploy-ingest   (jobs/payment-ingest/** changed)
  ├── deploy-backend  (backend/**, jobs/pacs008-generator/** changed)
  └── deploy-frontend (frontend/** changed)
        │
        ▼
Cloudflare DNS  →  ALB (HTTPS, ACM cert)
                    ├── /api/*  → FastAPI :8080  (ECS Fargate)
                    └── /*      → Nginx/React :80 (ECS Fargate)
                                       │
                    ┌──────────────────┼────────────────────┐
                    ▼                  ▼                     ▼
              S3 (mock data)    Bedrock (Claude)    RDS PostgreSQL
              payments/ prefix  claude-sonnet-4-6   (payment records)
                    │
                    ▼
              SQS queue
                    │
                    ▼
              Lambda (payment-xml-ingest)
              pacs.008 XML → PostgreSQL payments table
```

### Key AWS Resources

| Resource | Name |
|---|---|
| ECS Cluster | `payinvestigator` |
| Backend Service | `payinvestigator-backend` (Fargate, port 8080) |
| Frontend Service | `payinvestigator-frontend` (Fargate, port 80) |
| ECR — Backend | `payinvestigator-backend` |
| ECR — Frontend | `payinvestigator-frontend` |
| ECR — Ingest Lambda | `payinvestigator-ingest` |
| Lambda | `payinvestigator-payment-xml-ingest` |
| SQS Queue | `payinvestigator-payment-ingest` |
| RDS | PostgreSQL 16, `db.t4g.micro` (publicly accessible for team debugging) |
| S3 — Mock Data | `payinvestigator-mockdata-<account_id>` |
| S3 — Knowledge Base | `payinvestigator-kb-<account_id>` |
| Bedrock Knowledge Base | `OGQEU4WHIQ` |
| AOSS Collection | `payinvestigator-kb` (VECTORSEARCH) |
| Bedrock Guardrail | `elu2okf0di0w` |
| ALB | HTTPS listener, path-based routing |
| Region | `us-west-2` |

---

## Payment Data Pipeline

`POST /api/seed` on the backend generates a batch of pacs.008 ISO 20022 XML messages and uploads them to S3, triggering the full ingest pipeline:

```
POST /api/seed
      │  generates pacs.008 XML via jobs/pacs008-generator/
      ▼
S3  payments/YYYYMMDDTHHMMSSz-<run_id>/*.xml
      │  S3 ObjectCreated event
      ▼
SQS  payinvestigator-payment-ingest
      │  event source mapping
      ▼
Lambda  payment-xml-ingest
      │  parses pacs.008, upserts payment records
      ▼
RDS PostgreSQL  payments table
```

The generator produces realistic CBPR+ pacs.008 SR2025 messages with configurable error injection (`error_rate`, `error_codes`) for demo scenario seeding.

---

## Repo Structure

```
backend/                    FastAPI app + agent logic
  main.py                   API routes incl. POST /api/seed, POST /api/investigate
  seed_db.py                seeds SQLite from S3 on startup
  agents/
    guardrail.py            Bedrock converse() wrapper with guardrailConfig injection
    graph.py                LangGraph orchestration graph
    nodes/                  Individual agent node implementations
    tools/
      knowledge_base_tool.py  search_knowledge_base() — Bedrock KB retrieve()
  Dockerfile                build context: repo root
  requirements.txt

frontend/                   React + Vite + Recharts
  src/App.jsx               three-tab dashboard (Ops Dashboard, Exception Queue, Bottleneck Monitor)
  Dockerfile                multi-stage: node build → nginx serve

jobs/
  pacs008-generator/        pacs.008 XML generation package
    pacs008_generator/      Python package (generator, validator, datapool)
    error_catalog.yaml      ISO 20022 error codes + plain-English descriptions
    agent_error_knowledge.yaml  agent context for exception types
  payment-ingest/           SQS-triggered Lambda
    handler.py              downloads XML from S3, upserts to PostgreSQL
    Dockerfile              amazon/aws-lambda-python:3.12 base

infra/                      Terraform (us-west-2)
  main.tf                   AWS + opensearch-project/opensearch + Cloudflare providers
  bedrock.tf                Bedrock Guardrail (topic denial, PII anonymization, content filters)
  bedrock_kb.tf             Bedrock KB + AOSS collection + opensearch_index pre-creation
  ecr.tf                    three ECR repos + 5-tag lifecycle policies
  ecs.tf                    ECS cluster, task definitions, Fargate services
  alb.tf                    ALB, target groups, HTTPS listener, path routing
  acm.tf                    ACM certificate (DNS validation)
  dns.tf                    Cloudflare DNS records (Terraform provider)
  rds.tf                    RDS PostgreSQL 16, db.t4g.micro (public SG rule for team debug)
  sqs.tf                    SQS queue + S3 bucket notification
  lambda.tf                 Lambda function + VPC + event source mapping
  s3.tf                     mockdata bucket + knowledge_base bucket
  iam.tf                    task execution role, backend task role, GitHub OIDC role
  security_groups.tf        ALB, backend, frontend, Lambda, RDS SGs
  cloudwatch.tf             log groups
  assets/                   KB reference docs (6 markdown files, uploaded to KB S3 bucket)
  outputs.tf

.github/workflows/
  deploy.yml                parallel path-gated deploy jobs

docs/
  Vista Hackathon Implementation Plan.md   full product + agent spec
```

---

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload          # :8000
```

Seed payment data to S3 (requires `S3_BUCKET` env var and AWS credentials):

```bash
curl -X POST http://localhost:8000/api/seed \
  -H 'Content-Type: application/json' \
  -d '{"count": 20, "error_rate": 0.3}'
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # :5173
npm run build
```

### pacs.008 Generator (standalone)

```bash
cd jobs/pacs008-generator
pip install -r requirements.txt
python -m pacs008_generator.generator   # writes to output/
```

### Infrastructure

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill in values; set aws_profile
terraform init
terraform plan

# Standard apply:
terraform apply

# When bedrock_kb.tf / opensearch_index changes are involved, the opensearch provider
# doesn't inherit the AWS SSO session — export creds first:
eval "$(aws configure export-credentials --profile AdministratorAccess-446643829639 --format env)" && terraform apply -auto-approve
```

Required GitHub secret: `AWS_ACCOUNT_ID`

---

## Responsible AI

Human-in-the-loop is non-negotiable — a judging criterion and a core design requirement:

- **No autonomous execution.** The Resolution Agent recommends only. Every action requires explicit human approval before it executes.
- **Explainability.** Every recommendation includes plain-English rationale and lists the tools and data sources consulted.
- **Audit trail.** Full log of agent reasoning steps, tool calls, and data accessed — satisfies regulatory requirements.
- **Uncertainty escalation.** The Compliance Agent is designed to flag uncertainty and escalate rather than auto-reject. It reduces human judgment workload, does not replace it.
- **Bedrock Guardrails.** An automated safety layer applied to all LLM calls: topic denial for non-payment queries, PII anonymization (IBAN, SWIFT codes, card numbers, email), and content filtering at high sensitivity. Runs before any agent response reaches the user.
- **No real PII.** All demo data is synthetic. In production, the system operates within the bank's existing data governance perimeter.
