export function IllustrativeLabel({ className = "" }: { className?: string }) {
  return (
    <p
      className={`pointer-events-none absolute left-4 top-4 z-10 rounded-full border border-hairline bg-glass px-3 py-1 text-[11px] text-secondary backdrop-blur-22 ${className}`}
    >
      Illustrative scene
    </p>
  );
}
