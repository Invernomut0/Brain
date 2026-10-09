import { useBrain } from '../store'

const List = ({ items }: { items: string[] }) => (items?.length ? <ul>{items.map((i, k) => <li key={k}>{i}</li>)}</ul> : <p>—</p>)

export function SelfModelPanel() {
  const sm = useBrain((s) => s.selfmodel)
  const owner = useBrain((s) => s.owner)
  if (!sm) return <section className="panel" style={{ flex: 1 }}><h3>Self-model</h3><div className="empty">Waiting…</div></section>
  return (
    <section className="panel" style={{ flex: 1 }}>
      <h3>Self-model <span>rev {sm.revision}</span></h3>
      <div className="scroll sm">
        <h4>Identity</h4><p>{sm.identity}</p>
        <h4>Purpose</h4><p>{sm.purpose}</p>
        <h4>Capabilities</h4><List items={sm.capabilities} />
        <h4>Limitations</h4><List items={sm.limitations} />
        <h4>{owner || 'Owner'}</h4><p>{sm.about_user}</p>
        <h4>World</h4><p>{sm.about_world}</p>
        <h4>Open questions</h4><List items={sm.open_questions} />
        <h4>Hypotheses</h4><List items={sm.hypotheses} />
      </div>
    </section>
  )
}
