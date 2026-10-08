import { useState } from 'react'
import { AgentList } from './components/AgentList'
import { Chat, EventFeed, EvolutionView, Journal, LessonsView, ToolsView } from './components/Dock'
import { GoalTree } from './components/GoalTree'
import { Header } from './components/Header'
import { ErrorBoundary } from './components/ErrorBoundary'
import { LiveThoughts } from './components/LiveThoughts'
import { MainGoalCard, StartOverlay } from './components/MainGoal'
import { MetricsPanel } from './components/MetricsPanel'
import { Neural3D } from './components/Neural3D'
import { Neural2D, ViewOff } from './components/Neural2D'
import { SelfModelPanel } from './components/SelfModelPanel'
import { ToolGalaxy } from './components/ToolGalaxy'
import { useBrainSocket } from './hooks/useBrainSocket'
import { ROLE_COLOR } from './store'

const STAGE = { neural: 'Rete neurale', goals: 'Albero obiettivi', tools: 'Agenti ↔ Tool' } as const
const VMODE_KEY = 'brain.viewmode'
type VMode = '3d' | '2d' | 'off'
const VMODES: { id: VMode; label: string; hint: string }[] = [
  { id: '3d', label: '3D', hint: 'Vista 3D (usa la GPU)' },
  { id: '2d', label: '2D', hint: 'Vista 2D leggera, senza WebGL' },
  { id: 'off', label: 'Off', hint: 'Spegne la vista grafica: nessun uso di GPU' },
]
const DOCK = { feed: 'Eventi live', chat: 'Chat', journal: 'Giornale', lessons: 'Lezioni', tools: 'Tool', evo: 'Evoluzione' } as const

export default function App() {
  useBrainSocket()
  const [stage, setStage] = useState<keyof typeof STAGE>('neural')
  const [dock, setDock] = useState<keyof typeof DOCK>('feed')
  const [vmode, setVmodeState] = useState<VMode>(() => (['3d', '2d', 'off'].includes(localStorage.getItem(VMODE_KEY) ?? '') ? localStorage.getItem(VMODE_KEY) as VMode : '3d'))
  const setVmode = (m: VMode) => { localStorage.setItem(VMODE_KEY, m); setVmodeState(m) }

  return (
    <div className="app">
      <Header />
      <aside style={{ gridColumn: 1, gridRow: 2, display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
        <MainGoalCard />
        <AgentList />
        <SelfModelPanel />
      </aside>
      <main className="panel stage">
        <div className="tabs">
          {(Object.keys(STAGE) as (keyof typeof STAGE)[]).map((k) => <div key={k} className={`tab ${stage === k ? 'on' : ''}`} onClick={() => setStage(k)}>{STAGE[k]}</div>)}
          {stage === 'neural' && (
            <div className="vmode" title="Modalità di visualizzazione">
              {VMODES.map((m) => <button key={m.id} title={m.hint} className={vmode === m.id ? 'on' : ''} onClick={() => setVmode(m.id)}>{m.label}</button>)}
            </div>
          )}
        </div>
        <div className="legend">
          {Object.entries(ROLE_COLOR).slice(0, 6).map(([r, c]) => <span key={r}><i style={{ background: c }} />{r}</span>)}
        </div>
        <div className="view">
          <ErrorBoundary key={`${stage}-${vmode}`} name={STAGE[stage]}>
            {stage === 'neural' && vmode === '3d' && <Neural3D />}
            {stage === 'neural' && vmode === '2d' && <Neural2D />}
            {stage === 'neural' && vmode === 'off' && <ViewOff onPick={setVmode} />}
            {stage === 'goals' && <GoalTree />}
            {stage === 'tools' && <ToolGalaxy />}
          </ErrorBoundary>
        </div>
        {stage === 'neural' && vmode !== 'off' && <LiveThoughts />}
        <StartOverlay />
      </main>
      <aside style={{ gridColumn: 3, gridRow: 2, display: 'flex', minHeight: 0 }}>
        <MetricsPanel />
      </aside>
      <section className="panel dock">
        <div className="tabs">
          {(Object.keys(DOCK) as (keyof typeof DOCK)[]).map((k) => <div key={k} className={`tab ${dock === k ? 'on' : ''}`} onClick={() => setDock(k)}>{DOCK[k]}</div>)}
        </div>
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', paddingTop: 6 }}>
          <ErrorBoundary key={dock} name={DOCK[dock]}>
            {dock === 'feed' && <EventFeed />}
            {dock === 'chat' && <Chat />}
            {dock === 'journal' && <Journal />}
            {dock === 'lessons' && <LessonsView />}
            {dock === 'tools' && <ToolsView />}
            {dock === 'evo' && <EvolutionView />}
          </ErrorBoundary>
        </div>
      </section>
    </div>
  )
}
