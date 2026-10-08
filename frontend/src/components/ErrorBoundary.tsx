import { Component, type ReactNode } from 'react'

/** Keeps a failing view from blanking the whole dashboard. */
export class ErrorBoundary extends Component<{ name: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="empty" style={{ padding: 18 }}>
        Errore nella vista “{this.props.name}”: {this.state.error.message}
        <br /><button className="btn" style={{ marginTop: 10 }} onClick={() => this.setState({ error: null })}>Riprova</button>
      </div>
    )
  }
}
