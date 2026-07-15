// ---------------------------------------------------------------------------
// Mock datasets — mirror the data model from the Implementation Plan.
// Used as automatic fallback whenever the FastAPI backend (/api/*) is
// unreachable, so the full demo works standalone.
// ---------------------------------------------------------------------------

// KPI row.
//
// DATA CONTRACT (for backend engineer): GET /api/metrics/kpis
//   in_flight       → total payments in the system (mock rails + generated CBPR+)
//   exceptions_open → exceptions awaiting human approve/reject
//   settlement_risk → open, unprocessed exceptions whose interbank settlement
//                     date (IntrBkSttlmDt) is at risk vs. current processing time —
//                     i.e. they will miss settlement if not approved/rejected in time
export const kpis = {
  in_flight: 142,
  exceptions_open: 23,
  settlement_risk: 4,
  mttr_before: '38m',
  mttr_now: '2s',
};

// Transaction volume time series — hourly, by rail.
//
// DATA CONTRACT (for backend engineer):
//   sepa_sct + fedwire  → static mock rails, CONSTANT per hour (not simulated)
//   swift_cbpr          → the ONLY dynamic series: fill from generated
//                         SWIFT CBPR+ message load per hour
//   exceptions          → exception count per hour (from simulation)
// The chart stacks all three rails; only swift_cbpr varies with load.
export const volumeSeries = [
  { hour: '08:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 42, exceptions: 3 },
  { hour: '09:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 67, exceptions: 5 },
  { hour: '10:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 81, exceptions: 4 },
  { hour: '11:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 96, exceptions: 7 },
  { hour: '12:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 88, exceptions: 6 },
  { hour: '13:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 79, exceptions: 4 },
  { hour: '14:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 93, exceptions: 9 },
  { hour: '15:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 104, exceptions: 8 },
  { hour: '16:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 99, exceptions: 6 },
  { hour: '17:00', sepa_sct: 120, fedwire: 45, swift_cbpr: 76, exceptions: 5 },
];

// Cost savings per pre-check case — hourly, by rail (USD).
//
// DATA CONTRACT (for backend engineer):
//   baseline    → manual investigation cost per case. CONSTANT:
//                 industry range $15–$40 → midpoint $27.50 used as baseline
//   swift_cbpr  → the ONLY dynamic series: baseline − actual LLM token cost
//                 of the agent investigation, per resolved case in that hour
//   sepa_sct + fedwire → static mock rails, CONSTANT per hour (not simulated)
// Savings per case = baseline − token cost, so values sit just below baseline.
export const MANUAL_COST_RANGE = { min: 15, max: 40, baseline: 27.5 };

export const savingsSeries = [
  { hour: '08:00', swift_cbpr: 27.21, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '09:00', swift_cbpr: 27.08, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '10:00', swift_cbpr: 27.17, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '11:00', swift_cbpr: 26.84, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '12:00', swift_cbpr: 27.02, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '13:00', swift_cbpr: 27.19, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '14:00', swift_cbpr: 26.62, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '15:00', swift_cbpr: 26.91, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '16:00', swift_cbpr: 27.11, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
  { hour: '17:00', swift_cbpr: 27.24, sepa_sct: 27.1, fedwire: 26.9, baseline: 27.5 },
];

// Exception breakdown — by type (8 CBPR+ exception criteria).
//
// DATA CONTRACT (for backend engineer):
//   approved → agent proposed a fix AND the human operator approved it (HITL)
//   rejected → the human operator rejected the agent's recommendation (HITL)
//   count    → total investigated for this type (approved + rejected + pending)
export const exceptionBreakdown = [
  { type: 'Bad IBAN (checksum)', count: 9, approved: 7, rejected: 2, avg_resolution_min: 0.2 },
  { type: 'Invalid BIC', count: 4, approved: 3, rejected: 1, avg_resolution_min: 0.3 },
  { type: 'Duplicate UETR', count: 5, approved: 4, rejected: 1, avg_resolution_min: 0.3 },
  { type: 'Sanctions name hit', count: 3, approved: 1, rejected: 2, avg_resolution_min: 4.1 },
  { type: 'Missing mandatory field', count: 4, approved: 3, rejected: 1, avg_resolution_min: 0.4 },
  { type: 'Invalid currency/amount', count: 3, approved: 2, rejected: 1, avg_resolution_min: 0.5 },
  { type: 'FX limit breach', count: 2, approved: 1, rejected: 1, avg_resolution_min: 2.8 },
  { type: 'Cut-off / value date', count: 2, approved: 2, rejected: 0, avg_resolution_min: 1.1 },
];

// Correspondent health table
export const correspondents = [
  { bic: 'DEUTDEDB', bank: 'Deutsche Bank', country: 'DE', status: 'degraded', avg_processing_min: 372, delayed: 4 },
  { bic: 'CHASUS33', bank: 'JPMorgan Chase', country: 'US', status: 'normal', avg_processing_min: 41, delayed: 0 },
  { bic: 'BNPAFRPP', bank: 'BNP Paribas', country: 'FR', status: 'normal', avg_processing_min: 28, delayed: 0 },
  { bic: 'HSBCGB2L', bank: 'HSBC', country: 'GB', status: 'normal', avg_processing_min: 35, delayed: 1 },
  { bic: 'DBSSSGSG', bank: 'DBS Bank', country: 'SG', status: 'normal', avg_processing_min: 52, delayed: 0 },
  { bic: 'UBSWCHZH', bank: 'UBS', country: 'CH', status: 'normal', avg_processing_min: 19, delayed: 0 },
];

// Avg LLM token cost per exception type (USD per agent investigation).
//
// DATA CONTRACT (for backend engineer): GET /api/metrics/token-costs
//   avg_token_cost_usd → average LLM token spend per investigation of this type
export const tokenCostPerType = [
  { type: 'Bad IBAN (checksum)', avg_token_cost_usd: 0.09 },
  { type: 'Invalid BIC', avg_token_cost_usd: 0.07 },
  { type: 'Duplicate UETR', avg_token_cost_usd: 0.11 },
  { type: 'Sanctions name hit', avg_token_cost_usd: 0.88 },
  { type: 'Missing mandatory field', avg_token_cost_usd: 0.14 },
  { type: 'Invalid currency/amount', avg_token_cost_usd: 0.12 },
  { type: 'FX limit breach', avg_token_cost_usd: 0.31 },
  { type: 'Cut-off / value date', avg_token_cost_usd: 0.18 },
];

// Exceptions detected vs. resolved per hour.
//
// DATA CONTRACT (for backend engineer): GET /api/metrics/throughput
//   detected → exceptions raised in that hour (from generated CBPR+ load)
//   resolved → cases closed in that hour (agent fix + human approve/reject)
export const hourlyThroughput = [
  { hour: '08:00', detected: 3, resolved: 2 },
  { hour: '09:00', detected: 5, resolved: 5 },
  { hour: '10:00', detected: 4, resolved: 4 },
  { hour: '11:00', detected: 7, resolved: 6 },
  { hour: '12:00', detected: 6, resolved: 7 },
  { hour: '13:00', detected: 4, resolved: 4 },
  { hour: '14:00', detected: 9, resolved: 8 },
  { hour: '15:00', detected: 8, resolved: 8 },
  { hour: '16:00', detected: 6, resolved: 7 },
  { hour: '17:00', detected: 5, resolved: 5 },
];

// AI performance stats
export const aiStats = {
  total_investigations: 214,
  auto_resolved: 161,
  escalated_to_human: 53,
  recommendation_acceptance_rate: 0.94,
  avg_investigation_seconds: 11,
};

// ---------------------------------------------------------------------------
// View 2 — Exception queue
// ---------------------------------------------------------------------------

export const exceptionQueue = [
  {
    tx_id: 'TX-00142', type: 'Bad IBAN', type_key: 'iban', amount: '€42,000',
    sender: 'Norfolk Precision Engineering Ltd', receiver: 'Hartley Components Ltd', status: 'pending',
    settlement_date: null,
    precheck_summary: { action_hint: 'IBAN checksum mismatch on creditor account. Route to Technical Diagnosis.', needs_technical: true, needs_compliance: false },
  },
  {
    tx_id: 'TX-00138', type: 'Sanctions hit', type_key: 'sanctions', amount: '$198,500',
    sender: 'Global Trade Partners LLC', receiver: 'Novaya Star Shipping', status: 'pending',
    settlement_date: null,
    precheck_summary: { action_hint: 'Possible SDN match on receiver entity name. Route to Compliance Agent.', needs_technical: false, needs_compliance: true },
  },
  {
    tx_id: 'TX-00136', type: 'ISO 20022 field', type_key: 'iso', amount: '¥8,400,000',
    sender: 'Pacific Rim Trading Co', receiver: 'Pacific Imports Inc', status: 'pending',
    settlement_date: null,
    precheck_summary: { action_hint: 'Mandatory creditor country field missing from pacs.008 message.', needs_technical: true, needs_compliance: false },
  },
  {
    tx_id: 'TX-00131', type: 'FX limit breach', type_key: 'fx', amount: '$2,450,000',
    sender: 'Meridian Capital', receiver: 'Andes Mining SA', status: 'pending',
    settlement_date: null,
    precheck_summary: null,
  },
  {
    tx_id: 'TX-00121', type: 'Duplicate ref', type_key: 'duplicate', amount: '£7,200',
    sender: 'Thames Logistics', receiver: 'Clyde Freight Ltd', status: 'resolved',
    settlement_date: null,
    precheck_summary: null,
  },
];

// Scripted agent investigation streams (fallback when backend is offline).
// Shape matches the SSE events the FastAPI backend will emit:
//   { agent, kind: 'reasoning' | 'tool', text }
export const investigationScripts = {
  'TX-00142': {
    report_id: 'RPT-0142',
    steps: [
      { agent: 'Intake Agent', cls: 'intake', text: 'Classified exception as IBAN_CHECKSUM_ERROR (severity: low, auto-correctable candidate)' },
      { agent: 'tool', cls: 'tool', text: '↳ get_payment_record("TX-00142")' },
      { agent: 'Investigation Agent', cls: 'investigation', text: 'Pulled payment record TX-00142 — €42,000 EUR→GBP via SWIFT gpi, sender Norfolk Precision Engineering Ltd' },
      { agent: 'tool', cls: 'tool', text: '↳ validate_iban("GB29NWBK60161331926819")' },
      { agent: 'Technical Diagnosis', cls: 'technical', text: 'IBAN GB29NWBK60161331926819 fails ISO 7064 mod-97-10 check. Check digits (19) inconsistent with BBAN.' },
      { agent: 'tool', cls: 'tool', text: '↳ lookup_bic("NWBKGB2L") → NatWest Bank, GB, status: active' },
      { agent: 'tool', cls: 'tool', text: '↳ suggest_correction("receiver_iban", …, "IBAN_CHECKSUM_ERROR")' },
      { agent: 'Technical Diagnosis', cls: 'technical', text: 'Proposed correction: GB29NWBK60161331926820 — passes mod-97, account structure valid for NatWest sort code 601613' },
      { agent: 'Resolution Agent', cls: 'resolution', text: 'Recommendation: CORRECT_AND_RESUBMIT. Confidence 0.98. Data-entry error, receiving bank active, 3 prior settled payments to same receiver.' },
    ],
    recommendation: {
      action: 'Correct IBAN → GB29NWBK60161331926820 and resubmit payment',
      rationale: 'Checksum failure is a data-entry error; corrected IBAN is valid and receiver bank (NatWest, UK) is active in the directory.',
    },
  },
  'TX-00138': {
    report_id: 'RPT-0138',
    steps: [
      { agent: 'Intake Agent', cls: 'intake', text: 'Classified exception as SANCTIONS_SCREENING_HIT (severity: high, routing → Compliance Agent)' },
      { agent: 'tool', cls: 'tool', text: '↳ get_payment_record("TX-00138")' },
      { agent: 'Investigation Agent', cls: 'investigation', text: 'Pulled payment record TX-00138 — $198,500 USD→AED via SWIFT gpi, receiver "Novaya Star Shipping"' },
      { agent: 'tool', cls: 'tool', text: '↳ screen_entity("Novaya Star Shipping", "AE")' },
      { agent: 'Compliance Agent', cls: 'compliance', text: 'Fuzzy match 87% against SDN entry "NOVAYA ZVEZDA SHIPPING LLC" (aliases: Novaya Star, NZ Shipping) — list: OFAC SDN, program: RUSSIA-EO14024' },
      { agent: 'tool', cls: 'tool', text: '↳ search_adverse_media("Novaya Star Shipping")' },
      { agent: 'Compliance Agent', cls: 'compliance', text: '2 adverse media hits (2025): alleged sanctions evasion via UAE re-registration. Entity profile shows ownership overlap with listed entity.' },
      { agent: 'tool', cls: 'tool', text: '↳ get_transaction_history("Novaya Star Shipping") → no prior transactions with this institution' },
      { agent: 'Resolution Agent', cls: 'resolution', text: 'Recommendation: HOLD + ESCALATE to compliance officer. Match score above 85% threshold; first-time counterparty; adverse media corroborates.' },
    ],
    recommendation: {
      action: 'Hold payment and escalate to compliance officer',
      rationale: '87% SDN match with corroborating adverse media. Not auto-rejected — human review required per policy (reduce false positives, keep human judgment).',
    },
  },
  'TX-00136': {
    report_id: 'RPT-0136',
    steps: [
      { agent: 'Intake Agent', cls: 'intake', text: 'Classified exception as ISO20022_MANDATORY_FIELD_MISSING (severity: low)' },
      { agent: 'tool', cls: 'tool', text: '↳ get_swift_message("TX-00136") → pacs.008' },
      { agent: 'tool', cls: 'tool', text: '↳ validate_iso20022_fields(message)' },
      { agent: 'Technical Diagnosis', cls: 'technical', text: 'Missing mandatory field: /Document/FIToFICstmrCdtTrf/CdtTrfTxInf/Cdtr/PstlAdr/Ctry (creditor country). All other 47 fields valid.' },
      { agent: 'tool', cls: 'tool', text: '↳ lookup_bic("BOTKJPJT") → MUFG Bank, JP' },
      { agent: 'Technical Diagnosis', cls: 'technical', text: 'Creditor country derivable from creditor agent BIC (JP) and creditor IBAN country prefix. Suggested repair: Ctry = "US" from creditor postal address block.' },
      { agent: 'Resolution Agent', cls: 'resolution', text: 'Recommendation: FIELD_REPAIR — populate Cdtr/PstlAdr/Ctry = "US" and revalidate. Confidence 0.95.' },
    ],
    recommendation: {
      action: 'Repair missing creditor country field (Ctry = "US") and revalidate message',
      rationale: 'Field value unambiguously derivable from existing message data; repair follows CBPR+ usage guidelines.',
    },
  },
  'TX-00131': {
    report_id: 'RPT-0131',
    steps: [
      { agent: 'Intake Agent', cls: 'intake', text: 'Classified exception as FX_LIMIT_BREACH (severity: medium, routing → Technical + Compliance in parallel)' },
      { agent: 'tool', cls: 'tool', text: '↳ get_fx_limits("USD/CLP", 2450000)' },
      { agent: 'Technical Diagnosis', cls: 'technical', text: 'Amount $2.45M exceeds configured desk limit $2.0M for USD/CLP corridor (utilization 122%).' },
      { agent: 'tool', cls: 'tool', text: '↳ get_aml_flags("TX-00131") → none' },
      { agent: 'Compliance Agent', cls: 'compliance', text: 'No AML flags. Counterparty Andes Mining SA: 14 prior settled payments, avg $1.8M — amount consistent with history.' },
      { agent: 'Resolution Agent', cls: 'resolution', text: 'Recommendation: ROUTE_FOR_REVIEW to FX desk — limit exception approval or split execution. No compliance concern.' },
    ],
    recommendation: {
      action: 'Route to FX desk for limit exception approval (or split execution)',
      rationale: 'Clean counterparty history, no AML flags; breach is a configured desk limit, not a regulatory block.',
    },
  },
  'TX-00121': {
    report_id: 'RPT-0121',
    steps: [
      { agent: 'Intake Agent', cls: 'intake', text: 'Classified exception as DUPLICATE_PAYMENT_REFERENCE' },
      { agent: 'tool', cls: 'tool', text: '↳ check_duplicate("INV-2026-0711", £7,200, "RBOSGB2L")' },
      { agent: 'Technical Diagnosis', cls: 'technical', text: 'Exact duplicate found: TX-00119, same reference, amount, sender — settled 42 minutes earlier.' },
      { agent: 'Resolution Agent', cls: 'resolution', text: 'Recommendation: CANCEL duplicate TX-00121. Original TX-00119 already settled.' },
    ],
    recommendation: {
      action: 'Cancel duplicate payment TX-00121 (original TX-00119 settled)',
      rationale: 'Reference, amount and sender identical; original settled 42 minutes earlier.',
    },
  },
};

// Canned chatbot answers (fallback). Backend equivalent: POST /api/reports/{id}/chat
export const chatAnswers = {
  'TX-00142': [
    {
      match: /why|warum|flag|iban/i,
      tool: null,
      answer: 'The IBAN GB29NWBK60161331926819 fails the ISO 7064 mod-97-10 check digit algorithm. The check digits (19) should be (20) based on the preceding sort code and account number. This is a data-entry error — the corrected IBAN is valid and the receiving bank (NatWest, UK) is active in our directory.',
    },
    {
      match: /other payments|receiver|sender|week|weitere/i,
      tool: 'get_payment_record + filter by receiver_bic',
      answer: 'Found 3 other payments to NatWest this week — all settled successfully with the corrected IBAN format. Same receiver account, amounts £12,400 / £8,950 / £15,300.',
    },
    {
      match: /approve|happens|correction|genehmig/i,
      tool: 'get_resolution_history("TX-00142")',
      answer: 'On approval, the Resolution Agent calls execute_resolution() with your approval token: the receiver IBAN is corrected to GB29NWBK60161331926820, the payment is resubmitted to the SWIFT gpi rail, and the full correction is written to the audit trail with your user ID as approver.',
    },
  ],
  'TX-00138': [
    {
      match: /why|warum|hold|recommend/i,
      tool: null,
      answer: 'The receiver "Novaya Star Shipping" fuzzy-matched at 87% against the OFAC SDN entry "NOVAYA ZVEZDA SHIPPING LLC" (program RUSSIA-EO14024). Two adverse media reports from 2025 allege sanctions evasion via UAE re-registration, and this is a first-time counterparty. Policy requires hold + human escalation above the 85% match threshold — the agent never auto-rejects.',
    },
    {
      match: /history|deutsche|delays|prior/i,
      tool: 'get_transaction_history("Novaya Star Shipping")',
      answer: 'No prior transactions with this counterparty at our institution. The sender, Global Trade Partners LLC, has 27 settled payments over 14 months with no compliance flags.',
    },
    {
      match: /alias|sdn|entry|match/i,
      tool: 'get_sanctions_entry("NOVAYA ZVEZDA SHIPPING LLC")',
      answer: 'Full SDN record: NOVAYA ZVEZDA SHIPPING LLC — aliases "Novaya Star", "NZ Shipping"; country: RU (re-registered AE 2024); list: OFAC SDN; program: RUSSIA-EO14024; vessel ownership links to two listed entities.',
    },
  ],
  default: [
    {
      match: /./,
      tool: 'search_payments(filters)',
      answer: 'Based on the investigation report context: I can pull additional payment records, lifecycle timelines, correspondent stats or sanctions entries. Try asking about the recommendation rationale, related payments, or what happens after approval.',
    },
  ],
};

// ---------------------------------------------------------------------------
// View 3 — Bottleneck monitor
// ---------------------------------------------------------------------------

export const activeAlerts = [
  {
    id: 'AL-0031',
    severity: 'critical',
    title: '4 payments delayed at DEUTDEDB (Deutsche Bank)',
    detail: 'Avg delay +4.2h over SLA · Systemic pattern detected across USD→SGD corridor · Root cause hypothesis: FX conversion step at intermediary',
    recommended: 'Escalate to correspondent ops team',
    payments: ['TX-00155', 'TX-00151', 'TX-00149', 'TX-00147'],
  },
  {
    id: 'AL-0032',
    severity: 'warning',
    title: 'Cut-off risk: TX-00160 approaching correspondent cut-off in 20 min',
    detail: 'No processing confirmation from DBSSSGSG yet · SLA deadline 17:30 SGT · Alert fired proactively before breach',
    recommended: 'Request status via gpi tracker / prioritize with correspondent',
    payments: ['TX-00160'],
  },
];

export const inflightPayments = [
  { tx_id: 'TX-00155', corridor: 'USD→SGD', step: 'FX conversion @ DEUTDEDB', elapsed_min: 372, sla_min: 120, risk: 'breached' },
  { tx_id: 'TX-00151', corridor: 'USD→SGD', step: 'Processing @ DEUTDEDB', elapsed_min: 305, sla_min: 120, risk: 'breached' },
  { tx_id: 'TX-00160', corridor: 'EUR→SGD', step: 'Sent to correspondent', elapsed_min: 96, sla_min: 120, risk: 'at-risk' },
  { tx_id: 'TX-00149', corridor: 'USD→SGD', step: 'Processing @ DEUTDEDB', elapsed_min: 214, sla_min: 120, risk: 'breached' },
  { tx_id: 'TX-00148', corridor: 'USD→JPY', step: 'Validation', elapsed_min: 41, sla_min: 60, risk: 'at-risk' },
  { tx_id: 'TX-00147', corridor: 'USD→SGD', step: 'FX conversion @ DEUTDEDB', elapsed_min: 188, sla_min: 120, risk: 'breached' },
  { tx_id: 'TX-00144', corridor: 'GBP→EUR', step: 'Settlement', elapsed_min: 12, sla_min: 45, risk: 'on-track' },
  { tx_id: 'TX-00143', corridor: 'EUR→CHF', step: 'Processing', elapsed_min: 8, sla_min: 30, risk: 'on-track' },
];

// Corridor latency heatmap — status per lifecycle step
export const heatmap = {
  steps: ['Submitted', 'Validated', 'To correspondent', 'Processing', 'Settled'],
  rows: [
    { corridor: 'USD→SGD', cells: ['ok', 'ok', 'warn', 'bad', 'bad'] },
    { corridor: 'EUR→SGD', cells: ['ok', 'ok', 'warn', 'warn', 'ok'] },
    { corridor: 'EUR→USD', cells: ['ok', 'ok', 'ok', 'ok', 'ok'] },
    { corridor: 'USD→JPY', cells: ['ok', 'warn', 'ok', 'ok', 'ok'] },
    { corridor: 'GBP→EUR', cells: ['ok', 'ok', 'ok', 'ok', 'ok'] },
    { corridor: 'USD→BRL', cells: ['ok', 'ok', 'ok', 'warn', 'ok'] },
  ],
};
