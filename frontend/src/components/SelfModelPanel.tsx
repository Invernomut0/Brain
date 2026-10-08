import { useBrain } from '../store'

const List = ({ items }: { items: string[] }) => (items?.length ? <ul>{items.map((i, k) => <li key={k}>{i}</li>)}</ul> : <p>—</p>)

export function SelfModelPanel() {
  const sm = useBrain((s) => s.selfmodel)
  if (!sm) return <section className="panel" style={{ flex: 1 }}><h3>Self-model</h3><div className="empty">In attesa…</div></section>
  return (
    <section className="panel" style={{ flex: 1 }}>
      <h3>Self-model <span>rev {sm.revision}</span></h3>
      <div className="scroll sm">
        <h4>Identità</h4><p>{sm.identity}</p>
        <h4>Scopo</h4><p>{sm.purpose}</p>
        <h4>Capacità</h4><List items={sm.capabilities} />
        <h4>Limiti</h4><List items={sm.limitations} />
        <h4>Lorenzo</h4><p>{sm.about_user}</p>
        <h4>Mondo</h4><p>{sm.about_world}</p>
        <h4>Domande aperte</h4><List items={sm.open_questions} />
        <h4>Ipotesi</h4><List items={sm.hypotheses} />
      </div>
    </section>
  )
}
