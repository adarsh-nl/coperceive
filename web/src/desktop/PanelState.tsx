// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";

export interface PanelStateProps {
  kind: "empty" | "loading" | "error";
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  actionDisabled?: boolean;
  icon?: ReactNode;
  compact?: boolean;
  className?: string;
  id?: string;
}

export default function PanelState({
  kind,
  title,
  description,
  actionLabel,
  onAction,
  actionDisabled = false,
  icon,
  compact = false,
  className = "",
  id,
}: PanelStateProps) {
  const defaultIcon =
    kind === "loading" ? "⏳" : kind === "error" ? "⚠️" : "📦";

  return (
    <div
      id={id}
      data-testid={`panel-state-${kind}`}
      className={`panel-state panel-state-${kind} ${compact ? "panel-state-compact" : ""} ${className}`}
      role={kind === "error" ? "alert" : "status"}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        padding: compact ? "1.25rem 0.8rem" : "3rem 1.5rem",
        borderRadius: "10px",
        border:
          kind === "error"
            ? "1px solid var(--danger)"
            : "1px dashed var(--line)",
        backgroundColor: "var(--sunken)",
        color: "var(--text)",
        margin: compact ? "0.4rem 0" : "1rem 0",
      }}
    >
      <div
        className="panel-state-icon"
        style={{
          fontSize: compact ? "1.4rem" : "2.2rem",
          marginBottom: compact ? "0.4rem" : "0.75rem",
          display: "inline-block",
        }}
        aria-hidden="true"
      >
        {icon ?? defaultIcon}
      </div>
      <h3
        className="panel-state-title"
        style={{
          fontSize: compact ? "0.95rem" : "1.15rem",
          fontWeight: 600,
          margin: "0 0 0.35rem 0",
          color: kind === "error" ? "var(--danger)" : "var(--text)",
        }}
      >
        {title}
      </h3>
      <p
        className="panel-state-description"
        style={{
          fontSize: compact ? "0.8rem" : "0.88rem",
          color: "var(--muted)",
          margin: "0 0 1rem 0",
          maxWidth: compact ? "280px" : "420px",
          lineHeight: 1.45,
        }}
      >
        {description}
      </p>
      {actionLabel && onAction ? (
        <button
          type="button"
          className={`panel-state-action ${kind === "error" ? "panel-state-btn-danger" : "panel-state-btn-primary"}`}
          onClick={onAction}
          disabled={actionDisabled}
          style={{
            background: kind === "error" ? "transparent" : "#1f6feb",
            border: kind === "error" ? "1px solid var(--danger)" : "none",
            color: kind === "error" ? "var(--danger)" : "#ffffff",
            padding: compact ? "0.35rem 0.75rem" : "0.5rem 1.1rem",
            borderRadius: "6px",
            fontSize: compact ? "0.8rem" : "0.85rem",
            fontWeight: 500,
            cursor: actionDisabled ? "not-allowed" : "pointer",
            opacity: actionDisabled ? 0.6 : 1,
          }}
        >
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}
