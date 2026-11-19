// SPDX-License-Identifier: Apache-2.0
import React, { useEffect } from "react";
import { BrandWordmark } from "../Brand";
import { IconButton } from "./IconButton";
import { isTitlebarOverlay, getModifierKey } from "../platform";

export interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  headerRight?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export function Sidebar({
  collapsed,
  onToggle,
  headerRight,
  children,
  footer,
  className = "",
  style,
}: SidebarProps) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // ⌘\ on Mac or Ctrl+\ on Windows/Linux
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault();
        onToggle();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onToggle]);

  const isOverlay = isTitlebarOverlay();
  const modKey = getModifierKey();

  return (
    <aside
      className={`c-sidebar ${collapsed ? "collapsed" : ""} ${className}`.trim()}
      style={{
        display: "flex",
        flexDirection: "column",
        width: collapsed ? "0px" : "236px",
        minWidth: collapsed ? "0px" : "236px",
        maxWidth: collapsed ? "0px" : "236px",
        height: "100%",
        backgroundColor: "var(--sidebar-bg, #1a1a1c)",
        borderRight: collapsed ? "none" : "1px solid var(--line, rgba(255,255,255,0.08))",
        overflow: "hidden",
        transition: "width 0.2s cubic-bezier(0.16, 1, 0.3, 1), min-width 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
        userSelect: "none",
        position: "relative",
        zIndex: 5,
        ...style,
      }}
    >
      {/* Top Header with macOS traffic lights spacing (or flush 16px on Win/Linux) and brand wordmark */}
      <div
        data-tauri-drag-region
        style={{
          height: "52px",
          display: "flex",
          alignItems: "center",
          gap: "0",
          padding: isOverlay ? "0 14px 0 0" : "0 14px 0 16px",
          flexShrink: 0,
          borderBottom: "1px solid var(--line-subtle, rgba(255,255,255,0.04))",
        }}
      >
        {/* macOS traffic lights safe area (width 78px starting at x=0 to ensure zero overlap) */}
        {isOverlay && (
          <div
            data-tauri-drag-region
            className="traffic-lights-spacer"
            style={{ width: "78px", flexShrink: 0, height: "100%" }}
            aria-hidden="true"
          />
        )}

        {/* Brand wordmark to the right of traffic lights */}
        <div
          data-tauri-drag-region
          style={{
            display: "flex",
            alignItems: "center",
            fontSize: "13px",
            minWidth: 0,
            overflow: "hidden",
          }}
        >
          <BrandWordmark fontSize="13px" />
        </div>

        {/* Right controls: optional headerRight + Toggle sidebar button */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "4px", flexShrink: 0 }}>
          {headerRight}
          <IconButton
            size="sm"
            label={`Toggle sidebar (${modKey}\\)`}
            onClick={onToggle}
            data-tauri-drag-region="false"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
              <path d="M1.5 2.5A1 1 0 0 1 2.5 1.5h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11zm1 0v11h3.5v-11h-3.5zm4.5 0v11h6.5v-11H7z" />
            </svg>
          </IconButton>
        </div>
      </div>

      {/* Main navigation / content */}
      <div
        className="c-sidebar-content"
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          padding: "8px 10px",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        {children}
      </div>

      {/* Optional bottom footer */}
      {footer && (
        <div
          className="c-sidebar-footer"
          style={{
            marginTop: "auto",
            borderTop: "1px solid var(--line, rgba(255,255,255,0.08))",
            padding: "12px 14px",
            fontSize: "11.5px",
            flexShrink: 0,
          }}
        >
          {footer}
        </div>
      )}
    </aside>
  );
}
