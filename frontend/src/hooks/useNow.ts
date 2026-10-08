import { useEffect, useState } from 'react'

/** Re-renders the caller every `ms` so time-based visuals (fading agents) stay current. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(t)
  }, [ms])
  return now
}
