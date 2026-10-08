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

export const api = {
  get: <T = unknown>(path: string) => call<T>('GET', path),
  post,
  control: (action: 'start' | 'pause' | 'resume' | 'stop' | 'kill') => post(`/control/${action}`),
  budget: (b: { max_cycles?: number; max_tokens?: number }) => post('/budget', b),
  chat: (text: string) => post('/chat', { text }),
  reset: () => post('/reset', { confirm: 'RESET' }),
  setMainGoal: (text: string, archive_pending: boolean) => put<{ cancelled: number }>('/main-goal', { text, archive_pending }),
}
