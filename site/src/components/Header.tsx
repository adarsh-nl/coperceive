import { GITHUB_REPO } from "../cta";
import { CtaLink } from "./CtaLink";

export function Header() {
  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-hairline bg-glass backdrop-blur-22 pt-[env(safe-area-inset-top)]">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-[max(1.25rem,env(safe-area-inset-left))] pr-[max(1.25rem,env(safe-area-inset-right))]">
        <a href="#top" className="text-sm font-medium tracking-tight text-primary" translate="no">
          coperceive
        </a>
        <div className="flex items-center gap-2 sm:gap-4">
          <a
            href={GITHUB_REPO}
            className="inline-flex min-h-11 items-center px-2 text-sm text-secondary transition-colors duration-150 hover:text-primary"
          >
            GitHub
          </a>
          <CtaLink />
        </div>
      </div>
    </header>
  );
}
