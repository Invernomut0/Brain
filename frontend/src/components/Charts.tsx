import { useMemo } from 'react'
import * as d3 from 'd3'

export function Sparkline({ data, color = '#22d3ee', height = 44, domain }: { data: number[]; color?: string; height?: number; domain?: [number, number] }) {
  const w = 280
  const { path, area } = useMemo(() => {
    if (data.length < 2) return { path: '', area: '' }
    const x = d3.scaleLinear().domain([0, data.length - 1]).range([0, w])
    const y = d3.scaleLinear().domain(domain ?? [0, Math.max(1, d3.max(data) ?? 1)]).range([height - 3, 3])
    const line = d3.line<number>().x((_, i) => x(i)).y((d) => y(d)).curve(d3.curveMonotoneX)
    const ar = d3.area<number>().x((_, i) => x(i)).y0(height).y1((d) => y(d)).curve(d3.curveMonotoneX)
    return { path: line(data) ?? '', area: ar(data) ?? '' }
  }, [data, height, domain])
  const id = useMemo(() => 'g' + Math.random().toString(36).slice(2, 7), [])
  return (
    <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      <defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={color} stopOpacity={0.4} /><stop offset="1" stopColor={color} stopOpacity={0} /></linearGradient></defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={path} fill="none" stroke={color} strokeWidth={1.8} />
    </svg>
  )
}

/** Radial gauge for the awareness index (0..1). */
export function Gauge({ value, label }: { value: number; label: string }) {
  const R = 54, C = 2 * Math.PI * R
  const arc = C * 0.75
  return (
    <svg viewBox="0 0 140 130" width="100%" height={130}>
      <defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stopColor="#8b7bff" /><stop offset="1" stopColor="#22d3ee" /></linearGradient></defs>
      <g transform="translate(70,70) rotate(135)">
        <circle r={R} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={10} strokeDasharray={`${arc} ${C}`} strokeLinecap="round" />
        <circle r={R} fill="none" stroke="url(#gg)" strokeWidth={10} strokeDasharray={`${arc * Math.min(1, value)} ${C}`} strokeLinecap="round" style={{ transition: 'stroke-dasharray 0.8s', filter: 'drop-shadow(0 0 6px #8b7bff)' }} />
      </g>
      <text x="70" y="72" textAnchor="middle" fill="#e6e9ff" fontSize="26" fontWeight="700">{(value * 100).toFixed(0)}</text>
      <text x="70" y="90" textAnchor="middle" fill="#7f89b8" fontSize="8" letterSpacing="2">{label}</text>
    </svg>
  )
}
