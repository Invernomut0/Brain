import { useBrain } from '../store'
import { Gauge, Sparkline } from './Charts'

const pct = (v: number | null) => (v == null ? '—' : (v * 100).toFixed(0) + '%')

function Bar({ label, v }: { label: string; v: number | null }) {
  return (
    <>
      <div className="row"><span>{label}</span><b>{pct(v)}</b></div>
      <div className="bar"><i style={{ width: `${(v ?? 0) * 100}%` }} /></div>
    </>
  )
}

export function MetricsPanel() {
  const m = useBrain((s) => s.metrics)
  const sys = useBrain((s) => s.sys)
  const hist = useBrain((s) => s.history)
  return (
    <section className="panel" style={{ flex: 1 }}>
      <h3>Awareness (measurable proxies)</h3>
      <div className="scroll">
        <Gauge value={m.awareness_index} label="AWARENESS INDEX" />
        <Sparkline data={hist.map((h) => h.awareness)} color="#8b7bff" domain={[0, 1]} />
        <div style={{ height: 8 }} />
        <Bar label="Calibration (Brier)" v={m.calibration} />
        <Bar label="Introspection (probe)" v={m.introspection} />
        <Bar label="Success rate" v={m.success_rate} />
        <div className="kpis">
          <div className="kpi"><div className="v">{m.goals_done}<span style={{ color: '#ff4d6d', fontSize: 13 }}> /{m.goals_failed}</span></div><div className="l">goals ok / ko</div></div>
          <div className="kpi"><div className="v">{m.tools}</div><div className="l">tools created</div></div>
          <div className="kpi"><div className="v">{m.selfmodel_revision}</div><div className="l">self-model revisions</div></div>
          <div className="kpi"><div className="v">{m.evolutions_applied}<span style={{ color: '#ffb020', fontSize: 13 }}> ↺{m.evolutions_rolled_back}</span></div><div className="l">evolutions / rollbacks</div></div>
          <div className="kpi"><div className="v">{m.memories}</div><div className="l">memories</div></div>
          <div className="kpi"><div className="v">{m.agents_spawned}</div><div className="l">agents spawned</div></div>
          <div className="kpi"><div className="v">{m.lessons}</div><div className="l">lessons learned</div></div>
        </div>
        <div className="row"><span>LLM speed</span><b>{sys.tps.toFixed(1)} tok/s</b></div>
        <Sparkline data={hist.map((h) => h.tps)} color="#22d3ee" />
        <div className="row"><span>CPU</span><b>{sys.cpu.toFixed(0)}%</b></div>
        <Sparkline data={hist.map((h) => h.cpu)} color="#34f5a0" domain={[0, 100]} height={32} />
        <div className="row"><span>RAM</span><b>{sys.mem.toFixed(0)}%</b></div>
        <Sparkline data={hist.map((h) => h.mem)} color="#ffb020" domain={[0, 100]} height={32} />
        <div className="row" style={{ marginTop: 6 }}><span>LLM calls</span><b>{sys.calls}</b></div>
        <div className="row"><span>Uptime</span><b>{Math.floor(sys.uptime / 60)}m {sys.uptime % 60}s</b></div>
      </div>
    </section>
  )
}
