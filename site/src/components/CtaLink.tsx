import { DOWNLOAD_MODE, GITHUB_REPO, RELEASE_URL } from "../cta";

const buttonClass =
  "inline-flex min-h-11 items-center justify-center rounded-full bg-primary px-5 text-sm font-medium text-black transition-colors duration-150 hover:bg-white";

export function CtaLink({ showNote = false }: { showNote?: boolean }) {
  if (DOWNLOAD_MODE === "release") {
    return (
      <div className="flex flex-col items-start gap-2">
        <a className={buttonClass} href={RELEASE_URL}>
          Download for macOS (Apple Silicon) · beta
        </a>
        {showNote ? (
          <p className="max-w-xs text-xs text-tertiary">
            Unsigned beta. First launch: right-click → Open.
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <div className="flex flex-col items-start gap-2">
      <a className={buttonClass} href={GITHUB_REPO}>
        View on GitHub
      </a>
      {showNote ? <p className="text-xs text-tertiary">macOS app coming soon</p> : null}
    </div>
  );
}
