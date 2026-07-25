// ---------------------------------------------------------------------------
// API client for the PayInvestigator FastAPI backend.
//
// Local dev uses the Vite /api proxy. Production builds can point directly at
// the backend Lambda Function URL via VITE_API_BASE_URL.
// ---------------------------------------------------------------------------

import * as mock from '../mock/data.js';

// The lite deployment's Lambda concurrency ceiling means real responses can
// legitimately take tens of seconds under load. A short timeout here causes
// the UI to give up and silently fall back to static mock data even though
// the backend is up and would have answered. Give real requests room to
// finish before treating the backend as unreachable.
const API_TIMEOUT_MS = 45000;
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function apiUrl(path) {
  return API_BASE_URL ? `${API_BASE_URL}${path}` : path;
}

async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), options.timeout ?? API_TIMEOUT_MS);
  try {
    const res = await fetch(apiUrl(path), { ...options, signal: controller.signal });
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const payload = await res.json();
        message = payload.detail || payload.message || message;
      } catch {
        // ignore parse failure
      }
      throw new ApiError(res.status, message);
    }
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

export const getKpis = () => getJson('/api/metrics/kpis', mock.kpis);
export const getVolume = () => getJson('/api/metrics/volume', mock.volumeSeries);
export const getSavings = () => getJson('/api/metrics/savings', mock.savingsSeries);
export const getExceptionBreakdown = () => getJson('/api/metrics/exceptions', mock.exceptionBreakdown);
export const getCorrespondents = () => getJson('/api/metrics/correspondents', mock.correspondents);
export const getTokenCosts = () => getJson('/api/metrics/token-costs', mock.tokenCostPerType);
export const getThroughput = () => getJson('/api/metrics/throughput', mock.hourlyThroughput);
export const getAiStats = () => getJson('/api/metrics/ai', mock.aiStats);

export const getExceptions = (status = 'active') =>
  getJson(`/api/exceptions?status=${status}`, mock.exceptionQueue);

export const getInvestigationReport = (txId) =>
  getJson(`/api/exceptions/${txId}/report`, null);

export function streamInvestigation(txId, onEvent, onDone) {
  let cancelled = false;

  (async () => {
    try {
      const res = await fetch(apiUrl(`/api/exceptions/${txId}/investigate`), { method: 'POST' });
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
          else if (evt.type === 'limit_reached') final = evt;
          else onEvent(evt);
        }
      }
      if (!cancelled) onDone(final ?? { report_id: `RPT-${txId}`, recommendation: null });
      return;
    } catch {
      /* backend offline → scripted fallback */
    }

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

export function streamLiveInvestigation(txId, onEvent, onDone) {
  let cancelled = false;

  (async () => {
    try {
      const res = await fetch(apiUrl(`/api/exceptions/${txId}/stream`));
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
          else if (evt.type === 'limit_reached') final = evt;
          else onEvent(evt);
        }
      }
      if (!cancelled) onDone(final ?? { report_id: null, recommendation: null });
    } catch {
      if (!cancelled) onDone({ report_id: null, recommendation: null });
    }
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
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      return { answer: err.message, tool: null, source: 'api', limitReached: true };
    }
    const bank = mock.chatAnswers[txId] ?? [];
    const hit = bank.find((c) => c.match.test(message)) ?? mock.chatAnswers.default[0];
    await new Promise((r) => setTimeout(r, 700));
    return { answer: hit.answer, tool: hit.tool, source: 'mock' };
  }
}

export async function generateDemoPayments() {
  try {
    const res = await apiFetch('/api/demo/generate', { method: 'POST', timeout: 30000 });
    const data = await res.json();
    return { generated: data.generated ?? 0, source: 'api' };
  } catch {
    await new Promise((r) => setTimeout(r, 1800));
    return { generated: 25, source: 'mock' };
  }
}

export const getInflight = () => getJson('/api/monitoring/inflight', mock.inflightPayments);
export const getAlerts = () => getJson('/api/monitoring/alerts', mock.activeAlerts);
export const getHeatmap = () => getJson('/api/monitoring/heatmap', mock.heatmap);

export async function probeBackend() {
  try {
    // Use the full API_TIMEOUT_MS here (not a short override) so a genuinely
    // live-but-cold-starting backend isn't mistaken for an offline one.
    await apiFetch('/api/metrics/kpis');
    return true;
  } catch {
    return false;
  }
}
