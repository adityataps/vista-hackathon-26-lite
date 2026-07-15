import { useEffect, useState } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import OperationsDashboard from './views/OperationsDashboard.jsx';
import ExceptionQueue from './views/ExceptionQueue.jsx';
import { probeBackend, getKpis, generateDemoPayments } from './api/client.js';

const TABS = [
  { id: 'dashboard', label: 'Operations Dashboard', path: '/dashboard' },
  { id: 'exceptions', label: 'Exception Queue',     path: '/exceptions' },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [backendLive, setBackendLive] = useState(null);
  const [openExceptions, setOpenExceptions] = useState(0);
  const [genState, setGenState] = useState('idle');

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

  const activeTab = TABS.find((t) => location.pathname.startsWith(t.path))?.id ?? 'dashboard';

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
              className={`tab ${activeTab === t.id ? 'active' : ''}`}
              onClick={() => navigate(t.path)}
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
        <Routes>
          <Route path="/" element={<Navigate replace to="/dashboard" />} />
          <Route path="/dashboard" element={<OperationsDashboard />} />
          <Route path="/exceptions" element={<ExceptionQueue />} />
        </Routes>
      </main>
    </div>
  );
}
