// SPDX-License-Identifier: Apache-2.0
/** Record window-level errors so the diagnostics context can include them. */

export interface RecordedUiError {
  message: string;
  stack?: string;
}

export function recordGlobalError(value: RecordedUiError): void {
  (window as unknown as { __COPERCEIVE_LAST_ERROR__?: RecordedUiError }).__COPERCEIVE_LAST_ERROR__ = value;
  try {
    localStorage.setItem("coperceive-last-ui-error", JSON.stringify(value));
  } catch {
    // Storage can be unavailable; the in-memory record still works.
  }
}

export function installGlobalErrorRecorders(): () => void {
  const onError = (event: ErrorEvent) => {
    recordGlobalError({
      message: event.message,
      stack: event.error instanceof Error ? event.error.stack : undefined,
    });
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    recordGlobalError({ message: String(event.reason) });
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
