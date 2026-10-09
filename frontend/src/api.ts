async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

const post = <T = unknown>(path: string, body?: unknown) => call<T>('POST', path, body)
const put = <T = unknown>(path: string, body?: unknown) => call<T>('PUT', path, body)

export interface ProjectMeta {
  id: string; name: string; saved_at: number; app_version: string; cycle: number; main_goal: string
  goals: { done: number; failed: number; total: number }
  memories: number; lessons: number; tools: number; wiki_pages: number; size_bytes: number; current?: boolean
}
export interface ProjectInfo { name: string; id: string; saved: boolean; saved_at: number | null }

export const api = {
  get: <T = unknown>(path: string) => call<T>('GET', path),
  post,
  control: (action: 'start' | 'pause' | 'resume' | 'stop' | 'kill') => post(`/control/${action}`),
  budget: (b: { max_cycles?: number; max_tokens?: number }) => post('/budget', b),
  chat: (text: string) => post('/chat', { text }),
  reset: () => post('/reset', { confirm: 'RESET' }),
  setMainGoal: (text: string, archive_pending: boolean) => put<{ cancelled: number }>('/main-goal', { text, archive_pending }),
  projects: () => call<{ current: ProjectInfo; items: ProjectMeta[] }>('GET', '/projects'),  saveProject: (name?: string) => post<ProjectMeta>('/projects/save', { name: name || null }),
  loadProject: (id: string, save_current: boolean) => post<ProjectMeta>(`/projects/${encodeURIComponent(id)}/load`, { save_current }),
  newProject: (name: string, main_goal: string, save_current: boolean, owner: string) => post<ProjectMeta>('/projects/new', { name, main_goal: main_goal.trim() || null, save_current, owner: owner.trim() || null }),
  setOwner: (name: string) => put<{ name: string }>('/owner', { name }),
  setNaming: (style: string) => put<{ style: string; styles: string[] }>('/naming', { style }),
  renameAgent: (id: string, name: string | null) => put<{ agent_id: string; name: string }>(`/agents/${encodeURIComponent(id)}/name`, { name }),
  deleteProject: (id: string) => call<{ deleted: string }>('DELETE', `/projects/${encodeURIComponent(id)}`),
}
