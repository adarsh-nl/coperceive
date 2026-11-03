// SPDX-License-Identifier: Apache-2.0
import { Component, type ErrorInfo, type ReactNode } from "react";
import { DiagnosticsDialog } from "./DiagnosticsDialog";

interface State {
  error: { message: string; stack?: string } | null;
  sharing: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, sharing: false };

  static getDerivedStateFromError(error: Error): State {
    return { error: { message: error.message, stack: error.stack }, sharing: false };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    const value = { message: error.message, stack: `${error.stack ?? ""}\n${info.componentStack ?? ""}` };
    (window as unknown as { __COPERCEIVE_LAST_ERROR__?: unknown }).__COPERCEIVE_LAST_ERROR__ = value;
    try {
      localStorage.setItem("coperceive-last-ui-error", JSON.stringify(value));
    } catch {
      // Storage can be unavailable; the panel still works.
    }
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div className="error-panel" role="alert">
        <h1>Something went wrong</h1>
        <p>Coperceive hit an error in this window. Your data is safe.</p>
        <div className="diagnostics-actions">
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
          <button type="button" onClick={() => this.setState({ sharing: true })}>
            Share diagnostics
          </button>
        </div>
        {this.state.sharing ? (
          <DiagnosticsDialog
            context={{ active_route: window.location.hash || "#/", ui_error: this.state.error }}
            initialError={this.state.error}
            onClose={() => this.setState({ sharing: false })}
          />
        ) : null}
      </div>
    );
  }
}
