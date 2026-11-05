// SPDX-License-Identifier: Apache-2.0
import type { StartupCrash } from "./local";

export function CrashNotice({
  crash,
  onShare,
  onDismiss,
}: {
  crash: StartupCrash;
  onShare: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="crash-notice" role="status" data-crash-kind={crash.kind} data-crash-version={crash.version}>
      <span>Coperceive closed unexpectedly last time.</span>
      <button type="button" className="link-button" onClick={onShare}>
        Share diagnostics…
      </button>
      <span aria-hidden="true">·</span>
      <button type="button" className="link-button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
