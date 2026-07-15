import { useEffect, useRef, useState } from 'react';
import { getExceptions, streamInvestigation, submitDecision, sendChat } from '../api/client.js';

const TYPE_PILL = {
  iban: 'blue', sanctions: 'red', iso: 'blue', fx: 'yellow', duplicate: 'gray',
};

const SUGGESTIONS = {
  'TX-00142': ['Why did you flag this IBAN specifically?', 'Are there other payments to this receiver this week?', 'If I approve this, what happens to the IBAN correction?'],
  'TX-00138': ['Why did you recommend holding this payment?', 'Show me the full SDN entry for the match', 'Does the sender have prior compliance flags?'],
  default: ['Why this recommendation?', 'Show related payments from the same sender'],
};

export default function ExceptionQueue() {
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(null);
  const [lines, setLines] = useState([]);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);   // {report_id, recommendation}
  const [decision, setDecision] = useState(null); // 'approve' | 'reject'
  const [chat, setChat] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const cancelRef = useRef(null);
  const streamRef = useRef(null);
  const chatEndRef = useRef(null);

  useEffect(() => {
    getExceptions().then(({ data }) => setQueue(data));
    return () => cancelRef.current?.();
  }, []);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' });
  }, [lines]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat]);

  function investigate(row) {
    cancelRef.current?.();
    setSelected(row);
    setLines([]);
    setReport(null);
    setDecision(null);
    setChat([]);
    setRunning(true);
    cancelRef.current = streamInvestigation(
      row.tx_id,
      (evt) => setLines((prev) => [...prev, evt]),
      (final) => {
        setRunning(false);
        setReport(final);
      }
    );
  }

  async function decide(kind) {
    if (!report) return;
    setDecision(kind);
    await submitDecision(report.report_id, kind);
    // Re-fetch the queue from the API so status reflects the DB, not an optimistic guess
    getExceptions().then(({ data }) => setQueue(data));
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

  const suggestions = selected ? (SUGGESTIONS[selected.tx_id] ?? SUGGESTIONS.default) : [];

  return (
    <>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 12px' }}>
          <h2 style={{ fontSize: 15 }}>Exception Queue</h2>
          <span className="pill gray">{queue.filter((r) => r.status === 'pending').length} pending</span>
        </div>
        <table>
          <thead>
            <tr><th>TX ID</th><th>Type</th><th>Amount</th><th>Sender → Receiver</th><th>Status</th></tr>
          </thead>
          <tbody>
            {queue.map((row) => (
              <tr
                key={row.tx_id}
                className={`clickable ${selected?.tx_id === row.tx_id ? 'selected' : ''}`}
                onClick={() => investigate(row)}
              >
                <td className="num">{row.tx_id}</td>
                <td><span className={`pill ${TYPE_PILL[row.type_key] ?? 'gray'}`}>{row.type}</span></td>
                <td className="num">{row.amount}</td>
                <td style={{ color: '#8fa1c0' }}>{row.sender} → {row.receiver}</td>
                <td>
                  <span className={`pill ${row.status === 'resolved' ? 'green' : 'yellow'}`}>
                    {row.status === 'resolved' ? 'Resolved' : 'Pending'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>
            Agent Investigation — {selected.tx_id} {running && <span className="spinner" style={{ marginLeft: 8 }} />}
          </h3>
          <div className="stream" ref={streamRef}>
            {lines.map((l, i) => (
              <div className="stream-line" key={i}>
                <span className={`agent ${l.cls}`}>{l.agent === 'tool' ? '' : `${l.agent}:`}</span>
                <span className="txt">{l.text}</span>
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
                          {m.role === 'bot' ? '🤖 ' : ''}{m.text}
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
    </>
  );
}
