import type { ReactNode } from "react";
import { useStory } from "../motion/useStory";

export function StoryShell({
  id,
  kicker,
  title,
  lede,
  keyProgress,
  children,
}: {
  id: string;
  kicker: string;
  title: string;
  lede: string;
  keyProgress: number;
  children: (story: ReturnType<typeof useStory>) => ReactNode;
}) {
  const story = useStory(keyProgress);
  return (
    <section
      id={id}
      ref={story.sectionRef}
      data-pin={story.pin ? "true" : "false"}
      data-story={id}
      className={story.pin ? "relative h-[200vh]" : "relative border-t border-hairline"}
    >
      <div
        data-sticky={story.pin ? "true" : "false"}
        className={
          story.pin
            ? "sticky top-0 flex h-[100svh] flex-col"
            : "flex min-h-[100svh] flex-col"
        }
      >
        <div className="mx-auto w-full max-w-6xl px-6 pb-4 pt-20 sm:px-10">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-tertiary">{kicker}</p>
          <h2 className="mt-3 max-w-3xl text-4xl font-semibold tracking-tight text-primary sm:text-5xl">
            {title}
          </h2>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-secondary sm:text-lg">{lede}</p>
        </div>
        {children(story)}
      </div>
    </section>
  );
}
