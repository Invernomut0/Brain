import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useBrain } from '../store'

const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString('en-GB')

function summary(e: { type: string; data: Record<string, any> }): string {
  const d = e.data
  switch (e.type) {
    case 'agent.thought': return `${d.action} — ${d.thought}`
    case 'tool.call': return `${d.tool} ${d.args}`
    case 'tool.result': return `${d.ok ? '✓' : '✗'} ${d.tool}: ${d.preview}`
    case 'agent.end': return `${d.success ? 'OK' : 'KO'} ${d.summary}`
    case 'agent.spawn': return `${d.role}: ${d.task}`
    case 'goal.update': return `#${d.id} [${d.status}] ${d.title}`
    case 'chat.message': return `${d.role}: ${d.text}`
    case 'system.log': return d.text
    case 'tool.created': return `${d.name} (${d.passed ? 'tests OK' : 'tests failed'})`
    case 'evolution': return `${d.kind} ${d.target}: ${d.status} — ${d.reason}`
    case 'introspection.probe': return `score ${d.score}: ${d.answer}`
    case 'lesson.learned': return `${d.new ? 'NEW' : `x${d.count}`} [${d.kind}] ${d.text}`
    case 'cycle.start': return `cycle ${d.cycle}`
    default: return JSON.stringify(d).slice(0, 160)
  }
}

export function EventFeed() {
  const events = useBrain((s) => s.events)
  const names = useBrain((s) => s.names)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight }, [events.length])
  return (
    <div className="scroll feed" ref={ref}>
      {events.map((e, i) => (
        <div className="ev" key={`${e.seq}-${i}`}>
          <span className="t">{time(e.ts)}</span><span className="ty">{e.type}</span><span className="ag" title={e.agent ?? ''}>{e.agent ? names[e.agent] ?? e.agent : ''}</span><span className="tx" title={summary(e)}>{summary(e)}</span>
        </div>
      ))}
    </div>
  )
}

export function Chat() {
  const chat = useBrain((s) => s.chat)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight; if (chat.at(-1)?.role === 'brain') setWaiting(false) }, [chat])

  const send = async () => {
    const t = text.trim()
    if (!t || sending) return
    setSending(true)
    try { await api.chat(t); setText(''); setWaiting(true) } finally { setSending(false) }
  }
  return (
    <div className="chat">
      <div className="msgs" ref={ref}>
        {!chat.length && <div className="empty">Write to Brain: it replies and can turn your requests into goals.</div>}
        {chat.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.text}</div>)}
        {waiting && <div className="msg brain"><span className="spin" /> is thinking…</div>}
      </div>
      <div className="composer">
        <input value={text} placeholder="Talk to Brain…" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} />
        <button className="btn go" disabled={sending || !text.trim()} onClick={send}>Send</button>
      </div>
    </div>
  )
}

export function Journal() {
  const journal = useBrain((s) => s.journal)
  return (
    <div className="scroll">
      {!journal.length && <div className="empty">The journal fills up with Brain's reflections.</div>}
      {[...journal].reverse().map((j) => <div className="entry" key={j.id}><small>{j.kind} · {time(j.ts)}</small>{j.text}</div>)}
    </div>
  )
}

export function ToolsView() {
  const custom = useBrain((s) => s.customTools)
  const all = useBrain((s) => s.tools)
  const calls = useBrain((s) => s.toolCalls)
  const { builtin, usage } = useMemo(() => {
    const usage: Record<string, number> = {}
    for (const [k, n] of Object.entries(calls)) { const t = k.split('|')[1]; usage[t] = (usage[t] ?? 0) + n }
    const builtin = all.filter((t) => !t.custom).sort((a, b) => (usage[b.name] ?? 0) - (usage[a.name] ?? 0) || a.name.localeCompare(b.name))
    return { builtin, usage }
  }, [all, calls])
  const head = { margin: '4px 0 8px', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: '#7f89b8' } as const
  return (
    <div className="scroll">
      <div style={head}>Tools created by Brain ({custom.length})</div>
      {!custom.length && <div className="empty" style={{ padding: '4px 0 12px' }}>No tools created yet: Brain builds them, tests them in the sandbox and registers them here.</div>}
      <div className="cols">
        {custom.map((t) => (
          <div className="card" key={t.name}>
            <b>{t.name}</b> <small style={{ display: 'inline' }}>[{t.status}] calls {t.calls} · errors {t.failures}</small>
            <small>{t.description}</small>
          </div>
        ))}
      </div>
      <div style={{ ...head, marginTop: 14 }}>Built-in tools ({builtin.length})</div>
      <div className="cols">
        {builtin.map((t) => (
          <div className="card" key={t.name}>
            <b style={{ color: '#60a5fa' }}>{t.name}</b> <small style={{ display: 'inline' }}>uses {usage[t.name] ?? 0}</small>
            <small>{t.description}</small>
          </div>
        ))}
      </div>
    </div>
  )
}

export function LessonsView() {
  const lessons = useBrain((s) => s.lessons)
  return (
    <div className="scroll">
      {!lessons.length && <div className="empty">When a tool fails and is then fixed, or a goal fails, Brain derives a lesson from it and uses it in later prompts.</div>}
      {lessons.map((l) => (
        <div className="entry" key={l.text}>
          <small>{l.kind} · seen {l.count}× · {time(l.ts)}</small>{l.text}
        </div>
      ))}
    </div>
  )
}

export function EvolutionView() {
  const evs = useBrain((s) => s.evolutions)
  return (
    <div className="scroll">
      {!evs.length && <div className="empty">No evolution yet: prompts and hooks are changed only with tests and rollback.</div>}
      {evs.map((e) => (
        <div className="entry" key={e.id}>
          <small>{e.kind} · {e.target} · {time(e.ts)}</small>
          <b style={{ color: e.status === 'applied' ? '#34f5a0' : e.status === 'rolled_back' ? '#ffb020' : '#ff4d6d' }}>{e.status}</b> — {e.reason}
        </div>
      ))}
    </div>
  )
}
