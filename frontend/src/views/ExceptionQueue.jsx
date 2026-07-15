import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getExceptions, getInvestigationReport, streamLiveInvestigation, submitDecision, sendChat } from '../api/client.js';

function Md({ children }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>;
}

const TYPE_PILL = {
  iban: 'blue', sanctions: 'red', iso: 'blue', fx: 'yellow', duplicate: 'gray',
};

const STATUS_PILL = {
  pending:           { cls: 'yellow',  label: 'Pending' },
  evaluating:        { cls: 'yellow',  label: 'Evaluating…', spinner: true },
  investigating:     { cls: 'blue',    label: 'Investigating', spinner: true },
  awaiting_approval: { cls: 'orange',  label: 'Awaiting Approval' },
  resolved:          { cls: 'green',   label: 'Resolved' },
  rejected:          { cls: 'gray',    label: 'Rejected' },
};

const SUGGESTIONS = {
  iban:      ['Why did you flag this IBAN specifically?', 'What is the corrected IBAN?', 'Are there other payments to this receiver this week?'],
  sanctions: ['Why did you recommend holding this payment?', 'Show me the full SDN entry for the match', 'Does the sender have prior compliance flags?'],
  duplicate: ['Which payment is the duplicate?', 'Should I cancel the second one or the first?', 'Was the UETR reused or is this a business duplicate?'],
  fx:        ['What FX limit was breached?', 'What is the approved limit for this corridor?', 'Has this sender exceeded limits before?'],
  iso:       ['Which mandatory field is missing?', 'Can the field be derived from other payment data?', 'What happens if I approve the repair?'],
  default:   ['Why this recommendation?', 'Show related payments from the same sender', 'What is the risk if I approve this?'],
};

const SORT_OPTIONS = [
  { value: 'default',      label: 'Default order' },
  { value: 'priority',     label: 'Priority (pending first)' },
  { value: 'amount-desc',  label: 'Amount ↓' },
  { value: 'amount-asc',   label: 'Amount ↑' },
  { value: 'sla',          label: 'SLA (soonest first)' },
];

const TYPE_FILTERS = ['all', 'iban', 'sanctions', 'duplicate', 'fx', 'iso'];

function timeAgo(iso) {
  if (!iso) return '—';
  const secs = (Date.now() - new Date(iso)) / 1000;
  if (secs < 60)   return `${Math.round(secs)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

function settlementCell(iso) {
  if (!iso) return { label: '—', cls: null, urgent: false };
  const d = new Date(iso);
  const hoursUntil = (d - Date.now()) / 3_600_000;
  const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  if (hoursUntil >= 0 && hoursUntil <= 24)  return { label, cls: 'sla-red',    urgent: true };
  if (hoursUntil >= 0 && hoursUntil <= 72)  return { label, cls: 'sla-yellow', urgent: false };
  if (hoursUntil < 0)                       return { label, cls: 'sla-past',   urgent: false };
  return { label, cls: null, urgent: false };
}

export default function ExceptionQueue() {
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(null);
  const [lines, setLines] = useState([]);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);
  const [decision, setDecision] = useState(null);
  const [chat, setChat] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [archive, setArchive] = useState([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [sortBy, setSortBy] = useState('default');
  const [typeFilter, setTypeFilter] = useState('all');
  const [animatingIds, setAnimatingIds] = useState(new Set());

  const cancelRef = useRef(null);
  const streamRef = useRef(null);
  const chatEndRef = useRef(null);
  const runningRef = useRef(false);
  const knownIdsRef = useRef(new Set());
  const modalOpenRef = useRef(false);

  useEffect(() => { modalOpenRef.current = modalOpen; }, [modalOpen]);

  function loadStoredReport(row) {
    getInvestigationReport(row.tx_id).then(({ data }) => {
      if (!data) return;
      setLines(data.steps || []);
      setReport({ report_id: data.report_id, recommendation: data.recommendation });
      runningRef.current = false;
      setRunning(false);
    });
  }

  function investigate(row) {
    cancelRef.current?.();
    setSelected(row);
    setLines([]);
    setReport(null);
    setDecision(null);
    setChat([]);

    const done = ['awaiting_approval', 'resolved', 'rejected'];
    if (done.includes(row.status)) {
      runningRef.current = false;
      setRunning(false);
      loadStoredReport(row);
      return;
    }

    if (row.status === 'investigating' || row.status === 'evaluating') {
      runningRef.current = true;
      setRunning(true);
      if (row.status === 'evaluating') {
        setLines([{ agent: 'System', cls: 'intake', text: 'Precheck evaluating — investigation will start shortly…' }]);
      }
      cancelRef.current = streamLiveInvestigation(
        row.tx_id,
        (evt) => setLines((prev) => {
          if (evt.cls === 'tool') return [...prev, evt];
          const last = prev[prev.length - 1];
          if (last && last.agent === evt.agent && last.cls === evt.cls) {
            return [...prev.slice(0, -1), { ...last, text: last.text + evt.text }];
          }
          return [...prev, evt];
        }),
        (final) => {
          runningRef.current = false;
          setRunning(false);
          if (final?.report_id) {
            setReport({ report_id: final.report_id, recommendation: final.recommendation });
          }
        },
      );
      return;
    }

    setLines([{ agent: 'System', cls: 'intake', text: 'Queued for investigation — results will appear here when ready.' }]);
    runningRef.current = false;
    setRunning(false);
  }

  function openModal(row) {
    setModalOpen(true);
    investigate(row);
  }

  function closeModal() {
    if (runningRef.current) cancelRef.current?.();
    setModalOpen(false);
    setSelected(null);
    setLines([]);
    setReport(null);
    setDecision(null);
  }

  function fetchQueue() {
    getExceptions('active').then(({ data }) => {
      const newIds = data.map(r => r.tx_id).filter(id => !knownIdsRef.current.has(id));
      data.forEach(r => knownIdsRef.current.add(r.tx_id));
      if (newIds.length > 0) {
        setAnimatingIds(prev => new Set([...prev, ...newIds]));
        setTimeout(() => {
          setAnimatingIds(prev => {
            const next = new Set(prev);
            newIds.forEach(id => next.delete(id));
            return next;
          });
        }, 900);
      }

      setQueue(data);
      setSelected((prev) => {
        if (!prev) return prev;
        const updated = data.find((r) => r.tx_id === prev.tx_id);
        const nowDone = updated && ['awaiting_approval', 'resolved', 'rejected'].includes(updated.status);
        const wasDone = ['awaiting_approval', 'resolved', 'rejected'].includes(prev.status);
        if (nowDone && !wasDone && !runningRef.current) {
          loadStoredReport(updated);
        }
        return updated ?? prev;
      });
    });
  }

  useEffect(() => {
    fetchQueue();
    const id = setInterval(fetchQueue, 5000);
    return () => { clearInterval(id); cancelRef.current?.(); };
  }, []);

  useEffect(() => {
    function handleKey(e) {
      if (e.key === 'Escape' && modalOpenRef.current) closeModal();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, []);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' });
  }, [lines]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat]);

  async function decide(kind) {
    if (!report) return;
    setDecision(kind);
    await submitDecision(report.report_id, kind);
    fetchQueue();
    loadArchive();
    setTimeout(() => setModalOpen(false), 1400);
  }

  async function ask(text) {
    const message = (text ?? chatInput).trim();
    if (!message || chatBusy || !report) return;
    setChatInput('');
    setChat((c) => [...c, { role: 'user', text: message }]);
    setChatBusy(true);
    const res = await sendChat(report.report_id, selected.tx_id, message);
    setChat((c) => [...c, { role: 'bot', text: res.answer, tool: res.tool }]);
    setChatBusy(false);
  }

  async function loadArchive() {
    const { data } = await getExceptions('resolved,rejected');
    setArchive(data);
    setShowArchive(true);
  }

  const filteredQueue = queue
    .filter(row => {
      if (typeFilter !== 'all' && row.type_key !== typeFilter) return false;
      if (!searchText) return true;
      const q = searchText.toLowerCase();
      return (
        row.tx_id?.toLowerCase().includes(q) ||
        row.type?.toLowerCase().includes(q) ||
        row.sender?.toLowerCase().includes(q) ||
        row.receiver?.toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      if (sortBy === 'amount-desc') return parseFloat(b.amount || 0) - parseFloat(a.amount || 0);
      if (sortBy === 'amount-asc')  return parseFloat(a.amount || 0) - parseFloat(b.amount || 0);
      if (sortBy === 'priority') {
        const ord = { awaiting_approval: 0, pending: 1, evaluating: 2, investigating: 3, resolved: 4, rejected: 5 };
        return (ord[a.status] ?? 9) - (ord[b.status] ?? 9);
      }
      if (sortBy === 'sla') {
        const da = a.settlement_date ? new Date(a.settlement_date) : new Date('9999-12-31');
        const db = b.settlement_date ? new Date(b.settlement_date) : new Date('9999-12-31');
        return da - db;
      }
      return 0;
    });

  const suggestions = selected ? (SUGGESTIONS[selected.type_key] ?? SUGGESTIONS.default) : [];
  const pendingCount = queue.filter((r) => r.status === 'pending' || r.status === 'awaiting_approval').length;

  return (
    <>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 12px' }}>
          <h2 style={{ fontSize: 15 }}>Exception Queue</h2>
          <span className="pill gray">{pendingCount} pending</span>
        </div>

        <div className="queue-toolbar">
          <div className="search-wrap">
            <span className="search-icon">🔍</span>
            <input
              className="search-input"
              placeholder="Search by TX ID, type, sender, receiver…"
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
            />
            {searchText && (
              <button className="search-clear" onClick={() => setSearchText('')}>✕</button>
            )}
          </div>
          <select
            className="sort-select"
            value={sortBy}
            onChange={e => setSortBy(e.target.value)}
          >
            {SORT_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        <div className="type-filters">
          {TYPE_FILTERS.map(t => (
            <button
              key={t}
              className={`type-filter-btn ${typeFilter === t ? 'active' : ''}`}
              onClick={() => setTypeFilter(t)}
            >
              {t === 'all' ? 'All types' : t.toUpperCase()}
            </button>
          ))}
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ minWidth: 960 }}>
            <thead>
              <tr>
                <th>TX ID</th>
                <th>Type</th>
                <th>Error Code</th>
                <th style={{ textAlign: 'right' }}>Amount</th>
                <th>Sender → Receiver</th>
                <th>Created</th>
                <th>Settlement</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredQueue.map((row) => {
                const pill = STATUS_PILL[row.status] ?? STATUS_PILL.pending;
                const isNew = animatingIds.has(row.tx_id);
                const sla = settlementCell(row.settlement_date);
                return (
                  <tr
                    key={row.tx_id}
                    className={`clickable${isNew ? ' row-new' : ''}`}
                    onClick={() => openModal(row)}
                  >
                    <td className="num">{row.tx_id}</td>
                    <td>
                      <span className={`pill ${TYPE_PILL[row.type_key] ?? 'gray'}`}>{row.type}</span>
                      {row.precheck_summary?.action_hint && (
                        <div style={{ fontSize: 11, color: '#8fa1c0', marginTop: 2 }}>
                          {row.precheck_summary.action_hint.slice(0, 60)}
                        </div>
                      )}
                    </td>
                    <td className="num" style={{ fontSize: 11.5, color: '#8fa1c0' }}
                        title={row.error_code ?? ''}>
                      {row.error_code ? row.error_code.replace(/_/g, '_​') : '—'}
                    </td>
                    <td className="num" style={{ textAlign: 'right' }}>{row.amount}</td>
                    <td style={{ color: '#8fa1c0', fontSize: 12.5 }}>
                      <div>{row.sender}</div>
                      <div style={{ fontSize: 11, marginTop: 1 }}>→ {row.receiver}</div>
                    </td>
                    <td style={{ color: '#8fa1c0', fontSize: 12, whiteSpace: 'nowrap' }}>
                      {timeAgo(row.created_at)}
                    </td>
                    <td style={{ whiteSpace: 'nowrap', fontSize: 12 }}>
                      {sla.cls ? (
                        <span className={`sla-date ${sla.cls}`}>
                          {sla.urgent && '⚠ '}{sla.label}
                        </span>
                      ) : (
                        <span style={{ color: '#8fa1c0' }}>{sla.label}</span>
                      )}
                    </td>
                    <td>
                      <span className={`pill ${pill.cls}`}>
                        {pill.spinner && <span className="spinner" style={{ marginRight: 4 }} />}
                        {pill.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {filteredQueue.length === 0 && queue.length > 0 && (
                <tr><td colSpan={8} style={{ textAlign: 'center', color: '#8fa1c0', padding: 20 }}>No results match current filter</td></tr>
              )}
              {queue.length === 0 && (
                <tr><td colSpan={8} style={{ textAlign: 'center', color: '#8fa1c0', padding: 20 }}>No active exceptions</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {modalOpen && selected && (
        <div className="modal-overlay" onClick={closeModal}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className={`pill ${TYPE_PILL[selected.type_key] ?? 'gray'}`} style={{ fontSize: 11 }}>
                  {selected.type}
                </span>
                <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)' }}>
                  {selected.tx_id}
                </span>
                {running && <span className="spinner" />}
              </div>
              <button className="modal-close" onClick={closeModal} title="Close (Esc)">✕</button>
            </div>
            <div className="modal-body">
              <div className="stream" ref={streamRef}>
                {lines.map((l, i) => (
                  <div className="stream-line" key={i}>
                    <span className={`agent ${l.cls}`}>{l.agent === 'tool' ? '' : `${l.agent}:`}</span>
                    {l.cls === 'tool'
                      ? <span className="txt">{l.text}</span>
                      : <div className="txt md"><Md>{l.text}</Md></div>}
                  </div>
                ))}
                {running && <span className="cursor" />}
              </div>

              {report?.report_id && (
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <a
                    href={`/api/reports/${report.report_id}/pdf`}
                    download={`${report.report_id}.pdf`}
                    className="btn"
                    style={{ fontSize: 12, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 6 }}
                  >
                    📄 Download PDF Report
                  </a>
                </div>
              )}

              {report?.recommendation && (
                <div className={`hitl ${decision === 'approve' ? 'approved' : decision === 'reject' ? 'rejected' : ''}`}>
                  <div className="msg">
                    {decision === null && <>
                      <strong>⏳ Awaiting human approval</strong>
                      <div style={{ marginTop: 6 }}>{report.recommendation.action}</div>
                      <div className="footnote">{report.recommendation.rationale}</div>
                    </>}
                    {decision === 'approve' && <>
                      <strong style={{ color: '#34d399' }}>✓ Approved &amp; executed</strong>
                      <div className="footnote">execute_resolution() called with approval token · full trail written to audit log</div>
                    </>}
                    {decision === 'reject' && <>
                      <strong style={{ color: '#f87171' }}>✖ Rejected</strong>
                      <div className="footnote">Recommendation rejected — case returned to manual queue · decision logged to audit trail</div>
                    </>}
                  </div>
                  {decision === null && (
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn approve" onClick={() => decide('approve')}>Approve</button>
                      <button className="btn reject" onClick={() => decide('reject')}>Reject</button>
                    </div>
                  )}
                </div>
              )}

              {report && (
                <div>
                  <h3 style={{ margin: '4px 0 10px', fontSize: 13, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--muted)' }}>
                    Ask a question about this investigation
                  </h3>
                  <div className="chat">
                    {chat.length > 0 && (
                      <div className="chat-history">
                        {chat.map((m, i) => (
                          <div className={`msg-row ${m.role}`} key={i}>
                            <div className="msg-bubble">
                              {m.tool && <span className="tool-note">🔧 [calls {m.tool}]</span>}
                              {m.role === 'bot' ? <Md>{'🤖 ' + m.text}</Md> : m.text}
                            </div>
                          </div>
                        ))}
                        {chatBusy && (
                          <div className="msg-row bot">
                            <div className="msg-bubble"><span className="spinner" /> thinking…</div>
                          </div>
                        )}
                        <div ref={chatEndRef} />
                      </div>
                    )}
                    <div className="suggestions">
                      {suggestions.map((s) => (
                        <button key={s} className="suggestion" onClick={() => ask(s)}>{s}</button>
                      ))}
                    </div>
                    <div className="chat-input">
                      <input
                        value={chatInput}
                        onChange={(e) => setChatInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && ask()}
                        placeholder="e.g. Why did you flag this IBAN specifically?"
                      />
                      <button className="btn primary" onClick={() => ask()} disabled={chatBusy}>Send</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title" style={{ margin: '0 0 8px' }}>
          <h2 style={{ fontSize: 15 }}>Resolved Cases</h2>
          <button
            className="btn"
            style={{ fontSize: 12 }}
            onClick={showArchive ? () => setShowArchive(false) : loadArchive}
          >
            {showArchive ? 'Hide archive' : `Show resolved (${archive.length || '…'})`}
          </button>
        </div>
        <div className={`archive-collapse ${showArchive ? 'open' : ''}`}>
          <table>
            <thead>
              <tr>
                <th>TX ID</th><th>Type</th><th>Amount</th>
                <th>Decision</th><th>Agent Recommendation</th><th>Resolved At</th>
              </tr>
            </thead>
            <tbody>
              {archive.map((row) => (
                <tr key={row.tx_id}>
                  <td className="num">{row.tx_id}</td>
                  <td><span className={`pill ${TYPE_PILL[row.type_key] ?? 'gray'}`}>{row.type}</span></td>
                  <td className="num">{row.amount}</td>
                  <td>
                    <span className={`pill ${row.status === 'resolved' ? 'green' : 'gray'}`}>
                      {row.status === 'resolved' ? 'Approved' : 'Rejected'}
                    </span>
                  </td>
                  <td style={{ color: '#8fa1c0', fontSize: 12 }}>
                    {row.recommendation_action ? row.recommendation_action.slice(0, 80) : '—'}
                  </td>
                  <td style={{ color: '#8fa1c0', fontSize: 12 }}>
                    {row.resolved_at ? new Date(row.resolved_at).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
              {archive.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: '#8fa1c0', padding: 16 }}>No resolved cases yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
