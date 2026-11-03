// SPDX-License-Identifier: Apache-2.0
import React from "react";

export interface SidebarItemProps {
  label: React.ReactNode;
  icon?: React.ReactNode;
  badge?: React.ReactNode;
  active?: boolean;
  indent?: boolean;
  onClick?: () => void;
  action?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export function SidebarItem({
  label,
  icon,
  badge,
  active = false,
  indent = false,
  onClick,
  action,
  className = "",
  style,
}: SidebarItemProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.();
        }
      }}
      className={`c-sidebar-item ${active ? "active" : ""} ${className}`.trim()}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "8px",
        padding: indent ? "5px 10px 5px 24px" : "6px 10px",
        borderRadius: "var(--radius-md, 6px)",
        fontSize: "13px",
        fontWeight: active ? 500 : 400,
        color: active ? "var(--text, #f5f5f7)" : "var(--text-secondary, #c7c7cc)",
        backgroundColor: active ? "rgba(255, 255, 255, 0.08)" : "transparent",
        cursor: "pointer",
        userSelect: "none",
        transition: "background 0.12s ease, color 0.12s ease",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0, overflow: "hidden" }}>
        {icon && <span style={{ flexShrink: 0, display: "grid", placeItems: "center" }}>{icon}</span>}
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "6px", flexShrink: 0 }}>
        {badge !== undefined && (
          <span
            style={{
              fontSize: "11px",
              color: active ? "var(--text-secondary, #c7c7cc)" : "var(--text-tertiary, #a1a1a6)",
              fontWeight: 400,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {badge}
          </span>
        )}
        {action}
      </div>
    </div>
  );
}
