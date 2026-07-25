import { useEffect, useState } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import OperationsDashboard from './views/OperationsDashboard.jsx';
import ExceptionQueue from './views/ExceptionQueue.jsx';
import { probeBackend, getKpis, generateDemoPayments } from './api/client.js';

const LITE_BANNER_STORAGE_KEY = 'payinvestigator-lite-banner-dismissed';
const LITE_MODAL_STORAGE_KEY = 'payinvestigator-lite-modal-seen';
const LITE_BANNER_TEXT = 'Lite deployment: first requests can be slower while the serverless DB resumes and Lambda warms up.';
const LITE_MODAL_TITLE = 'Welcome to PayInvestigator Lite';
const LITE_MODAL_BODY = 'This rebuild keeps the demo online on a near-$0 idle-cost footprint after the original hackathon environment was retired.';
const LITE_MODAL_POINTS = [
  'Cheaper Claude Haiku model instead of the original higher-cost setup.',
  'Aurora Serverless v2 can cold-resume after idle, so the first backend call may take a few extra seconds.',
  'Frontend is served directly from S3 website hosting, without a CDN layer.',
];

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
  const [showLiteBanner, setShowLiteBanner] = useState(() => localStorage.getItem(LITE_BANNER_STORAGE_KEY) !== 'true');
  const [showLiteModal, setShowLiteModal] = useState(() => localStorage.getItem(LITE_MODAL_STORAGE_KEY) !== 'true');

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

  function dismissLiteBanner() {
    localStorage.setItem(LITE_BANNER_STORAGE_KEY, 'true');
    setShowLiteBanner(false);
  }

  function closeLiteModal() {
    localStorage.setItem(LITE_MODAL_STORAGE_KEY, 'true');
    setShowLiteModal(false);
  }

  const activeTab = TABS.find((t) => location.pathname.startsWith(t.path))?.id ?? 'dashboard';

  return (
    <div className="app">
      {showLiteBanner && (
        <div className="lite-banner" role="status" aria-live="polite">
          <span className="lite-banner__badge">Lite deployment</span>
          <span className="lite-banner__text">{LITE_BANNER_TEXT}</span>
          <button className="lite-banner__close" onClick={dismissLiteBanner} aria-label="Dismiss lite deployment notice">✕</button>
        </div>
      )}

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
        <div className="conn" title="Backend connectivity">
          <span className={`dot ${backendLive ? 'live' : 'mock'}`} />
          {backendLive === null ? 'Connecting…' : backendLive ? 'Backend live' : 'Demo mode (mock data)'}
        </div>
        <button
          className="btn primary"
          style={{ whiteSpace: 'nowrap' }}
          onClick={generate}
          disabled={genState === 'running'}
        >
          {genState === 'idle' && '(Demo) ⚡ Generate Payments'}
          {genState === 'running' && <><span className="spinner" style={{ marginRight: 8 }} />Generating…</>}
          {genState === 'done' && '✓ Payments generated'}
        </button>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate replace to="/dashboard" />} />
          <Route path="/dashboard" element={<OperationsDashboard />} />
          <Route path="/exceptions" element={<ExceptionQueue />} />
        </Routes>
      </main>

      {showLiteModal && (
        <div className="modal-overlay" onClick={closeLiteModal}>
          <div className="modal lite-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="lite-modal-title">
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className="pill blue">Lite</span>
                <span id="lite-modal-title" style={{ fontWeight: 600, fontSize: 15 }}>{LITE_MODAL_TITLE}</span>
              </div>
              <button className="modal-close" onClick={closeLiteModal} aria-label="Close lite deployment notice">✕</button>
            </div>
            <div className="modal-body">
              <p className="lite-modal__body">{LITE_MODAL_BODY}</p>
              <ul className="lite-modal__list">
                {LITE_MODAL_POINTS.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
              <div className="lite-modal__actions">
                <button className="btn primary" onClick={closeLiteModal}>Got it</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
