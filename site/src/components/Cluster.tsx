import { Reveal } from "../motion/Reveal";

export function Cluster() {
  return (
    <section id="cluster" data-pin="false" data-story="cluster" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <Reveal>
          <span className="inline-flex rounded-full border border-hairline-bright px-3 py-1 text-xs text-secondary">
            Coming soon
          </span>
          <h2 className="mt-6 max-w-[680px] text-4xl font-semibold tracking-tight sm:text-5xl">
            Evaluate on your own cluster.
          </h2>
          <p className="mt-4 max-w-[680px] text-lg text-secondary">
            Connect over SSH and run evaluations next to your data.
          </p>
        </Reveal>
      </div>
    </section>
  );
}
