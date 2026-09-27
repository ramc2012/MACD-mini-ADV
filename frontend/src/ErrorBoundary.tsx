import { Component, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <section className="panel recovery-panel" role="alert">
      <h1>The terminal could not display this data</h1>
      <p>Your paper book is stored on the server. Reload to fetch a fresh snapshot.</p>
      <button onClick={() => window.location.reload()}>Reload terminal</button>
    </section>;
    return this.props.children;
  }
}
