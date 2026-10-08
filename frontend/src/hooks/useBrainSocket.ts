import { useEffect } from 'react'
import { useBrain } from '../store'

/** Keeps a WebSocket open to the backend, reconnecting with backoff. */
export function useBrainSocket() {
  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let retry = 500
    let timer: number | undefined

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => { retry = 500; useBrain.getState().setConnected(true) }
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data)
        if (msg.type === 'snapshot') useBrain.getState().applySnapshot(msg.data)
        else useBrain.getState().applyEvent(msg)
      }
      ws.onclose = () => {
        useBrain.getState().setConnected(false)
        if (!closed) { timer = window.setTimeout(connect, retry); retry = Math.min(retry * 2, 8000) }
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    return () => { closed = true; window.clearTimeout(timer); ws?.close() }
  }, [])
}
