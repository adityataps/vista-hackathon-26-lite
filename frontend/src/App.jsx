import { useEffect, useState } from 'react';
import OperationsDashboard from './views/OperationsDashboard.jsx';
import ExceptionQueue from './views/ExceptionQueue.jsx';
import { probeBackend, getKpis, generateDemoPayments } from './api/client.js';

const TABS = [
  { id: 'dashboard', label: 'Operations Dashboard' },
  { id: 'exceptions', label: 'Exception Queue' },
];

export default function App() {
  const [tab, setTab] = useState('dashboard');
  const [backendLive, setBackendLive] = useState(null);
  const [openExceptions, setOpenExceptions] = useState(0);
  const [genState, setGenState] = useState('idle'); // idle | running | done

  function refreshBadge() {
    getKpis().then(({ data }) => setOpenExceptions(data.exceptions_open ?? 0));
  }

  useEffect(() => {
    probeBackend().then(setBackendLive);
    refreshBadge();
  }, []);

  async function generate() {
    if (genState === 'running') return;
    setGenState('running');
    await generateDemoPayments();
    refreshBadge();
    setGenState('done');
    setTimeout(() => setGenState('idle'), 3000);
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <div className="brand-logo">⚡</div>
          <div>
            PayInvestigator
            <small>AI Payment Exception Investigation · Global PAYplus layer</small>
          </div>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.id === 'exceptions' && openExceptions > 0 && (
                <span className="badge">{openExceptions}</span>
              )}
            </button>
          ))}
        </nav>
        <button
          className="btn primary"
          style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}
          onClick={generate}
          disabled={genState === 'running'}
        >
          {genState === 'idle' && '⚡ Generate Payments'}
          {genState === 'running' && <><span className="spinner" style={{ marginRight: 8 }} />Generating…</>}
          {genState === 'done' && '✓ Payments generated'}
        </button>
        <div className="conn" title="Backend connectivity">
          <span className={`dot ${backendLive ? 'live' : 'mock'}`} />
          {backendLive === null ? 'Connecting…' : backendLive ? 'Backend live' : 'Demo mode (mock data)'}
        </div>
      </header>

      <main className="main">
        {tab === 'dashboard' && <OperationsDashboard />}
        {tab === 'exceptions' && <ExceptionQueue />}
      </main>
    </div>
  );
}
