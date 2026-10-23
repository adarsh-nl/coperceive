export function IllustrativeLabel({ className = "" }: { className?: string }) {
  return (
    <p className={`pointer-events-none absolute bottom-4 left-6 text-[11px] text-tertiary ${className}`}>
      Illustrative
    </p>
  );
}
