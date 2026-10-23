const CARDS: { id: string; title: string; body: string }[] = [
  { id: "spec-play", title: "Play", body: "Several viewpoints of one scene." },
  { id: "spec-check", title: "Check", body: "A written report for a folder of scenes." },
  { id: "spec-compare", title: "Compare", body: "A prediction next to ground truth." },
  { id: "spec-map", title: "Map", body: "Roads drawn with the sensors." },
  { id: "spec-export", title: "Export", body: "A picture or a clip." },
  { id: "spec-desktop", title: "Desktop", body: "Specified, and not a download." },
];

export function Specs() {
  return (
    <section id="tools" data-pin="false" data-story="tools" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <h2 className="text-4xl font-semibold tracking-tight sm:text-5xl">Tools.</h2>
        <div className="mt-10 border-t border-hairline">
          {CARDS.map((card) => (
            <details key={card.id} id={card.id} className="group border-b border-hairline">
              <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-4 py-3 text-lg text-primary transition-colors duration-150 hover:text-white [&::-webkit-details-marker]:hidden">
                {card.title}
                <span
                  aria-hidden="true"
                  className="text-tertiary transition-transform duration-150 motion-reduce:transition-none group-open:rotate-45"
                >
                  +
                </span>
              </summary>
              <p className="max-w-xl pb-6 text-sm text-secondary">{card.body}</p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
