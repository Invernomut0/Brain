import { useState } from 'react'
import { AgentList } from './components/AgentList'
import { Chat, EventFeed, EvolutionView, Journal, ToolsView } from './components/Dock'
import { GoalTree } from './components/GoalTree'
import { Header } from './components/Header'
import { LiveThoughts } from './components/LiveThoughts'
import { MetricsPanel } from './components/MetricsPanel'
import { Neural3D } from './components/Neural3D'
import { SelfModelPanel } from './components/SelfModelPanel'
import { ToolGalaxy } from './components/ToolGalaxy'
import { useBrainSocket } from './hooks/useBrainSocket'
import { ROLE_COLOR } from './store'

const STAGE = { neural: 'Rete neurale 3D', goals: 'Albero obiettivi', tools: 'Agenti ↔ Tool' } as const
const DOCK = { feed: 'Eventi live', chat: 'Chat', journal: 'Giornale', tools: 'Tool', evo: 'Evoluzione' } as const

export default function App() {
  useBrainSocket()
  const [stage, setStage] = useState<keyof typeof STAGE>('neural')
  const [dock, setDock] = useState<keyof typeof DOCK>('feed')

  return (
    <div className="app">
      <Header />
      <aside style={{ gridColumn: 1, gridRow: 2, display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
        <AgentList />
        <SelfModelPanel />
      </aside>
      <main className="panel stage">
        <div className="tabs">
          {(Object.keys(STAGE) as (keyof typeof STAGE)[]).map((k) => <div key={k} className={`tab ${stage === k ? 'on' : ''}`} onClick={() => setStage(k)}>{STAGE[k]}</div>)}
        </div>
        <div className="legend">
          {Object.entries(ROLE_COLOR).slice(0, 6).map(([r, c]) => <span key={r}><i style={{ background: c }} />{r}</span>)}
        </div>
        <div className="view">
          {stage === 'neural' && <Neural3D />}
          {stage === 'goals' && <GoalTree />}
          {stage === 'tools' && <ToolGalaxy />}
        </div>
        {stage === 'neural' && <LiveThoughts />}
      </main>
      <aside style={{ gridColumn: 3, gridRow: 2, display: 'flex', minHeight: 0 }}>
        <MetricsPanel />
      </aside>
      <section className="panel dock">
        <div className="tabs">
          {(Object.keys(DOCK) as (keyof typeof DOCK)[]).map((k) => <div key={k} className={`tab ${dock === k ? 'on' : ''}`} onClick={() => setDock(k)}>{DOCK[k]}</div>)}
        </div>
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', paddingTop: 6 }}>
          {dock === 'feed' && <EventFeed />}
          {dock === 'chat' && <Chat />}
          {dock === 'journal' && <Journal />}
          {dock === 'tools' && <ToolsView />}
          {dock === 'evo' && <EvolutionView />}
        </div>
      </section>
    </div>
  )
}
