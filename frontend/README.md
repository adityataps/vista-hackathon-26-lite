# PayInvestigator — Frontend

React + Recharts dashboard for the Vista Hackathon implementation plan
(AI-powered payment exception investigation & bottleneck monitoring).

## Views

1. **Operations Dashboard** — KPI row, hourly transaction volume by rail, latency
   percentiles vs. 7-day benchmark, exception breakdown, correspondent health.
2. **Exception Queue** — live queue; clicking *Investigate* streams the multi-agent
   reasoning (Intake → Investigation → Compliance/Technical → Resolution), ends in a
   human-in-the-loop Approve/Reject gate, then opens the Report Q&A chatbot.
3. **Bottleneck Monitor** — active alerts (systemic correspondent delay, cut-off risk),
   in-flight payments ranked by SLA risk, corridor×step latency heatmap.

## Backend integration

All data flows through `src/api/client.js`, which targets the FastAPI
endpoints (`/api/*`). Local dev uses the Vite proxy; production builds can point
directly at the backend Lambda Function URL via `VITE_API_BASE_URL`. If the backend
is unreachable, every call **falls back to bundled mock data automatically** — the
header badge shows "Demo mode (mock data)" vs. "Backend live".

Expected backend endpoints:

```
GET  /health
GET  /api/metrics/{kpis|volume|latency|exceptions|correspondents|ai}
GET  /api/exceptions
POST /api/exceptions/{tx_id}/investigate    ← SSE: data:{agent,cls,text} … data:{type:"done",report_id,recommendation}
POST /api/resolutions/{report_id}/{approve|reject}
POST /api/reports/{report_id}/chat          ← {message} → {answer, tool?}
GET  /api/monitoring/{inflight|alerts|heatmap}
```

## Run

```bash
npm install
npm run dev        # http://localhost:5173, /api proxied to localhost:8080
npm run build      # production build → dist/
```

## Deployment

Production builds are synced to the public S3 website bucket provisioned by Terraform:

```bash
VITE_API_BASE_URL=https://<function-id>.lambda-url.us-east-1.on.aws npm run build
aws s3 sync dist/ s3://payinvestigator-frontend-<account_id> --delete
```
