export type RunState = 'idle' | 'running' | 'paused' | 'stopped' | 'killed'

export interface Control { state: RunState; cycle: number; max_cycles: number; max_tokens: number }

export interface Goal {
  id: number; parent_id: number | null; title: string; description: string; status: string
  priority: number; expected_success: number | null; result: string | null; attempts: number; role: string
}

export interface AgentView {
  id: string; role: string; parent: string | null; goal_id: number | null; task: string
  state: string; detail: string; thought: string; action: string; steps: number
  bornAt: number; endedAt: number | null; success: boolean | null; summary: string
}

export interface ToolInfo { name: string; description: string; custom: boolean }
export interface CustomTool { name: string; description: string; status: string; calls: number; failures: number }

export interface Metrics {
  awareness_index: number; calibration: number | null; introspection: number | null; success_rate: number
  goals_done: number; goals_failed: number; tools: number; journal: number; memories: number
  agents_spawned: number; selfmodel_revision: number; evolutions_applied: number; evolutions_rolled_back: number
}

export interface SelfModel {
  identity: string; purpose: string; capabilities: string[]; limitations: string[]
  about_user: string; about_world: string; open_questions: string[]; hypotheses: string[]; revision: number
}

export interface BrainEvent { seq: number; ts: number; type: string; agent: string | null; data: Record<string, any> }
export interface ChatMsg { role: 'user' | 'brain'; text: string; ts: number }
export interface JournalEntry { id: number; ts: number; kind: string; text: string }
export interface Evolution { id: number; ts: number; kind: string; target: string; status: string; reason: string }

export interface SysPoint { t: number; cpu: number; mem: number; tps: number; tokens: number; awareness: number }
export interface Pulse { id: number; from: string; to: string; color: string; t0: number }

export interface Health {
  llm?: { ok: boolean; model?: string; error?: string }
  sandbox?: { ok: boolean; image?: boolean; error?: string; running?: boolean; container?: string; active?: number }
}
