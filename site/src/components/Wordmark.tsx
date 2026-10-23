/** Light-on-dark wordmark. Lowercase Geist Mono, then a short baseline block. */
export function Wordmark({ blink = false }: { blink?: boolean }) {
  return (
    <span
      translate="no"
      className="inline-flex items-baseline font-mono text-[1.75rem] font-medium leading-none tracking-tight text-primary drop-shadow-[0_1px_10px_rgba(0,0,0,0.95)] sm:text-[2rem]"
    >
      coperceive
      <span
        aria-hidden="true"
        className={`ml-[0.14em] inline-block h-[0.28em] min-h-2 w-[0.62em] min-w-[1.1rem] translate-y-[0.08em] rounded-[2px] bg-cursor shadow-[0_0_0_1px_rgba(0,0,0,0.45)] ${blink ? "cursor-blink" : ""}`}
      />
    </span>
  );
}
