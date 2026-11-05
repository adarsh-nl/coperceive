// SPDX-License-Identifier: Apache-2.0
import React, { useEffect } from "react";

export type ToastTone = "info" | "success" | "error" | "warning";

export interface ToastProps {
  message: React.ReactNode;
  tone?: ToastTone;
  onDismiss?: () => void;
  durationMs?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function Toast({
  message,
  tone = "info",
  onDismiss,
  durationMs,
  className = "",
  style,
}: ToastProps) {
  useEffect(() => {
    if (typeof durationMs === "number" && durationMs > 0 && onDismiss) {
      const timer = setTimeout(onDismiss, durationMs);
      return () => clearTimeout(timer);
    }
  }, [durationMs, onDismiss]);

  const borderToneMap: Record<ToastTone, string> = {
    info: "var(--line-elevated, rgba(255,255,255,0.14))",
    success: "rgba(63, 185, 80, 0.5)",
    error: "rgba(255, 123, 114, 0.5)",
    warning: "rgba(224, 164, 49, 0.5)",
  };

  const bgToneMap: Record<ToastTone, string> = {
    info: "var(--panel, #1a1a1c)",
    success: "rgba(63, 185, 80, 0.12)",
    error: "rgba(255, 123, 114, 0.12)",
    warning: "rgba(224, 164, 49, 0.12)",
  };

  return (
    <div
      role="status"
      className={`c-toast tone-${tone} ${className}`.trim()}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        padding: "8px 14px",
        borderRadius: "var(--radius-md, 6px)",
        border: `1px solid ${borderToneMap[tone]}`,
        backgroundColor: bgToneMap[tone],
        color: "var(--text, #f5f5f7)",
        fontSize: "12.5px",
        boxShadow: "0 4px 12px rgba(0, 0, 0, 0.25)",
        maxWidth: "480px",
        ...style,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>{message}</div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          style={{
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
            color: "var(--text-tertiary, #a1a1a6)",
            fontSize: "14px",
            lineHeight: 1,
          }}
        >
          ×
        </button>
      )}
    </div>
  );
}
