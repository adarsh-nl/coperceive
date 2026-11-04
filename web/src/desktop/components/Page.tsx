// SPDX-License-Identifier: Apache-2.0
/**
 * The v0.1.1 page pattern (docs/design/v0.1.1/App Pages.dc.html): a main list
 * or table and a right inspector for the selected row, under a one-line header
 * the shell draws from what the page publishes.
 *
 * Tables give every column a maximum width, so a wide window adds space after
 * the last column (and to the inspector), never between the columns of a row.
 */
import React, { createContext, useContext, useEffect, useRef } from "react";

// -- header ---------------------------------------------------------------------

export interface PageHeaderSpec {
  /** One meta line next to the title, e.g. "4 scenes · 72.6 s · 1.4 GB". */
  meta?: React.ReactNode;
  /** The page's primary action(s), at the top right. */
  actions?: React.ReactNode;
  /** Shown next to the page in the sidebar ("4", "1 running"); only real counts. */
  navCount?: string;
}

type PublishHeader = (page: string, spec: PageHeaderSpec | null) => void;

const PageHeaderContext = createContext<PublishHeader>(() => undefined);

export const PageHeaderProvider = PageHeaderContext.Provider;

/** Publish this page's header meta and actions to the shell while mounted. */
export function usePageHeader(page: string, spec: PageHeaderSpec): void {
  const publish = useContext(PageHeaderContext);
  useEffect(() => {
    publish(page, spec);
  });
  useEffect(() => () => publish(page, null), [page, publish]);
}

// -- layout ---------------------------------------------------------------------

export function PageLayout({
  children,
  inspector,
  inspectorLabel,
}: {
  children: React.ReactNode;
  inspector?: React.ReactNode;
  inspectorLabel?: string;
}) {
  return (
    <div className={`c-page${inspector ? " has-inspector" : ""}`}>
      <div className="c-page-main">{children}</div>
      {inspector ? (
        <aside className="c-inspector" aria-label={inspectorLabel ?? "Details"}>
          {inspector}
        </aside>
      ) : null}
    </div>
  );
}

/** A bar above a table: filters on the left, a count or note on the right. */
export function PageToolbar({ children }: { children: React.ReactNode }) {
  return <div className="c-page-toolbar">{children}</div>;
}

export function InspectorSection({
  title,
  children,
  className,
}: {
  title?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`c-inspector-section${className ? ` ${className}` : ""}`}>
      {title ? <div className="c-inspector-label">{title}</div> : null}
      {children}
    </section>
  );
}

/** Label/value pairs in two columns, like the mock's Duration / Frames grid. */
export function Facts({ items }: { items: Array<[string, React.ReactNode] | null | false | undefined> }) {
  const shown = items.filter((item): item is [string, React.ReactNode] => Boolean(item));
  if (shown.length === 0) return null;
  return (
    <dl className="c-facts">
      {shown.map(([label, value]) => (
        <div key={label} className="c-fact">
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A short horizontal bar, 0..1. */
export function MiniBar({ value, tone = "neutral", label }: { value: number; tone?: "neutral" | "ok" | "primary" | "warn" | "danger"; label?: string }) {
  const pct = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100;
  return (
    <span className={`c-minibar tone-${tone}`} role={label ? "img" : undefined} aria-label={label}>
      <span style={{ width: `${pct}%` }} />
    </span>
  );
}

// -- table ----------------------------------------------------------------------

export interface GridColumn<T> {
  key: string;
  header: React.ReactNode;
  /** Narrowest and widest the column may be, in px. Equal for a fixed column. */
  min: number;
  max: number;
  align?: "left" | "right";
  render: (row: T, index: number) => React.ReactNode;
}

export interface GridTableProps<T> {
  label: string;
  columns: GridColumn<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  selectedKey?: string | null;
  onSelect?: (row: T, index: number) => void;
  /** Enter on the selected row, or a double click. */
  onActivate?: (row: T, index: number) => void;
  rowHeight?: number;
  empty?: React.ReactNode;
  rowClassName?: (row: T) => string | undefined;
}

export function gridTemplate(columns: Array<{ min: number; max: number }>): string {
  return columns.map((column) => (column.min === column.max ? `${column.min}px` : `minmax(${column.min}px, ${column.max}px)`)).join(" ");
}

export function GridTable<T>({
  label,
  columns,
  rows,
  rowKey,
  selectedKey,
  onSelect,
  onActivate,
  rowHeight = 36,
  empty,
  rowClassName,
}: GridTableProps<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const template = gridTemplate(columns);
  const minWidth = columns.reduce((sum, column) => sum + column.min, 0) + 12 * (columns.length - 1) + 48;
  const selectedIndex = rows.findIndex((row, index) => rowKey(row, index) === selectedKey);

  const select = (index: number) => {
    const row = rows[index];
    if (row === undefined) return;
    onSelect?.(row, index);
    ref.current?.querySelector(`[data-row-index="${index}"]`)?.scrollIntoView?.({ block: "nearest" });
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (rows.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      select(Math.min(rows.length - 1, selectedIndex + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      select(Math.max(0, selectedIndex - 1));
    } else if (event.key === "Enter" && selectedIndex >= 0) {
      event.preventDefault();
      onActivate?.(rows[selectedIndex], selectedIndex);
    }
  };

  return (
    <div className="c-grid-scroll">
      <div
        ref={ref}
        role="grid"
        aria-label={label}
        aria-rowcount={rows.length + 1}
        tabIndex={0}
        onKeyDown={onKeyDown}
        className="c-grid"
        style={{ minWidth }}
      >
        <div role="row" className="c-grid-head" style={{ gridTemplateColumns: template }}>
          {columns.map((column) => (
            <span key={column.key} role="columnheader" className={column.align === "right" ? "is-right" : undefined}>
              {column.header}
            </span>
          ))}
        </div>
        {rows.length === 0 && empty ? <div className="c-grid-empty">{empty}</div> : null}
        {rows.map((row, index) => {
          const key = rowKey(row, index);
          const selected = key === selectedKey;
          return (
            <div
              key={key}
              role="row"
              aria-selected={selected}
              data-row-index={index}
              className={`c-grid-row${selected ? " is-selected" : ""}${rowClassName?.(row) ? ` ${rowClassName(row)}` : ""}`}
              style={{ gridTemplateColumns: template, minHeight: rowHeight }}
              onClick={() => onSelect?.(row, index)}
              onDoubleClick={() => onActivate?.(row, index)}
            >
              {columns.map((column) => (
                <span key={column.key} role="gridcell" className={column.align === "right" ? "is-right" : undefined}>
                  {column.render(row, index)}
                </span>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Select the first row when nothing (or something no longer listed) is selected. */
export function useDefaultSelection<T>(
  rows: T[],
  rowKey: (row: T, index: number) => string,
  selectedKey: string | null,
  setSelectedKey: (key: string | null) => void,
): void {
  useEffect(() => {
    if (rows.length === 0) {
      if (selectedKey !== null) setSelectedKey(null);
      return;
    }
    if (selectedKey === null || !rows.some((row, index) => rowKey(row, index) === selectedKey)) {
      setSelectedKey(rowKey(rows[0], 0));
    }
  }, [rows, rowKey, selectedKey, setSelectedKey]);
}
