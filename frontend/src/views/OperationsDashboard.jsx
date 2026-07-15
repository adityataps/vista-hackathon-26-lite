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
              <Bar isAnimationActive={false} dataKey="sepa_sct" name="SEPA SCT (mock)" stackId="a" fill="#3d5578" />
              <Bar isAnimationActive={false} dataKey="fedwire" name="Fedwire (mock)" stackId="a" fill="#3f6e6a" />
              <Bar isAnimationActive={false} dataKey="swift_cbpr" name="SWIFT CBPR+" stackId="a" fill="#4f8ef7" radius={[3, 3, 0, 0]} />
              <Bar isAnimationActive={false} dataKey="exceptions" name="Exceptions" fill="#f87171" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>Cost Savings vs Manual Baseline (USD · today)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={savings}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis dataKey="hour" stroke="#8fa1c0" fontSize={11} />
              <YAxis
                stroke="#8fa1c0"
                fontSize={11}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v, name) => {
                  if (name === 'Resolved cases') return [v, name];
                  return [`$${Number(v).toFixed(2)}`, name];
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {/* saving_per_case is always non-zero — shows the per-case saving regardless of volume */}
              <Bar dataKey="saving_per_case" name="Saving per case" fill="#4f8ef7" radius={[3, 3, 0, 0]} />
              <Bar dataKey="total_saving"    name="Total hourly saving" fill="#34d399" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="footnote">
            Saving per case = $27.50 manual baseline − actual avg LLM token cost (claude-sonnet-4-6 at
            $0.003/1k input · $0.015/1k output). Total hourly saving = saving per case × resolved count.
            Per-case saving shown for all hours as the potential saving; total saving only accrues when
            investigations complete.
          </div>
        </div>

        <div className="card">
          <h3>Exception Breakdown (all-time)</h3>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={breakdown} layout="vertical" margin={{ left: 30 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#22304d" />
              <XAxis type="number" stroke="#8fa1c0" fontSize={11} allowDecimals={false} />
              <YAxis type="category" dataKey="type" stroke="#8fa1c0" fontSize={11.5} width={150} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(79,142,247,0.06)' }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {/* green = agent fix approved by operator; orange = operator rejected (HITL) */}
              <Bar isAnimationActive={false} dataKey="approved" name="Approved (human)" stackId="x" fill="#34d399" />
              <Bar isAnimationActive={false} dataKey="rejected" name="Rejected (human)" stackId="x" fill="#fbbf24" radius={[0, 3, 3, 0]} />
            </BarChart>
          </ResponsiveContainer>
          {ai && (
            <div className="footnote">
              AI performance: {ai.total_investigations} investigations · {Math.round(ai.recommendation_acceptance_rate * 100)}% recommendation acceptance · avg {ai.avg_investigation_seconds}s per investigation
            </div>
          )}
        </div>

        <div className="card">
          <h3>
            AI Cost per Exception Type (USD)
            {tokenCosts.length > 0 && (
              <span style={{ fontSize: 11, fontWeight: 400, marginLeft: 8, color: tokenCosts[0].is_live ? '#34d399' : '#8fa1c0' }}>
                {tokenCosts[0].is_live ? '● live' : '○ estimated'}
              </span>
            )}
          </h3>
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
              <Bar isAnimationActive={false} dataKey="precheck_avg_usd"      name="Pre-check"         fill="#3d5578" radius={[0,3,3,0]} />
              <Bar isAnimationActive={false} dataKey="investigation_avg_usd" name="Full investigation" fill="#4f8ef7" radius={[0,3,3,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </>
  );
}
