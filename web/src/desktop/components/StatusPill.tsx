// SPDX-License-Identifier: Apache-2.0
import React from "react";

export type StatusTone = "ready" | "preparing" | "queued" | "error" | "neutral";

export interface StatusPillProps {
  tone: StatusTone;
  label: React.ReactNode;
  subtitle?: React.ReactNode;
  progressPercent?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function StatusPill({
  tone,
  label,
  subtitle,
  progressPercent,
  className = "",
  style,
}: StatusPillProps) {
  const dotColorMap: Record<StatusTone, string> = {
    ready: "var(--ok, #3fb950)",
    preparing: "var(--primary-hover, #388bfd)",
    queued: "rgba(121, 192, 255, 0.4)",
    error: "var(--danger, #ff7b72)",
    neutral: "var(--text-subtle, #6e6e73)",
  };

  const textColorMap: Record<StatusTone, string> = {
    ready: "var(--text, #f5f5f7)",
    preparing: "var(--primary-hover, #388bfd)",
    queued: "var(--text-tertiary, #a1a1a6)",
    error: "var(--danger, #ff7b72)",
    neutral: "var(--text-tertiary, #a1a1a6)",
  };

  return (
    <span
      className={`c-status-pill tone-${tone} ${className}`.trim()}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        fontSize: "12px",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      <span
        className="c-status-dot"
        aria-hidden="true"
        style={{
          width: "7px",
          height: "7px",
          borderRadius: "50%",
          backgroundColor: dotColorMap[tone],
          flexShrink: 0,
        }}
      />
      <span style={{ color: textColorMap[tone], fontWeight: 500 }}>{label}</span>
      {typeof progressPercent === "number" && (
        <span
          className="c-status-progress"
          style={{
            width: "56px",
            height: "3px",
            borderRadius: "2px",
            background: "rgba(56, 139, 253, 0.2)",
            display: "inline-block",
            overflow: "hidden",
            marginLeft: "2px",
          }}
        >
          <span
            style={{
              display: "block",
              height: "100%",
              width: `${Math.max(0, Math.min(100, progressPercent))}%`,
              background: "var(--primary-hover, #388bfd)",
              transition: "width 0.4s ease",
            }}
          />
        </span>
      )}
      {subtitle && (
        <span style={{ color: "var(--text-subtle, #6e6e73)", fontSize: "11px" }}>{subtitle}</span>
      )}
    </span>
  );
}
