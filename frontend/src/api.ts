async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export const api = {
  control: (action: 'start' | 'pause' | 'resume' | 'stop' | 'kill') => post(`/control/${action}`),
  budget: (b: { max_cycles?: number; max_tokens?: number }) => post('/budget', b),
  chat: (text: string) => post('/chat', { text }),
  reset: () => post('/reset', { confirm: 'RESET' }),
}
