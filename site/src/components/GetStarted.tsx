import { useState } from "react";
import { GITHUB_REPO } from "../cta";
import { CtaLink } from "./CtaLink";

const INSTALL = `python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin]"
coperceive --help`;

const NEXT = `coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes
coperceive serve --root /path/to/a/workdir --maps /path/to/maps`;

export function GetStarted() {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    const text = `${INSTALL}\n${NEXT}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <section id="start" data-pin="false" data-story="start" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-tertiary">Checkout</p>
        <h2 className="mt-3 text-4xl font-semibold tracking-tight sm:text-5xl">Get started.</h2>
        <p className="mt-4 max-w-2xl text-lg text-secondary">
          <span translate="no">coperceive</span> is not published on PyPI. Install from a checkout,
          the way the README shows. Python 3.10–3.13.
        </p>
        <div className="mt-8 flex flex-wrap items-start gap-6">
          <CtaLink showNote />
          <a
            href={GITHUB_REPO}
            className="inline-flex min-h-11 items-center text-sm text-secondary transition-colors duration-150 hover:text-primary"
          >
            github.com/adarsh-nl/coperceive
          </a>
        </div>
        <div className="mt-10 overflow-hidden rounded-3xl border border-hairline">
          <div className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-3">
            <p className="text-xs text-tertiary">From the README</p>
            <button
              type="button"
              className="inline-flex min-h-11 items-center rounded-full px-3 text-sm text-primary transition-colors duration-150 hover:bg-white/5"
              onClick={() => void copy()}
            >
              Copy install commands
            </button>
          </div>
          <pre translate="no" className="overflow-x-auto p-5 font-mono text-[13px] leading-relaxed text-primary">
            <code>{`${INSTALL}\n\n${NEXT}`}</code>
          </pre>
        </div>
        <p className="mt-4 text-sm text-tertiary">
          A download that writes files also needs --accept-griffin-terms.
        </p>
        <p className="text-sm text-primary" aria-live="polite">
          {copied === "copied"
            ? "Copied."
            : copied === "failed"
              ? "Clipboard blocked. Select the commands and copy them."
              : ""}
        </p>
      </div>
    </section>
  );
}
