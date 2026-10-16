import { useEffect, useRef, useState, type ReactNode } from "react";

export type MenuOption = { value: string; label: string };

export function Menu({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label?: string;
  value: string;
  options: readonly MenuOption[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointer(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const current = options.find((option) => option.value === value);
  return (
    <div className="menu" ref={root}>
      {label ? (
        <span className="field-label" id={`${id}-label`}>
          {label}
        </span>
      ) : null}
      <button
        type="button"
        className="menu-button"
        id={id}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby={label ? `${id}-label` : undefined}
        onClick={() => setOpen((currentOpen) => !currentOpen)}
      >
        <span>{current?.label ?? "Choose"}</span>
        <Caret />
      </button>
      {open ? (
        <ul className="menu-list" role="listbox" aria-labelledby={id}>
          {options.map((option) => (
            <li key={option.value}>
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function EyeToggle({
  on,
  label,
  onChange,
}: {
  on: boolean;
  label: string;
  onChange: () => void;
}) {
  return (
    <button type="button" className="eye" aria-pressed={on} onClick={onChange}>
      <EyeIcon open={on} />
      <span>{label}</span>
    </button>
  );
}

export function SwatchToggle({
  on,
  label,
  color,
  meta,
  onChange,
}: {
  on: boolean;
  label: string;
  color: string;
  meta?: ReactNode;
  onChange: () => void;
}) {
  return (
    <button type="button" className={on ? "swatch on" : "swatch"} aria-pressed={on} onClick={onChange}>
      <i style={{ background: color }} aria-hidden="true" />
      <span>{label}</span>
      {meta}
    </button>
  );
}

export function Segmented({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly MenuOption[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="seg-field">
      <span className="field-label">{label}</span>
      <div className="seg" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function IconPlay({ playing }: { playing: boolean }) {
  if (playing) {
    return (
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path fill="currentColor" d="M4 3h3v10H4zM9 3h3v10H9z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path fill="currentColor" d="M5 3.2v9.6L13 8z" />
    </svg>
  );
}

export function IconStep({ direction }: { direction: -1 | 1 }) {
  const path = direction < 0 ? "M10 3L4 8l6 5V3zM11 3h2v10h-2z" : "M6 3l6 5-6 5V3zM3 3h2v10H3z";
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path fill="currentColor" d={path} />
    </svg>
  );
}

export function IconLoop() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        fill="currentColor"
        d="M3.2 6.2A4.5 4.5 0 0 1 11 4h1.2L10.6 5.6 11.8 6.8 15 3.6 11.8.4 10.6 1.6 12.2 3H11a5.7 5.7 0 0 0-5.5 4.1l1.2.4A4.5 4.5 0 0 1 3.2 6.2zM12.8 9.8A4.5 4.5 0 0 1 5 12H3.8l1.6-1.6L4.2 9.2 1 12.4l3.2 3.2 1.2-1.2L3.8 13H5a5.7 5.7 0 0 0 5.5-4.1l-1.2-.4a4.5 4.5 0 0 1 3.5 1.3z"
      />
    </svg>
  );
}

function Caret() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path fill="currentColor" d="M2.2 4.2 6 8l3.8-3.8-1-1L6 6 3.2 3.2z" />
    </svg>
  );
}

function EyeIcon({ open }: { open: boolean }) {
  if (!open) {
    return (
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          d="M2 2l12 12M3.2 6.2C2.4 7 2 8 2 8s2.2 4 6 4c1 0 1.9-.2 2.6-.6M6.1 4.3C6.7 4.1 7.3 4 8 4c3.8 0 6 4 6 4s-.4.8-1.2 1.6"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        d="M1.5 8S4 4 8 4s6.5 4 6.5 4-2.5 4-6.5 4S1.5 8 1.5 8z"
      />
      <circle cx="8" cy="8" r="2" fill="currentColor" />
    </svg>
  );
}
