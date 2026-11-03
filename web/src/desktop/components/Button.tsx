// SPDX-License-Identifier: Apache-2.0
import React from "react";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: React.ReactNode;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className = "", children, style, disabled, ...rest },
  ref,
) {
  const baseStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontFamily: "inherit",
    fontWeight: 500,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "all 0.15s ease",
    whiteSpace: "nowrap",
    userSelect: "none",
    textDecoration: "none",
    ...style,
  };

  const sizeStyles: Record<ButtonSize, React.CSSProperties> = {
    sm: {
      padding: "3px 10px",
      fontSize: "12px",
      borderRadius: "var(--radius-sm, 5px)",
      height: "26px",
    },
    md: {
      padding: "6px 14px",
      fontSize: "13px",
      borderRadius: "var(--radius-md, 6px)",
      height: "32px",
    },
    lg: {
      padding: "8px 18px",
      fontSize: "14px",
      borderRadius: "var(--radius-lg, 8px)",
      height: "38px",
    },
  };

  const variantClass = `btn-${variant}`;
  const sizeClass = `btn-${size}`;

  return (
    <button
      ref={ref}
      disabled={disabled}
      className={`c-button ${variantClass} ${sizeClass} ${className}`.trim()}
      style={{ ...baseStyle, ...sizeStyles[size] }}
      {...rest}
    >
      {children}
    </button>
  );
});
