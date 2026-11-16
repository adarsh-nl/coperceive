// SPDX-License-Identifier: Apache-2.0
/** Record window-level errors and console.error calls for diagnostics. */

export interface RecordedUiError {
  message: string;
  stack?: string;
}

const MAX_CONSOLE_ERRORS = 50;

function appendConsoleError(args: unknown[]): void {
  const formatted = args
    .map((item) => {
      if (item instanceof Error) return item.stack || item.message;
      if (typeof item === "string") return item;
      try {
        return JSON.stringify(item);
      } catch {
        return String(item);
      }
    })
    .join(" ");
  const host = window as unknown as { __COPERCEIVE_CONSOLE_ERRORS__?: string[] };
  if (!Array.isArray(host.__COPERCEIVE_CONSOLE_ERRORS__)) {
    host.__COPERCEIVE_CONSOLE_ERRORS__ = [];
  }
  host.__COPERCEIVE_CONSOLE_ERRORS__.push(formatted);
  if (host.__COPERCEIVE_CONSOLE_ERRORS__.length > MAX_CONSOLE_ERRORS) {
    host.__COPERCEIVE_CONSOLE_ERRORS__.shift();
  }
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
  const host = window as unknown as { __COPERCEIVE_CONSOLE_ERRORS__?: string[] };
  if (!Array.isArray(host.__COPERCEIVE_CONSOLE_ERRORS__)) {
    host.__COPERCEIVE_CONSOLE_ERRORS__ = [];
  }
  const origConsoleError = console.error;
  console.error = (...args: unknown[]) => {
    appendConsoleError(args);
    origConsoleError.apply(console, args);
  };
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
    if (console.error) {
      console.error = origConsoleError;
    }
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
