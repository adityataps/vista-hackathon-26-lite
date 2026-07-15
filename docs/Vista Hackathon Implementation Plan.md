# Vista Hackathon — Implementation Plan
**Theme:** Build a product Finastra would want to buy
**Decision:** Payment Exception Investigation by AI Agents

---

## Product Concept

**PayInvestigator** — an AI-powered multi-agent system with two modes: it autonomously investigates and triages payment exceptions (reactive), and continuously monitors in-flight payments to detect and surface bottlenecks before they become failures (proactive).

### Problem Statement
Payment ops teams are always fighting on two fronts:
1. **Exceptions** — when a payment fails, analysts manually click through 5+ systems to investigate. Each case takes 15–45 minutes. A mid-size bank handles hundreds per day. + Costs
2. **Bottlenecks** — slow in-flight payments sit undetected until a customer calls. By then, the SLA window has closed and the damage is done.

Both problems share the same root cause: no system connects the dots across payment lifecycle events, correspondent data, and business rules automatically.

### Why Finastra Would Buy This
- Slots directly into **Global PAYplus** as an AI investigation + monitoring layer
- Reactive mode: exception resolution time from 45 min → seconds
- Proactive mode: catches bottlenecks before SLA breach, before the customer calls
- Full audit trail satisfies regulatory requirements
- Human-in-the-loop approval before any action is taken

### Security/Compliance Angle (baked in, not a separate product)
Compliance holds (sanctions hits, AML flags) are one of the exception types the system handles — the compliance research agent queries screening results, adverse media, and entity profiles as part of its investigation. This covers the security compliance theme without splitting the product.

---

## Agent Architecture

Two tracks, shared resolution layer.

```
TRACK 1 — REACTIVE (Exception Resolution)
═══════════════════════════════════════════════════════
Failed Payment Event
      │
      ▼
┌─────────────────┐
│  Intake Agent   │  ← Classifies exception type, extracts key fields
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Investigation   │  ← Pulls payment record, SWIFT message, error code,
│    Agent        │    counterparty directory, account status
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌──────────┐  ┌──────────────┐
│Compliance│  │  Technical   │  ← Parallel sub-agents based on exception type
│  Agent   │  │  Diagnosis   │
└────┬─────┘  └──────┬───────┘
     └────────┬───────┘
              │
              ▼
      ┌───────────────┐
      │  Resolution   │  ← Synthesizes findings, recommends action (HITL gate)
      │    Agent      │
      └───────────────┘


TRACK 2 — PROACTIVE (Bottleneck Detection)
═══════════════════════════════════════════════════════
Payment Lifecycle Event Stream (continuous)
      │
      ▼
┌─────────────────┐
│  Monitor Agent  │  ← Watches all in-flight payments, compares elapsed
│                 │    time per step against SLA benchmarks
└────────┬────────┘
         │  (SLA risk detected)
         ▼
┌──────────────────────┐
│ Bottleneck Analysis  │  ← Identifies WHERE the delay is occurring:
│       Agent          │    which step, which correspondent, which corridor
└────────┬─────────────┘
         │
         ▼
┌─────────────────┐
│  Pattern Agent  │  ← Checks if this is systemic: are multiple payments
│                 │    through the same correspondent delayed? Same rail?
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Alert Agent   │  ← Notifies ops team with context pre-populated:
│                 │    which payments at risk, root cause hypothesis,
│                 │    recommended escalation action
└─────────────────┘
```

**Exception types handled (Track 1):**
1. Bad IBAN checksum → Technical Diagnosis → auto-correctable
2. Duplicate payment reference → Technical Diagnosis → recommend cancel
3. Sanctions screening hit → Compliance Agent → recommend hold + escalate
4. Missing mandatory ISO 20022 field → Technical Diagnosis → field repair suggestion
5. FX limit breach → Technical Diagnosis + Compliance → recommend review

**Bottleneck patterns detected (Track 2):**
1. Single payment stuck at correspondent beyond SLA threshold
2. Systemic delay — multiple payments delayed through same intermediary
3. Rail degradation — a payment rail (e.g., SEPA Instant) showing elevated processing times
4. Cut-off risk — payment approaching correspondent cut-off with no confirmation yet


TRACK 3 — CONVERSATIONAL (Report Q&A Chatbot)
═══════════════════════════════════════════════════════
After Track 1 or Track 2 produces an investigation report, the analyst
can ask follow-up questions in natural language. The chatbot has the
report as context and can call the same tools to fetch additional detail.

Investigation Report (from Track 1 or 2)
      │
      ▼
┌─────────────────────┐
│  Report Q&A Agent   │  ← Multi-turn conversational agent
│                     │    System prompt = investigation report
│                     │    LangGraph checkpointing = conversation memory
│                     │    Tool access = same tool set as source track
└─────────────────────┘

Example interactions:
  Analyst: "Why did you recommend holding this payment?"
  Analyst: "What's the history of delays with Deutsche Bank?"
  Analyst: "If I approve this, what happens to the IBAN correction?"
  Analyst: "Show me other payments from the same sender this week"

---

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| AI / LLM | Claude claude-sonnet-4-6 via **AWS Bedrock** (`ChatBedrock`) | AWS-native LLM; best tool use + reasoning |
| Agent framework | **LangGraph** (Python) | Graph-based multi-agent orchestration, built-in state + checkpointing, parallel node execution |
| Tool connectivity | LangGraph tool nodes + LangChain tools | Native integration; `@tool` decorator, bind to `ChatBedrock` |
| Responsible AI | **Amazon Bedrock Guardrails** | Topic denial (non-payment queries), PII anonymization, content filtering — automated safety layer on all LLM calls |
| Knowledge Base | **Amazon Bedrock KB + AOSS** (VECTORSEARCH) | Semantic retrieval over 6 payment ops reference docs; KB ID `OGQEU4WHIQ` |
| Backend | FastAPI (Python) | Fast to stand up, clean REST endpoints for frontend |
| Data store | SQLite (seeded from S3 on startup); RDS PostgreSQL for payment events | Simple for mock data; S3 as source of truth |
| Frontend | React + Recharts | Dashboard + investigation UI — 3 views, agent stream, HITL gate, chatbot |
| Containerisation | Docker | Single image for backend; deployed via Fargate |
| CI/CD | GitHub Actions → ECR → ECS Fargate | Push-to-deploy; Docker build + push + task definition update |
| Cloud | AWS (ECS Fargate, ECR, ALB, RDS, SQS, Lambda, S3, Bedrock, AOSS, ACM) | Full AWS stack |
| DNS / Hosting | `vistahack26.tapshalkar.com` (Cloudflare) → ALB | ACM cert on ALB; Cloudflare DNS-only CNAME |
| Repo | GitHub | Judges review commit history; source of CI/CD triggers |

---

## Infrastructure & Deployment

### AWS Architecture

```
GitHub (push to main)
        │
        ▼
GitHub Actions (OIDC → AWS)
  1. Build + push backend image  → ECR (payinvestigator-backend)
  2. Build + push frontend image → ECR (payinvestigator-frontend)
  3. ECS rolling deploy — backend service  (wait for stability)
  4. ECS rolling deploy — frontend service (wait for stability)
        │
        ▼
Cloudflare DNS (DNS only, grey cloud)
  vistahack26.tapshalkar.com  CNAME  →  ALB DNS name
        │
        ▼
ALB (HTTPS :443, ACM cert for vistahack26.tapshalkar.com)
  HTTP :80 → redirect to HTTPS
  /api/*   → Backend Target Group  (Fargate, port 8080)
  /*       → Frontend Target Group (Fargate, port 80)
        │              │
        ▼              ▼
  FastAPI          Nginx serving
  + LangGraph      React build
  (:8080)          (:80)
      │
      ├── S3 (mock data JSON → SQLite on startup)
      └── Bedrock (claude-sonnet-4-6)
```

### GitHub Actions Pipeline

```yaml
# .github/workflows/deploy.yml — on push to main
steps:
  1. Checkout code
  2. Configure AWS credentials (OIDC — no long-lived keys)
  3. Login to ECR
  4. Build + push backend image  (tags: :latest + :<git-sha>)
  5. Build + push frontend image (tags: :latest + :<git-sha>)
  6. Download current backend ECS task definition
  7. Render new task def with updated backend image
  8. Deploy to backend ECS service + wait for stability
  9. Repeat steps 6–8 for frontend service
```

No SSH keys. No long-lived AWS credentials.

### Docker Images

Two images, two ECR repositories:

```dockerfile
# Backend — FastAPI + LangGraph (backend/Dockerfile)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["sh", "-c", "python seed_db.py && uvicorn main:app --host 0.0.0.0 --port 8080"]

# Frontend — React served by Nginx (frontend/Dockerfile)
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

### Data Strategy — SQLite + S3

- **S3 bucket** (`payinvestigator-mockdata-<account_id>`) holds master mock data JSON files (transactions, error catalog, BIC directory, sanctions list, lifecycle events, SLA benchmarks)
- **On container startup**, `seed_db.py` downloads from S3 and seeds a local SQLite DB
- **SQLite** lives on the container's ephemeral disk — fine for a demo with fixed mock data
- **No persistence needed** — mock data is static; S3 is the source of truth

### Subdomain + TLS Setup

- Domain: `tapshalkar.com` on Cloudflare
- Subdomain: `vistahack26.tapshalkar.com` CNAME → ALB DNS name (DNS only, not proxied)
- ACM certificate for `vistahack26.tapshalkar.com`, DNS-validated via Cloudflare CNAME record
- Both DNS records managed via Terraform Cloudflare provider

### Key AWS + Infra Checklist

- [x] ECR repositories created (`payinvestigator-backend`, `payinvestigator-frontend`, `payinvestigator-ingest`)
- [x] ECS cluster + Fargate services defined (backend + frontend)
- [x] ALB + target groups + HTTPS listener + path-based routing
- [x] ACM certificate issued and validated via Cloudflare
- [x] S3 mock data bucket created (`payinvestigator-mockdata-<account_id>`)
- [x] S3 knowledge base bucket created (`payinvestigator-kb-<account_id>`)
- [x] IAM task execution role (ECR pull + CloudWatch Logs)
- [x] IAM backend task role (Bedrock invoke + S3 read + Bedrock KB retrieve + guardrail apply)
- [x] IAM OIDC role (GitHub Actions): ECR push + ECS deploy
- [x] Bedrock model access confirmed (`claude-sonnet-4-6`, `us-west-2`)
- [x] Bedrock Guardrail provisioned (`elu2okf0di0w`) — topic denial, PII, content filters
- [x] Bedrock Knowledge Base provisioned (`OGQEU4WHIQ`) — AOSS VECTORSEARCH, 6 reference docs ingested
- [x] RDS PostgreSQL provisioned (payment events, publicly accessible for team debugging)
- [x] SQS queue + Lambda ingest pipeline live
- [x] `AWS_ACCOUNT_ID` added to GitHub Secrets
- [x] `GET /health` endpoint added to FastAPI (ALB health check)

---

## Team Swim Lanes

| Role | Owner | Responsibilities |
|---|---|---|
| Backend / AI / Infra | Aditya | Agent logic, orchestration, FastAPI, deployment |
| Product / Demo Script | PM (Finastra veteran) | Exception scenario narrative, slide deck, demo flow |
| Frontend | TBD teammate | Dashboard, investigation UI, exception queue, alert feed |
| Mock Data | Any | Generate realistic payment JSON, SWIFT message samples |
| Responsible AI Assessment | PM + Aditya | Human-in-the-loop design, audit trail, bias considerations |

---

## Mock Data Plan

> Pre-generate before writing any agent code. ~30 records per dataset.

### Knowledge Base Reference Docs (`infra/assets/` — ingested into Bedrock KB `OGQEU4WHIQ`)

Six markdown documents uploaded to `payinvestigator-kb-<account_id>` and indexed in the Bedrock Knowledge Base. Agents query these via `search_knowledge_base()`:

| File | Contents |
|---|---|
| `error-code-catalog.md` | 10 ISO 20022 error codes with detection logic, XPath fields, remediation actions |
| `iban-format-registry.md` | 41-country IBAN length/format table, Mod-97 algorithm |
| `sanctions-screening-procedure.md` | OFAC/EU/UK/UN watchlists, hold procedure, false-positive handling; demo entities pre-loaded |
| `duplicate-payment-resolution.md` | camt.056 recall procedure, detection signals, resolution outcomes |
| `swift-pacs008-field-guide.md` | CBPR+ SR2025 pacs.008 AppHdr + CdtTrfTxInf field reference |
| `payment-sla-and-escalation.md` | SLA windows per stage, stuck payment definition, escalation matrix, monitoring SQL |

### Track 1 datasets (Exception Resolution)
- **Payment transactions** — `tx_id`, `sender_bic`, `receiver_bic`, `sender_iban`, `receiver_iban`, `amount`, `currency`, `status`, `error_code`, `timestamp`
- **Error code catalog** — `error_code`, `description`, `plain_english_explanation`, `standard_remediation`
- **Bank/BIC directory** — `bic`, `bank_name`, `country`, `correspondent_banks[]`
- **Sanctions list (simplified OFAC SDN)** — `entity_name`, `aliases[]`, `country`, `list_type`
- **Resolution history** — past cases + what fixed them (agent memory context)

### Dashboard metrics dataset
- **Transaction volume time series** — `timestamp` (hourly), `rail`, `corridor`, `count`, `success_count`, `exception_count`
- **Latency percentiles** — `corridor`, `rail`, `p50_minutes`, `p75_minutes`, `p95_minutes`, `p99_minutes`, `period` (current vs. last_7d benchmark)
- **Exception breakdown** — `exception_type`, `count`, `avg_resolution_time_minutes`, `auto_resolved_count`, `escalated_count`
- **Correspondent health** — `bic`, `bank_name`, `status` (normal/degraded/outage), `avg_processing_minutes`, `payments_delayed_count`
- **AI performance stats** — `total_investigations`, `auto_resolved`, `escalated_to_human`, `recommendation_acceptance_rate`, `avg_investigation_seconds`

### Track 2 datasets (Bottleneck Detection)
- **Payment lifecycle events** — `tx_id`, `step` (submitted / validated / sent_to_correspondent / processing / settled), `step_timestamp`, `expected_duration_minutes`, `actual_duration_minutes`
- **SLA benchmarks** — `corridor`, `rail`, `step`, `p50_minutes`, `p95_minutes`, `breach_threshold_minutes`
- **Correspondent processing stats** — `bic`, `bank_name`, `avg_processing_time_minutes`, `current_status` (normal / degraded / outage)
- **In-flight payment queue** — `tx_id`, `current_step`, `elapsed_minutes`, `sla_deadline`, `risk_level` (on-track / at-risk / breached)

### Pre-crafted demo scenarios (script these exactly)
1. **The easy win** — bad IBAN checksum, agent corrects and re-proposes in 10 seconds (Track 1)
2. **The compliance hold** — sender name partial-matches SDN list, agent researches, recommends hold + rationale (Track 1)
3. **The stuck payment** — USD→SGD payment at Deutsche Bank intermediary for 6h (expected 2h). Monitor agent detects it, Bottleneck Analysis identifies the FX conversion step, Pattern agent finds 3 other payments through the same correspondent also delayed → systemic flag raised (Track 2)
4. **The near-miss** — payment approaching correspondent cut-off in 20 minutes with no processing confirmation. Alert agent fires before SLA breach (Track 2)

---

## 24-Hour Build Timeline

| Window | Goal |
|---|---|
| **Hour 0–1** | Finalize architecture, generate all mock data (both tracks), scaffold repo |
| **Hour 1–4** | Track 1: Intake Agent + Investigation Agent end-to-end |
| **Hour 4–7** | Track 1: Compliance Agent + Technical Diagnosis Agent + Resolution Agent |
| **Hour 7–10** | Track 2: Monitor Agent + Bottleneck Analysis Agent |
| **Hour 10–13** | Track 2: Pattern Agent + Alert Agent, wire both tracks together |
| **Hour 13–17** | FastAPI metrics endpoints, dashboard View 1 + View 2 wired up, demo scenarios 1 + 2 working |
| **Hour 17–20** | Demo scenarios 3 + 4 (bottleneck track), polish all agent outputs |
| **Hour 20–22** | Full dry-run of all 4 scenarios, fix rough edges |
| **Hour 22–24** | Slides, Responsible AI Assessment, submission |

---

## Frontend / Dashboard Layout

Three views, navigable by tab or sidebar.

### View 1 — Operations Dashboard (default landing page)
The "before" view — shows the scale of the problem and system health at a glance.

```
┌────────────────────────────────────────────────────────────────┐
│  KPI Row                                                       │
│  [In-Flight: 142]  [Exceptions: 23]  [At-Risk: 4]  [MTTR: 38m→2s] │
├──────────────────────────┬─────────────────────────────────────┤
│  Transaction Volume      │  Latency Percentiles (by corridor)  │
│  (hourly bar chart,      │  p50 / p95 / p99 line chart,        │
│   by rail)               │  current vs. 7-day benchmark        │
├──────────────────────────┼─────────────────────────────────────┤
│  Exception Breakdown     │  Correspondent Health Table         │
│  (bar chart by type:     │  BIC | Bank | Status | Avg Time     │
│   IBAN / duplicate /     │  DEUTDEDB | Deutsche | ⚠ Degraded  │
│   sanctions / FX)        │  CHASUS33  | JPMorgan | ✓ Normal   │
└──────────────────────────┴─────────────────────────────────────┘
```

### View 2 — Exception Investigation Queue
Live feed of exceptions. Click any row to trigger the agent investigation and watch reasoning stream in real time. Once a report is produced, a chatbot panel opens below for follow-up questions.

```
┌─────────────────────────────────────────────────────────────┐
│ Exception Queue                              [▶ Run All AI] │
├──────────┬──────────────┬──────────┬──────────┬────────────┤
│ TX ID    │ Type         │ Amount   │ Status   │ Action     │
├──────────┼──────────────┼──────────┼──────────┼────────────┤
│ TX-00142 │ Bad IBAN     │ €42,000  │ Pending  │ [Investigate] │
│ TX-00138 │ Sanctions    │ $198,500 │ Pending  │ [Investigate] │
│ TX-00121 │ Duplicate    │ £7,200   │ Resolved │ [View] │
└──────────┴──────────────┴──────────┴──────────┴────────────┘

[Investigation panel — streams agent reasoning + tool calls]
  > Intake Agent: Classified as IBAN_CHECKSUM_ERROR
  > Investigation Agent: Pulled payment record TX-00142...
  > Technical Diagnosis: IBAN GB29NWBK60161331926819 fails mod-97 check
  > Resolution Agent: Suggested correction GB29NWBK60161331926820
  > ⏳ Awaiting human approval...          [Approve] [Reject]

──────────────────────────────────────────────────────────────
  Ask a question about this investigation
  ┌────────────────────────────────────────────────────────┐
  │ Why did you flag this IBAN specifically?               │
  └────────────────────────────────────────────────────────┘
  🤖 The IBAN GB29NWBK60161331926819 fails the ISO 7064
     mod-97-10 check digit algorithm. The last two digits
     (19) should be (20) based on the preceding sort code
     and account number. This is a data entry error —
     the corrected IBAN is valid and the receiving bank
     (NatWest, UK) is active in our directory.

  ┌────────────────────────────────────────────────────────┐
  │ Are there other payments to this receiver this week?   │
  └────────────────────────────────────────────────────────┘
  🤖 [calls get_payment_record + filters by receiver_bic]
     Found 3 other payments to NatWest this week — all
     settled successfully with the corrected IBAN format.
```

### View 3 — Bottleneck Monitor
In-flight payment health, live alerts, and correspondent degradation heatmap.

```
┌──────────────────────────────────────────────────────────────┐
│ 🔴 ACTIVE ALERT: 4 payments delayed at DEUTDEDB (Deutsche)   │
│ Avg delay: +4.2h over SLA · Systemic pattern detected        │
│ Recommended: Escalate to correspondent ops team   [Escalate] │
├──────────────────────┬───────────────────────────────────────┤
│ In-Flight Payments   │  Corridor Latency Heatmap             │
│ (list w/ risk level) │  (rows: corridor, cols: step,         │
│ 🔴 TX-00155 At-Risk  │   colour: on-track/at-risk/breached)  │
│ 🟡 TX-00148 Watch    │                                        │
│ 🟢 TX-00144 On-Track │                                        │
└──────────────────────┴───────────────────────────────────────┘
```

---

## Demo Script (live presentation)

1. **Slide 1** — Team name + members
2. **Slide 2** — Problem: show a screenshot of a "manual investigation" — 5 tabs open, 45 minutes per case
3. **Live demo:**
   - Trigger scenario 1 (IBAN error) — exception resolved in seconds
   - Trigger scenario 2 (sanctions hit) — compliance agent flags with reasoning
   - Trigger scenario 3 (stuck payment) — bottleneck detected at correspondent, systemic pattern identified
   - Trigger scenario 4 (cut-off risk) — proactive alert fires before SLA breach
4. **Business value** — two metrics: exception resolution time (45 min → seconds) + bottleneck detection (caught proactively vs. after customer complaint)
5. **Architecture slide** — the agent diagram
6. **Responsible AI Assessment** — human approval gate, full audit trail, no autonomous execution

---

## Responsible AI Assessment (required for submission)

- **Human-in-the-loop:** No payment action is executed without explicit human approval. Agent recommends only.
- **Explainability:** Every recommendation includes a plain-English rationale and the tools/data sources consulted.
- **Audit trail:** Full log of agent reasoning steps, tool calls, and data accessed — satisfies regulatory requirements.
- **Bias / false positives:** Compliance agent is designed to flag uncertainty and escalate rather than auto-reject. Reduces, not replaces, human judgment.
- **Bedrock Guardrails (automated layer):** All LLM calls pass through `aws_bedrock_guardrail.pay_investigator` — topic denial blocks non-payment queries, PII anonymization redacts IBANs/SWIFT codes/card numbers/emails, content filters block harmful content at HIGH sensitivity. Guardrail ID `elu2okf0di0w`.
- **Data privacy:** No real PII in demo. In production, would operate within bank's existing data governance perimeter.

---

## Agent Tools & Capabilities

> Implementation: define these as Python functions, register as tool definitions in the Anthropic SDK. Wrap in a FastMCP server if time allows — it makes the MCP story legible to judges and matches the FAQ's emphasis on the protocol.

### Track 1 — Exception Resolution

#### Intake Agent
| Tool | Description |
|---|---|
| `get_payment_record(tx_id)` | Retrieves full payment transaction from mock store |
| `parse_payment_message(raw_message)` | Extracts structured fields from MT103 / pacs.008 |
| `classify_exception(error_code)` | Maps error code to exception type, severity, and routing decision |

#### Investigation Agent
| Tool | Description |
|---|---|
| `get_payment_record(tx_id)` | Full transaction details |
| `get_swift_message(tx_id)` | Raw SWIFT message associated with the payment |
| `get_payment_events(tx_id)` | Full payment lifecycle event log (PAYMENT_RECEIVED → SETTLEMENT_CONFIRMED) |
| `lookup_bic(bic)` | Bank name, country, correspondent relationships |
| `validate_iban(iban)` | Checksum validation + format check |
| `get_error_details(error_code)` | Detailed error description and standard remediation steps |
| `get_account_status(account_id)` | Account standing, restrictions, recent activity |
| `search_knowledge_base(query)` | Semantic search over Bedrock KB (error codes, IBAN registry, sanctions procedure, pacs.008 field guide, SLA policy, duplicate resolution) |

#### Compliance Agent
| Tool | Description |
|---|---|
| `screen_entity(name, country)` | Fuzzy match against sanctions list, returns match score + matched entries |
| `get_sanctions_entry(entity_name)` | Full SDN record for a matched entity |
| `search_adverse_media(entity_name)` | Mock news/adverse media lookup for the entity |
| `get_aml_flags(tx_id)` | Any existing AML flags on the transaction |
| `get_transaction_history(entity_id)` | Prior transaction patterns for context |

#### Technical Diagnosis Agent
| Tool | Description |
|---|---|
| `validate_iban(iban)` | IBAN checksum + format (shared with Investigation Agent) |
| `validate_bic(bic)` | BIC format validation |
| `check_duplicate(reference, amount, sender_bic)` | Detects duplicate payment references |
| `get_fx_limits(currency_pair, amount)` | Checks amount against configured FX limits |
| `validate_iso20022_fields(message)` | Field-level validation against pacs.008 schema |
| `suggest_correction(field, value, error_type)` | Proposes corrected field value for auto-correctable errors |

#### Resolution Agent
| Tool | Description |
|---|---|
| `create_recommendation(findings, action_type)` | Structures the resolution recommendation with rationale |
| `log_case(tx_id, summary, recommendation)` | Writes full investigation to audit trail |
| `submit_for_approval(recommendation_id)` | Puts recommendation in human approval queue (HITL gate) |
| `execute_resolution(resolution_id, approval_token)` | Executes only after human approval — gated |

---

### Track 2 — Bottleneck Detection

#### Monitor Agent
| Tool | Description |
|---|---|
| `get_inflight_payments()` | All in-flight payments with current step and elapsed time |
| `get_sla_benchmark(corridor, rail, step)` | p50 / p95 / breach threshold for a given corridor+step |
| `calculate_risk_level(tx_id)` | On-track / at-risk / breached based on elapsed vs benchmark |
| `poll_payment_status(tx_id)` | Latest status update for a specific payment |

#### Bottleneck Analysis Agent
| Tool | Description |
|---|---|
| `get_payment_lifecycle(tx_id)` | Full step-by-step timeline with timestamps and expected durations |
| `get_correspondent_stats(bic)` | Current processing time stats and operational status for a correspondent |
| `get_corridor_performance(from_currency, to_currency, rail)` | Corridor-level latency metrics |
| `identify_delayed_step(tx_id)` | Pinpoints which step in the journey is causing the delay |

#### Pattern Agent
| Tool | Description |
|---|---|
| `get_payments_via_correspondent(bic, time_window)` | All payments routed through a given correspondent in the window |
| `get_payments_on_rail(rail, time_window)` | All payments on a given rail in the window |
| `calculate_delay_correlation(bic)` | Checks whether multiple payments share a delay at the same point |
| `get_historical_incidents(bic)` | Prior degradation incidents with this correspondent |
| `check_correspondent_status(bic)` | Known operational status: normal / degraded / outage |

#### Alert Agent
| Tool | Description |
|---|---|
| `create_alert(payments_at_risk, root_cause, recommended_action)` | Structured alert with pre-populated context |
| `notify_ops_team(alert_id)` | Sends alert to ops queue |
| `get_escalation_contacts(bic, corridor)` | Who to contact at the correspondent / internally |
| `log_bottleneck_incident(incident_details)` | Records incident for audit trail and future pattern learning |

---

### Track 3 — Report Q&A Chatbot

#### Report Q&A Agent
Conversational agent. System prompt is seeded with the full investigation report from Track 1 or 2. LangGraph checkpointing persists conversation history across turns. Inherits tools from whichever track produced the report.

| Tool | Description |
|---|---|
| `get_payment_record(tx_id)` | Fetch additional payment detail mid-conversation |
| `get_payment_lifecycle(tx_id)` | Pull full step timeline if analyst asks about timing |
| `get_correspondent_stats(bic)` | Look up correspondent history on demand |
| `search_payments(filters)` | Ad-hoc search — "show me other payments from this sender" |
| `get_resolution_history(tx_id)` | Prior cases involving same entity or error type |
| `get_sanctions_entry(entity_name)` | Deep-dive on a flagged entity during conversation |

**LangGraph implementation note:** use `MemorySaver` checkpointer with a `thread_id` per investigation report so each report gets its own isolated conversation context. Switching between reports in the UI switches `thread_id`.

```python
from langgraph.checkpoint.memory import MemorySaver
from langchain_aws import ChatBedrock

llm = ChatBedrock(model_id="anthropic.claude-sonnet-4-6", region_name="us-east-1")
checkpointer = MemorySaver()
# graph = build_qa_graph(llm, tools)
# graph.compile(checkpointer=checkpointer)
# config = {"configurable": {"thread_id": report_id}}
```

---

### MCP Server Strategy

Expose all tools above as a single **PayInvestigator MCP server** with two resource namespaces:

```
payinvestigator/
├── exceptions/        ← Track 1 tools
│   ├── get_payment_record
│   ├── classify_exception
│   ├── screen_entity
│   └── ...
└── monitoring/        ← Track 2 tools
    ├── get_inflight_payments
    ├── get_sla_benchmark
    ├── calculate_delay_correlation
    └── ...
```

**Implementation order:** build as raw Python functions first (fastest), then wrap in FastMCP at the end if time allows. The agent logic doesn't change — only how tools are served.

---

## Concepts + Technology Reference

- **Multi-agent orchestration** — parallel specialist agents (compliance, technical) coordinated by a resolution agent
- **Tool use / function calling** — agents call mock APIs to retrieve payment records, error codes, sanctions data
- **MCP (Model Context Protocol)** — standard interface for agent-to-tool communication
- **ISO 20022 / pacs.008** — payment message standard (MT103 → ISO 20022 migration context)
- **SWIFT gpi / UETR** — unique end-to-end transaction reference for tracking
- **Human-in-the-loop (HITL)** — approval gate before any resolution action is taken
