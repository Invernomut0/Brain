import { roleColor, useBrain } from '../store'
import { useNow } from '../hooks/useNow'

export function AgentList() {
  const agents = useBrain((s) => s.agents)
  const now = useNow(1000)
  const list = Object.values(agents).filter((a) => !a.endedAt || now - a.endedAt < 30000).sort((a, b) => b.bornAt - a.bornAt)
  return (
    <section className="panel" style={{ flex: 1 }}>
      <h3>Agenti <span>{list.filter((a) => !a.endedAt).length} attivi</span></h3>
      <div className="scroll">
        {!list.length && <div className="empty">Nessun agente attivo.<br />Premi “Avvia” per far partire il sistema.</div>}
        {list.map((a) => (
          <div className="agent" key={a.id} style={{ borderLeft: `3px solid ${roleColor(a.role)}`, opacity: a.endedAt ? 0.6 : 1 }}>
            <div className="top">
              <span className="role" style={{ color: roleColor(a.role) }}>{a.role}</span>
              <small style={{ color: '#7f89b8' }}>{a.id}</small>
              <span className={`badge ${a.state}`}>{a.state === 'acting' ? a.detail : a.state}</span>
            </div>
            <div className="sub">{a.summary || a.thought || a.task}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
