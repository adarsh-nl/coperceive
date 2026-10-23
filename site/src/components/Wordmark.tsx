/** Light-on-dark wordmark. Lowercase Geist Mono, then a short baseline block. */
export function Wordmark({ blink = false }: { blink?: boolean }) {
  return (
    <span translate="no" className="inline-flex items-baseline font-mono text-[1.05rem] font-medium tracking-tight text-primary">
      coperceive
      <span
        aria-hidden="true"
        className={`ml-[0.14em] inline-block h-[0.18em] w-[0.72em] translate-y-[0.06em] bg-cursor ${blink ? "cursor-blink" : ""}`}
      />
    </span>
  );
}
