// ---------------------------------------------------------------------------
// API client for the PayInvestigator FastAPI backend.
//
// Every call targets the planned /api/* endpoints. If the backend is not
// reachable (hackathon dev, demo without infra), the client transparently
// falls back to the bundled mock datasets, so the UI always works.
//
// Planned backend endpoints (FastAPI):
//   GET  /api/metrics/kpis
//   GET  /api/metrics/volume
//   GET  /api/metrics/savings
//   GET  /api/metrics/exceptions
//   GET  /api/metrics/correspondents
//   GET  /api/metrics/ai
//   GET  /api/exceptions
//   POST /api/exceptions/{tx_id}/investigate     (SSE stream of agent events)
//   POST /api/resolutions/{report_id}/approve
//   POST /api/resolutions/{report_id}/reject
//   POST /api/reports/{report_id}/chat
//   GET  /api/monitoring/inflight
//   GET  /api/monitoring/alerts
//   GET  /api/monitoring/heatmap
// ---------------------------------------------------------------------------

import * as mock from '../mock/data.js';

const API_TIMEOUT_MS = 2500;

async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), options.timeout ?? API_TIMEOUT_MS);
  try {
    const res = await fetch(path, { ...options, signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res;
  } finally {
    clearTimeout(t);
  }
}

async function getJson(path, fallback) {
  try {
    const res = await apiFetch(path);
    return { data: await res.json(), source: 'api' };
  } catch {
    return { data: fallback, source: 'mock' };
  }
}

// ---------- Dashboard ----------
export const getKpis = () => getJson('/api/metrics/kpis', mock.kpis);
export const getVolume = () => getJson('/api/metrics/volume', mock.volumeSeries);
export const getSavings = () => getJson('/api/metrics/savings', mock.savingsSeries);
export const getExceptionBreakdown = () => getJson('/api/metrics/exceptions', mock.exceptionBreakdown);
export const getCorrespondents = () => getJson('/api/metrics/correspondents', mock.correspondents);
export const getTokenCosts = () => getJson('/api/metrics/token-costs', mock.tokenCostPerType);
export const getThroughput = () => getJson('/api/metrics/throughput', mock.hourlyThroughput);
export const getAiStats = () => getJson('/api/metrics/ai', mock.aiStats);

// ---------- Exceptions ----------
export const getExceptions = (status = 'active') =>
  getJson(`/api/exceptions?status=${status}`, mock.exceptionQueue);

/**
 * Streams an agent investigation.
 * Tries the backend SSE endpoint first; falls back to replaying the
 * scripted investigation with realistic timing.
 *
 * onEvent({agent, cls, text})   — one reasoning/tool line
 * onDone({report_id, recommendation}) — investigation complete, HITL gate
 * Returns a cancel() function.
 */
export function streamInvestigation(txId, onEvent, onDone) {
  let cancelled = false;

  (async () => {
    // --- try real backend (SSE over fetch) ---
    // Note: no AbortController timeout here — investigations take 30-90s.
    // The outer `cancelled` flag handles user-initiated cancellation.
    try {
      const res = await fetch(`/api/exceptions/${txId}/investigate`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let final = null;
      while (!cancelled) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const evt = JSON.parse(line.slice(5));
          if (evt.type === 'done') final = evt;
          else onEvent(evt);
        }
      }
      if (!cancelled) onDone(final ?? { report_id: `RPT-${txId}`, recommendation: null });
      return;
    } catch {
      /* backend offline → scripted fallback */
    }

    // --- scripted fallback (only if a script exists for this exact TX) ---
    const script = mock.investigationScripts[txId];
    if (!script) {
      onEvent({ agent: 'System', cls: 'technical', text: `Backend unavailable — cannot investigate ${txId} in offline mode.` });
      onDone({ report_id: `RPT-${txId}`, recommendation: null });
      return;
    }
    for (const step of script.steps) {
      if (cancelled) return;
      await new Promise((r) => setTimeout(r, step.cls === 'tool' ? 450 : 950));
      if (cancelled) return;
      onEvent(step);
    }
    await new Promise((r) => setTimeout(r, 600));
    if (!cancelled) onDone({ report_id: script.report_id, recommendation: script.recommendation });
  })();

  return () => { cancelled = true; };
}

export async function submitDecision(reportId, decision) {
  try {
    await apiFetch(`/api/resolutions/${reportId}/${decision}`, { method: 'POST' });
    return { source: 'api' };
  } catch {
    return { source: 'mock' };
  }
}

/** Report Q&A chat. Backend first, canned answers as fallback. */
export async function sendChat(reportId, txId, message) {
  try {
    const res = await apiFetch(`/api/reports/${reportId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      timeout: 20000,
    });
    const data = await res.json();
    return { answer: data.answer, tool: data.tool ?? null, source: 'api' };
  } catch {
    const bank = mock.chatAnswers[txId] ?? [];
    const hit = bank.find((c) => c.match.test(message)) ?? mock.chatAnswers.default[0];
    await new Promise((r) => setTimeout(r, 700));
    return { answer: hit.answer, tool: hit.tool, source: 'mock' };
  }
}

// ---------- Demo payment generator ----------
/**
 * Triggers the demo payment generator.
 * Backend contract: POST /api/demo/generate → { generated: <count> }
 * (generator creates CBPR+ payments, writes them to the DB, agent picks them up)
 */
export async function generateDemoPayments() {
  try {
    const res = await apiFetch('/api/demo/generate', { method: 'POST', timeout: 30000 });
    const data = await res.json();
    return { generated: data.generated ?? 0, source: 'api' };
  } catch {
    await new Promise((r) => setTimeout(r, 1800)); // simulate generator run
    return { generated: 25, source: 'mock' };
  }
}

// ---------- Monitoring ----------
export const getInflight = () => getJson('/api/monitoring/inflight', mock.inflightPayments);
export const getAlerts = () => getJson('/api/monitoring/alerts', mock.activeAlerts);
export const getHeatmap = () => getJson('/api/monitoring/heatmap', mock.heatmap);

/** Lightweight connectivity probe for the header badge. */
export async function probeBackend() {
  try {
    await apiFetch('/api/health', { timeout: 1500 });
    return true;
  } catch {
    return false;
  }
}
