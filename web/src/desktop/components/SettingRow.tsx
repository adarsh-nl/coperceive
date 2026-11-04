// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";

export interface SettingRowProps {
  type?: "checkbox" | "radio";
  name?: string;
  value?: string;
  checked: boolean;
  disabled?: boolean;
  title: ReactNode;
  description?: ReactNode;
  onChange: (checked: boolean) => void;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * One setting: a checkbox or radio on the left, aligned with the first line of
 * the title; the title left-aligned beside it and the description under it.
 * Every setting in the app uses this row.
 */
export function SettingRow({
  type = "checkbox",
  name,
  value,
  checked,
  disabled,
  title,
  description,
  onChange,
  className = "",
  style,
}: SettingRowProps) {
  return (
    <label
      className={`c-setting-row setting-row ${className}`.trim()}
      data-disabled={disabled ? "true" : undefined}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "10px",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
        padding: "6px 0",
        userSelect: "none",
        ...style,
      }}
    >
      <input
        type={type}
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        style={{
          width: "14px",
          height: "14px",
          minWidth: "14px",
          minHeight: "14px",
          maxWidth: "14px",
          maxHeight: "14px",
          margin: "2px 0 0 0",
          padding: 0,
          flexShrink: 0,
          cursor: disabled ? "not-allowed" : "pointer",
          accentColor: "var(--primary, #1f6feb)",
        }}
      />
      <span
        className="setting-text"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "2px",
          minWidth: 0,
        }}
      >
        <span
          className="setting-title"
          style={{
            fontSize: "13px",
            fontWeight: 500,
            color: "var(--text, #f5f5f7)",
          }}
        >
          {title}
        </span>
        {description ? (
          <span
            className="setting-description"
            style={{
              fontSize: "12px",
              color: "var(--text-tertiary, #a1a1a6)",
              lineHeight: 1.4,
            }}
          >
            {description}
          </span>
        ) : null}
      </span>
    </label>
  );
}
