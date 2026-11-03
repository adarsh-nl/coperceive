// SPDX-License-Identifier: Apache-2.0
import React, { useRef } from "react";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  width?: string;
  align?: "left" | "center" | "right";
  render?: (item: T, index: number) => React.ReactNode;
}

export interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  keyExtractor: (item: T, index: number) => string;
  selectedIndex?: number;
  onSelectIndex?: (index: number) => void;
  onRowClick?: (item: T, index: number) => void;
  onRowDoubleClick?: (item: T, index: number) => void;
  emptyState?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export function Table<T>({
  columns,
  data,
  keyExtractor,
  selectedIndex,
  onSelectIndex,
  onRowClick,
  onRowDoubleClick,
  emptyState,
  className = "",
  style,
}: TableProps<T>) {
  const containerRef = useRef<HTMLDivElement>(null);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (data.length === 0 || selectedIndex === undefined) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.min(data.length - 1, selectedIndex + 1);
      onSelectIndex?.(next);
      ensureRowVisible(next);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = Math.max(0, selectedIndex - 1);
      onSelectIndex?.(prev);
      ensureRowVisible(prev);
    } else if (e.key === "Enter" || e.key === " ") {
      if (selectedIndex >= 0 && selectedIndex < data.length) {
        e.preventDefault();
        onRowClick?.(data[selectedIndex], selectedIndex);
      }
    }
  };

  const ensureRowVisible = (index: number) => {
    const row = containerRef.current?.querySelector(`[data-row-index="${index}"]`);
    if (row && typeof (row as HTMLElement).scrollIntoView === "function") {
      (row as HTMLElement).scrollIntoView({ block: "nearest" });
    }
  };

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className={`c-table-container ${className}`.trim()}
      style={{
        width: "100%",
        overflow: "auto",
        position: "relative",
        outline: "none",
        ...style,
      }}
    >
      <table
        className="c-table"
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: "12.5px",
          textAlign: "left",
        }}
      >
        <thead>
          <tr
            style={{
              position: "sticky",
              top: 0,
              zIndex: 2,
              background: "var(--panel, #1a1a1c)",
              borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
              height: "30px",
              fontSize: "11px",
              color: "var(--text-subtle, #6e6e73)",
              fontWeight: 500,
            }}
          >
            {columns.map((col) => (
              <th
                key={col.key}
                style={{
                  padding: "0 12px",
                  width: col.width,
                  textAlign: col.align || "left",
                  whiteSpace: "nowrap",
                }}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.length === 0 ? (
            <tr>
              <td colSpan={columns.length} style={{ padding: "32px 16px", textAlign: "center" }}>
                {emptyState || <span style={{ color: "var(--text-subtle, #6e6e73)" }}>No items</span>}
              </td>
            </tr>
          ) : (
            data.map((item, index) => {
              const isSelected = selectedIndex === index;
              return (
                <tr
                  key={keyExtractor(item, index)}
                  data-row-index={index}
                  onClick={() => {
                    onSelectIndex?.(index);
                    onRowClick?.(item, index);
                  }}
                  onDoubleClick={() => onRowDoubleClick?.(item, index)}
                  className={`c-table-row ${isSelected ? "selected" : ""}`}
                  style={{
                    height: "34px",
                    borderBottom: "1px solid var(--line-subtle, rgba(255,255,255,0.04))",
                    cursor: "pointer",
                    backgroundColor: isSelected
                      ? "rgba(31, 111, 235, 0.16)"
                      : "transparent",
                    transition: "background 0.1s ease",
                  }}
                >
                  {columns.map((col) => {
                    const value = col.render
                      ? col.render(item, index)
                      : (item as Record<string, unknown>)[col.key] as React.ReactNode;
                    return (
                      <td
                        key={col.key}
                        style={{
                          padding: "0 12px",
                          textAlign: col.align || "left",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {value}
                      </td>
                    );
                  })}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
