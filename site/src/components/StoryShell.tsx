import type { ReactNode } from "react";
import { useStory } from "../motion/useStory";

export function StoryShell({
  id,
  title,
  lede,
  keyProgress,
  children,
}: {
  id: string;
  title: string;
  lede?: string;
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
        <div className="mx-auto w-full max-w-6xl px-6 pb-2 pt-16 sm:px-10">
          <h2 className="max-w-3xl text-4xl font-semibold tracking-tight text-primary sm:text-5xl">{title}</h2>
          {lede ? <p className="mt-3 max-w-xl text-base text-secondary sm:text-lg">{lede}</p> : null}
        </div>
        {children(story)}
      </div>
    </section>
  );
}
