// SPDX-License-Identifier: Apache-2.0
import React from "react";

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  size?: "sm" | "md" | "lg";
  label: string;
  children: React.ReactNode;
}

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { size = "md", label, children, className = "", style, disabled, ...rest },
  ref,
) {
  const sizeMap: Record<"sm" | "md" | "lg", { dim: string; font: string; radius: string }> = {
    sm: { dim: "24px", font: "12px", radius: "var(--radius-sm, 4px)" },
    md: { dim: "30px", font: "14px", radius: "var(--radius-md, 6px)" },
    lg: { dim: "36px", font: "16px", radius: "var(--radius-lg, 8px)" },
  };

  const currentSize = sizeMap[size];

  return (
    <button
      ref={ref}
      aria-label={label}
      title={label}
      disabled={disabled}
      className={`c-icon-button ${className}`.trim()}
      style={{
        display: "inline-grid",
        placeItems: "center",
        width: currentSize.dim,
        height: currentSize.dim,
        fontSize: currentSize.font,
        borderRadius: currentSize.radius,
        border: "none",
        background: "transparent",
        color: "inherit",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.4 : 1,
        transition: "background 0.15s ease, color 0.15s ease",
        padding: 0,
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
});
