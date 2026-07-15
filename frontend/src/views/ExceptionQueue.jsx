import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getExceptions, getInvestigationReport, streamInvestigation, submitDecision, sendChat } from '../api/client.js';

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

function slaWarning(settlementDate) {
  if (!settlementDate) return false;
  const hoursUntil = (new Date(settlementDate) - Date.now()) / 3_600_000;
  return hoursUntil >= 0 && hoursUntil <= 24;
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
  const cancelRef = useRef(null);
  const streamRef = useRef(null);
  const chatEndRef = useRef(null);
  const seenTxIdsRef = useRef(new Set());
  const runningRef = useRef(false);

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

    if (row.status === 'investigating') {
      runningRef.current = true;
      setRunning(true);
      setLines([{ agent: 'System', cls: 'intake', text: 'Investigation running in background — waiting for results…' }]);
      // Poll until complete; the 5s queue poll will update `queue`, which triggers
      // a re-render; if the user clicks again the done branch above will fire.
      return;
    }

    // pending / evaluating — backend will pick it up automatically
    setLines([{ agent: 'System', cls: 'intake', text: 'Queued for investigation — results will appear here when ready.' }]);
    runningRef.current = false;
    setRunning(false);
  }

  function fetchQueue() {
    getExceptions('active').then(({ data }) => {
      setQueue(data);
      // If a selected exception just finished, load its report automatically
      setSelected((prev) => {
        if (!prev) return prev;
        const updated = data.find((r) => r.tx_id === prev.tx_id);
        if (updated && ['awaiting_approval', 'resolved', 'rejected'].includes(updated.status)
            && !['awaiting_approval', 'resolved', 'rejected'].includes(prev.status)) {
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

  const suggestions = selected ? (SUGGESTIONS[selected.type_key] ?? SUGGESTIONS.default) : [];
  const pendingCount = queue.filter((r) => r.status === 'pending' || r.status === 'awaiting_approval').length;

  return (
    <>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 12px' }}>
          <h2 style={{ fontSize: 15 }}>Exception Queue</h2>
          <span className="pill gray">{pendingCount} pending</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>TX ID</th><th>Type</th><th>Amount</th>
              <th>Sender → Receiver</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((row) => {
              const pill = STATUS_PILL[row.status] ?? STATUS_PILL.pending;
              return (
                <tr
                  key={row.tx_id}
                  className={`clickable ${selected?.tx_id === row.tx_id ? 'selected' : ''}`}
                  onClick={() => investigate(row)}
                >
                  <td className="num">
                    {row.tx_id}
                    {slaWarning(row.settlement_date) && (
                      <span className="pill orange" style={{ marginLeft: 6, fontSize: 10 }}>⚠ SLA</span>
                    )}
                  </td>
                  <td>
                    <span className={`pill ${TYPE_PILL[row.type_key] ?? 'gray'}`}>{row.type}</span>
                    {row.precheck_summary?.action_hint && (
                      <div style={{ fontSize: 11, color: '#8fa1c0', marginTop: 2 }}>
                        {row.precheck_summary.action_hint.slice(0, 80)}
                      </div>
                    )}
                  </td>
                  <td className="num">{row.amount}</td>
                  <td style={{ color: '#8fa1c0' }}>{row.sender} → {row.receiver}</td>
                  <td>
                    <span className={`pill ${pill.cls}`}>
                      {pill.spinner && <span className="spinner" style={{ marginRight: 4 }} />}
                      {pill.label}
                    </span>
                  </td>
                </tr>
              );
            })}
            {queue.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: 'center', color: '#8fa1c0', padding: 20 }}>No active exceptions</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>
            Agent Investigation — {selected.tx_id}
            {running && <span className="spinner" style={{ marginLeft: 8 }} />}
          </h3>
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
            <div style={{ marginTop: 18 }}>
              <h3>Ask a question about this investigation</h3>
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
        {showArchive && (
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
        )}
      </div>
    </>
  );
}
