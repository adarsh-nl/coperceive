// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";

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
}: {
  type?: "checkbox" | "radio";
  name?: string;
  value?: string;
  checked: boolean;
  disabled?: boolean;
  title: ReactNode;
  description?: ReactNode;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="setting-row" data-disabled={disabled ? "true" : undefined}>
      <input
        type={type}
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="setting-text">
        <span className="setting-title">{title}</span>
        {description ? <span className="setting-description">{description}</span> : null}
      </span>
    </label>
  );
}
