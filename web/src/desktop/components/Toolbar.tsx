// SPDX-License-Identifier: Apache-2.0
import React from "react";

export interface ToolbarProps {
  left?: React.ReactNode;
  center?: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export function Toolbar({ left, center, right, className = "", style }: ToolbarProps) {
  return (
    <header
      data-tauri-drag-region
      className={`c-toolbar ${className}`.trim()}
      style={{
        height: "52px",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "16px",
        padding: "0 20px",
        borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
        backgroundColor: "var(--panel, #1a1a1c)",
        userSelect: "none",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", minWidth: 0 }}>
        {left}
      </div>
      {center && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minWidth: 0 }}>
          {center}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginLeft: "auto", minWidth: 0 }}>
        {right}
      </div>
    </header>
  );
}
