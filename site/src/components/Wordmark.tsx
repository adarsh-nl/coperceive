/** Light-on-dark wordmark. Lowercase Geist Mono, then a short baseline block. */
export function Wordmark({ blink = false, size = "hero" }: { blink?: boolean; size?: "hero" | "small" }) {
  const type = size === "small" ? "text-[1.05rem]" : "text-[1.75rem] sm:text-[2rem]";
  const block =
    size === "small"
      ? "h-[0.62em] min-h-[10px] w-[0.5em] min-w-[10px]"
      : "h-[0.72em] min-h-[18px] w-[0.55em] min-w-[16px]";
  return (
    <span
      translate="no"
      className={`inline-flex items-baseline font-mono font-medium leading-none tracking-tight text-primary drop-shadow-[0_1px_10px_rgba(0,0,0,0.95)] ${type}`}
    >
      coperceive
      <span
        aria-hidden="true"
        className={`ml-[0.14em] inline-block translate-y-[0.06em] rounded-[2px] bg-cursor ${block} ${blink ? "cursor-blink" : ""}`}
      />
    </span>
  );
}
