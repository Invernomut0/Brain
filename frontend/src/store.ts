import { create } from 'zustand'
import type {
  AgentView, BrainEvent, ChatMsg, Control, CustomTool, Evolution, Goal, Health, JournalEntry, Lesson,
  Metrics, Pulse, SelfModel, SysPoint, ToolInfo,
} from './types'

export const ROLE_COLOR: Record<string, string> = {
  planner: '#8b7bff', executor: '#22d3ee', researcher: '#34f5a0', engineer: '#ffb020', critic: '#ff4d6d',
  reflector: '#f472d0', evolver: '#fde047', voice: '#a5b4fc',
}
export const roleColor = (r: string) => ROLE_COLOR[r] ?? '#94a3b8'

const EMPTY_METRICS: Metrics = {
  awareness_index: 0, calibration: null, introspection: null, success_rate: 0, goals_done: 0, goals_failed: 0,
  tools: 0, journal: 0, memories: 0, agents_spawned: 0, selfmodel_revision: 0, evolutions_applied: 0, evolutions_rolled_back: 0, lessons: 0,
}

const WEB_TOOLS = new Set(['web_search', 'web_fetch', 'http_request'])
const SANDBOX_TOOLS = new Set(['python_exec', 'shell_exec', 'read_file', 'write_file', 'list_files'])
const MEMORY_TOOLS = new Set(['remember', 'recall'])

/** Node in the 3D scene that a tool call is routed to. */
export function toolTarget(tool: string): string {
  if (WEB_TOOLS.has(tool)) return 'internet'
  if (SANDBOX_TOOLS.has(tool)) return 'sandbox'
  if (MEMORY_TOOLS.has(tool)) return 'memory'
  if (tool === 'ask_user') return 'user'
  return `tool:${tool}`
}

interface Store {
  connected: boolean
  control: Control
  goals: Record<number, Goal>
  agents: Record<string, AgentView>
  tools: ToolInfo[]
  customTools: CustomTool[]
  toolCalls: Record<string, number>
  activity: Record<string, number>
  pulses: Pulse[]
  events: BrainEvent[]
  history: SysPoint[]
  metrics: Metrics
  health: Health
  selfmodel: SelfModel | null
  journal: JournalEntry[]
  chat: ChatMsg[]
  evolutions: Evolution[]
  lessons: Lesson[]
  streams: Record<string, string>
  streamTps: Record<string, number>
  thoughtsInset: number  // left px covered by the live-thoughts panel (lets 2D views avoid it)
  wikiRev: number  // bumped on every wiki.update event so the wiki view refetches
  graphMode: '2d' | '3d'  // shared by the goal tree and the wiki graph
  project: { name: string; saved: boolean }  // active project (see the Projects dialog)
  owner: string  // who Brain works for; empty until asked
  loaded: boolean  // first snapshot received
  setGraphMode: (m: '2d' | '3d') => void
  sys: { cpu: number; mem: number; tps: number; tokens: number; llm_busy: number; llm_queued: number; calls: number; uptime: number; live_agents: number }
  setConnected: (c: boolean) => void
  applySnapshot: (s: any) => void
  resetLocal: () => void
  applyEvent: (e: BrainEvent) => void
}

let pulseId = 0
const MAX_EVENTS = 400

export const useBrain = create<Store>((set, get) => ({
  connected: false,
  control: { state: 'idle', cycle: 0, max_cycles: 0, max_tokens: 0 },
  goals: {}, agents: {}, tools: [], customTools: [], toolCalls: {}, activity: {}, pulses: [], events: [], history: [],
  metrics: EMPTY_METRICS, health: {}, selfmodel: null, journal: [], chat: [], evolutions: [], lessons: [], streams: {}, streamTps: {}, thoughtsInset: 0, wikiRev: 0,
  graphMode: location.hash.endsWith(':3d') || localStorage.getItem('brain.graphmode') === '3d' ? '3d' : '2d',
  project: { name: 'Untitled project', saved: false },
  owner: '',
  loaded: false,
  setGraphMode: (graphMode) => { localStorage.setItem('brain.graphmode', graphMode); set({ graphMode }) },
  sys: { cpu: 0, mem: 0, tps: 0, tokens: 0, llm_busy: 0, llm_queued: 0, calls: 0, uptime: 0, live_agents: 0 },

  setConnected: (connected) => set({ connected }),

  resetLocal: () => set({
    goals: {}, agents: {}, tools: [], customTools: [], toolCalls: {}, activity: {}, pulses: [], events: [], history: [],
    metrics: EMPTY_METRICS, selfmodel: null, journal: [], chat: [], evolutions: [], lessons: [], streams: {}, streamTps: {},
  }),

  applySnapshot: (s) => {
    const goals: Record<number, Goal> = {}
    for (const g of s.goals) goals[g.id] = g
    const agents: Record<string, AgentView> = {}
    for (const a of s.agents) {
      agents[a.id] = {
        id: a.id, role: a.role, parent: a.parent, goal_id: a.goal_id, task: a.task ?? '', state: a.state ?? 'idle', detail: a.detail ?? '',
        thought: a.thought ?? '', action: a.action ?? '', steps: a.steps ?? 0, bornAt: (a.born ?? Date.now() / 1000) * 1000,
        endedAt: a.ended ? a.ended * 1000 : null, success: a.success ?? null, summary: a.summary ?? '',
      }
    }
    const streams: Record<string, string> = {}
    const streamTps: Record<string, number> = {}
    for (const [id, v] of Object.entries<any>(s.streams ?? {})) {
      streams[id] = (v.reasoning ? '(ragiona) ' : '') + v.text
      streamTps[id] = v.tps
    }
    set({
      control: s.control, goals, agents, tools: s.tools, customTools: s.custom_tools, metrics: s.metrics,
      health: s.health ?? {}, selfmodel: s.selfmodel, journal: s.journal, chat: s.chat.map((c: any) => ({ role: c.role, text: c.text, ts: c.ts })),
      evolutions: s.evolutions, events: s.events, lessons: s.lessons ?? [], streams, streamTps,
      project: s.project ? { name: s.project.name, saved: s.project.saved } : { name: 'Untitled project', saved: false },
      owner: s.owner ?? '', loaded: true,
    })
  },

  applyEvent: (e) => {
    const st = get()
    const d = e.data
    const now = Date.now()
    const patch: Partial<Store> = {}
    const addPulse = (from: string, to: string, color: string) => {
      patch.pulses = [...(patch.pulses ?? st.pulses).filter((p) => now - p.t0 < 2500), { id: ++pulseId, from, to, color, t0: now }]
    }
    const touch = (id: string) => { patch.activity = { ...(patch.activity ?? st.activity), [id]: now } }

    switch (e.type) {
      case 'system.metrics': {
        patch.sys = { cpu: d.cpu, mem: d.mem, tps: d.tps, tokens: d.tokens, llm_busy: d.llm_busy, llm_queued: d.llm_queued ?? 0, calls: d.calls, uptime: d.uptime, live_agents: d.live_agents }
        patch.health = d.health
        patch.control = d.control
        patch.metrics = d.metrics
        patch.history = [...st.history, { t: now, cpu: d.cpu, mem: d.mem, tps: d.tps, tokens: d.tokens, awareness: d.metrics.awareness_index }].slice(-150)
        set(patch)
        return
      }
      case 'agent.stream': {
        if (e.agent) set({ streams: { ...st.streams, [e.agent]: (d.reasoning ? '(ragiona) ' : '') + d.text }, streamTps: { ...st.streamTps, [e.agent]: d.tps } })
        return
      }
      case 'control.state': patch.control = d as Control; break
      case 'goal.update': patch.goals = { ...st.goals, [d.id]: d as Goal }; break
      case 'agent.spawn': {
        const id = e.agent as string
        patch.agents = { ...st.agents, [id]: {
          id, role: d.role, parent: d.parent, goal_id: d.goal_id, task: d.task ?? '', state: 'idle', detail: '', thought: '', action: '',
          steps: 0, bornAt: now, endedAt: null, success: null, summary: '',
        } }
        addPulse('core', id, roleColor(d.role))
        break
      }
      case 'agent.state': {
        const a = st.agents[e.agent as string]
        if (a) patch.agents = { ...st.agents, [a.id]: { ...a, state: d.state, detail: d.detail } }
        touch(e.agent as string)
        break
      }
      case 'agent.thought': {
        const a = st.agents[e.agent as string]
        if (a) patch.agents = { ...st.agents, [a.id]: { ...a, thought: d.thought, action: d.action, steps: a.steps + 1 } }
        break
      }
      case 'agent.end': {
        const a = st.agents[e.agent as string]
        if (a) patch.agents = { ...st.agents, [a.id]: { ...a, state: d.success ? 'done' : 'failed', endedAt: now, success: d.success, summary: d.summary } }
        if (e.agent) addPulse(e.agent, 'core', d.success ? '#34f5a0' : '#ff4d6d')
        break
      }
      case 'agent.msg': addPulse(e.agent as string, d.to, '#ffffff'); break
      case 'tool.call': {
        const a = st.agents[e.agent as string]
        const target = toolTarget(d.tool)
        addPulse(e.agent as string, target, a ? roleColor(a.role) : '#22d3ee')
        touch(target)
        const key = `${e.agent}|${d.tool}`
        patch.toolCalls = { ...st.toolCalls, [key]: (st.toolCalls[key] ?? 0) + 1 }
        break
      }
      case 'tool.result': addPulse(toolTarget(d.tool), e.agent as string, d.ok ? '#34f5a0' : '#ff4d6d'); break
      case 'tool.created': {
        patch.customTools = [...st.customTools.filter((t) => t.name !== d.name), { name: d.name, description: d.description, status: d.passed ? 'active' : 'rejected', calls: 0, failures: 0 }]
        if (d.passed) patch.tools = [...st.tools.filter((t) => t.name !== d.name), { name: d.name, description: d.description, custom: true }]
        break
      }
      case 'chat.message': {
        patch.chat = [...st.chat, { role: d.role, text: d.text, ts: e.ts }].slice(-100)
        if (d.role === 'user') { addPulse('user', 'core', '#ffffff') } else { addPulse('core', 'user', '#a5b4fc'); touch('user') }
        break
      }
      case 'selfmodel.update': patch.selfmodel = d.model as SelfModel; break
      case 'lesson.learned': {
        const rest = st.lessons.filter((l) => l.text !== d.text)
        patch.lessons = [{ text: d.text, kind: d.kind, count: d.count, ts: e.ts }, ...rest].slice(0, 60)
        touch('core')
        break
      }
      case 'journal.entry': patch.journal = [...st.journal, { id: e.seq, ts: e.ts, kind: d.kind, text: d.text }].slice(-60); break
      case 'evolution': patch.evolutions = [{ id: e.seq, ts: e.ts, kind: d.kind, target: d.target, status: d.status, reason: d.reason }, ...st.evolutions].slice(0, 40); touch('core'); break
      case 'memory.add': addPulse('core', 'memory', '#60a5fa'); touch('memory'); break
      case 'wiki.update': patch.wikiRev = st.wikiRev + 1; break
      case 'project.changed': patch.project = { name: d.name, saved: true }; break
      case 'owner.changed': patch.owner = d.name; break
      case 'llm.start': if (e.agent) touch('core'); break
    }
    if (e.type !== 'llm.end' && e.type !== 'llm.start') patch.events = [...st.events, e].slice(-MAX_EVENTS)
    set(patch)
  },
}))
