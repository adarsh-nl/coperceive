// SPDX-License-Identifier: Apache-2.0
import React from "react";

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className = "",
  style,
}: EmptyStateProps) {
  return (
    <div
      className={`c-empty-state ${className}`.trim()}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        padding: "48px 24px",
        gap: "12px",
        color: "var(--text-tertiary, #a1a1a6)",
        ...style,
      }}
    >
      {icon && (
        <div
          style={{
            fontSize: "32px",
            color: "var(--text-subtle, #6e6e73)",
            marginBottom: "4px",
          }}
        >
          {icon}
        </div>
      )}
      <div
        style={{
          fontSize: "15px",
          fontWeight: 500,
          color: "var(--text, #f5f5f7)",
        }}
      >
        {title}
      </div>
      {description && (
        <p
          style={{
            margin: 0,
            fontSize: "12.5px",
            color: "var(--text-tertiary, #a1a1a6)",
            maxWidth: "380px",
            lineHeight: 1.5,
          }}
        >
          {description}
        </p>
      )}
      {action && <div style={{ marginTop: "6px" }}>{action}</div>}
    </div>
  );
}
