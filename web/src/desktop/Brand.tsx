// SPDX-License-Identifier: Apache-2.0
export const BRAND_CURSOR = "#D9482B";

export interface BrandWordmarkProps {
  /** When true, the block cursor blinks on a 1s step cadence (Connect screen). */
  blink?: boolean;
  /** Size override if not inherited. */
  fontSize?: string;
  className?: string;
}

export function BrandWordmark({ blink = false, fontSize, className }: BrandWordmarkProps) {
  return (
    <span
      className={`brand-wordmark ${blink ? "brand-cursor-blink" : ""} ${className || ""}`.trim()}
      style={fontSize ? { fontSize } : undefined}
    >
      <span className="brand-text">coperceive</span>
      <span className="brand-cursor" aria-hidden="true" />
    </span>
  );
}
