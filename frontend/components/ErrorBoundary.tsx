"use client";
import React from "react";

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            background: "#161b22",
            border: "1px solid #f85149",
            color: "#f85149",
            padding: 16,
            borderRadius: 8,
            margin: 16,
          }}
        >
          <h2 style={{ margin: 0 }}>Something went wrong</h2>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
            {this.state.error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 8, background: "#30363d", border: "1px solid #444c56", color: "#c9d1d9", padding: "6px 12px", borderRadius: 6 }}
          >
            Dismiss
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
