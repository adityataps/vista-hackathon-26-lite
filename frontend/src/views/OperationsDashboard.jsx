import { useEffect, useState } from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import {
  getKpis, getVolume, getSavings, getExceptionBreakdown,
  getTokenCosts, getThroughput, getAiStats,
} from '../api/client.js';

const tooltipStyle = {
  backgroundColor: '#0d1526',
  border: '1px solid #22304d',
  borderRadius: 8,
  color: '#e5ecf8',
  fontSize: 12,
};

export default function OperationsDashboard() {
  const [kpis, setKpis] = useState(null);
  const [volume, setVolume] = useState([]);
  const [savings, setSavings] = useState([]);
  const [breakdown, setBreakdown] = useState([]);
  const [tokenCosts, setTokenCosts] = useState([]);
  const [throughput, setThroughput] = useState([]);
  const [ai, setAi] = useState(null);

  useEffect(() => {
    getKpis().then(({ data }) => setKpis(data));
    getVolume().then(({ data }) => setVolume(data));
    getSavings().then(({ data }) => setSavings(data));
    getExceptionBreakdown().then(({ data }) => setBreakdown(data));
    getTokenCosts().then(({ data }) => setTokenCosts(data));
    getThroughput().then(({ data }) => setThroughput(data));
    getAiStats().then(({ data }) => setAi(data));
  }, []);

  if (!kpis) return <div className="card">Loading…</div>;

  return (
    <>
      <div className="kpi-row">
        <div className="kpi">
          <div className="label">In-Flight Payments</div>
          <div className="value">{kpis.in_flight}</div>
          <div className="sub">total payments — all rails incl. CBPR+</div>
        </div>
        <div className="kpi">
          <div className="label">Open Exceptions</div>
          <div className="value">{kpis.exceptions_open}</div>
          <div className="sub">awaiting approval or reject</div>
        </div>
        <div className="kpi">
          <div className="label">Settlement at Risk</div>
          <div className="value" style={{ color: '#f87171' }}>{kpis.settlement_risk}</div>
          <div className="sub">open exceptions vs. interbank settlement date</div>
        </div>
        <div className="kpi">
          <div className="label">Mean Time to Resolution</div>
          <div className="value">
            {kpis.mttr_before}<span className="arrow">→</span>{kpis.mttr_now}
          </div>
          <div className="sub">manual vs. AI investigation</div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Transaction Volume (hourly, by rail)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={volume}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis dataKey="hour" stroke="#8fa1c0" fontSize={11} />
              <YAxis stroke="#8fa1c0" fontSize={11} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(79,142,247,0.06)' }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {/* sepa_sct + fedwire = static mock rails (constant); swift_cbpr = dynamic from message generator */}
              <Bar dataKey="sepa_sct" name="SEPA SCT (mock)" stackId="a" fill="#3d5578" />
              <Bar dataKey="fedwire" name="Fedwire (mock)" stackId="a" fill="#3f6e6a" />
              <Bar dataKey="swift_cbpr" name="SWIFT CBPR+" stackId="a" fill="#4f8ef7" radius={[3, 3, 0, 0]} />
              <Bar dataKey="exceptions" name="Exceptions" fill="#f87171" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>Cost Savings per Pre-Check Case (USD · hourly, by rail)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={savings}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis dataKey="hour" stroke="#8fa1c0" fontSize={11} />
              <YAxis
                stroke="#8fa1c0"
                fontSize={11}
                domain={[24, 29]}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => `$${Number(v).toFixed(2)}`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {/* baseline = constant manual cost; swift_cbpr = dynamic (baseline − token cost); others mocked */}
              <Line type="monotone" dataKey="baseline" name="Manual investigation baseline" stroke="#8fa1c0" strokeDasharray="6 4" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="swift_cbpr" name="SWIFT CBPR+" stroke="#4f8ef7" strokeWidth={2.5} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="sepa_sct" name="SEPA SCT (mock)" stroke="#34d399" strokeWidth={1.5} strokeDasharray="2 3" dot={false} />
              <Line type="monotone" dataKey="fedwire" name="Fedwire (mock)" stroke="#2dd4bf" strokeWidth={1.5} strokeDasharray="2 3" dot={false} />
            </LineChart>
          </ResponsiveContainer>
          <div className="footnote">
            Baseline: manual investigation costs $15–$40 per case (industry average) — midpoint $27.50 used.
            Savings per case = baseline − LLM token cost of the AI investigation. SWIFT CBPR+ computed per
            resolved case; SEPA SCT &amp; Fedwire are mock datasets.
          </div>
        </div>

        <div className="card">
          <h3>Exception Breakdown (today)</h3>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={breakdown} layout="vertical" margin={{ left: 30 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis type="number" stroke="#8fa1c0" fontSize={11} allowDecimals={false} />
              <YAxis type="category" dataKey="type" stroke="#8fa1c0" fontSize={11.5} width={150} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(79,142,247,0.06)' }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {/* green = agent fix approved by operator; orange = operator rejected (HITL) */}
              <Bar dataKey="approved" name="Approved (human)" stackId="x" fill="#34d399" />
              <Bar dataKey="rejected" name="Rejected (human)" stackId="x" fill="#fbbf24" radius={[0, 3, 3, 0]} />
            </BarChart>
          </ResponsiveContainer>
          {ai && (
            <div className="footnote">
              AI performance: {ai.total_investigations} investigations · {Math.round(ai.recommendation_acceptance_rate * 100)}% recommendation acceptance · avg {ai.avg_investigation_seconds}s per investigation
            </div>
          )}
        </div>

        <div className="card">
          <h3>AI Cost per Exception Type (USD)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={tokenCosts} layout="vertical" margin={{ left: 160 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis type="number" stroke="#8fa1c0" fontSize={11} tickFormatter={(v) => `$${v.toFixed(3)}`} />
              <YAxis type="category" dataKey="type" stroke="#8fa1c0" fontSize={11} width={160} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name) => [`$${value.toFixed(4)}`, name]}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="precheck_avg_usd"      name="Pre-check"         fill="#3d5578" radius={[0,3,3,0]} />
              <Bar dataKey="investigation_avg_usd" name="Full investigation" fill="#4f8ef7" radius={[0,3,3,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </>
  );
}
