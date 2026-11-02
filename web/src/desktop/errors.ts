// SPDX-License-Identifier: Apache-2.0
/**
 * Error text from a desktop backend call. Tauri rejects `invoke` with the
 * command's error string, not an `Error`, so `err instanceof Error` alone
 * drops the backend's message; the controller and `fetch` throw `Error`s.
 */

/** The whole message: a string, an Error's message, or an object's `message`/`error`. */
export function errorDetails(error: unknown): string {
  if (typeof error === "string") return error.trim();
  if (error instanceof Error) return error.message.trim();
  if (error && typeof error === "object") {
    const record = error as { message?: unknown; error?: unknown };
    if (typeof record.message === "string") return record.message.trim();
    if (typeof record.error === "string") return record.error.trim();
  }
  return "";
}

/** The first non-empty line of the message, in plain words; `fallback` when there is none. */
export function errorText(error: unknown, fallback = "Something went wrong. Try again."): string {
  const first = errorDetails(error)
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean);
  if (!first) return fallback;
  return first.replace(/^(?:Error|TypeError):\s*/, "");
}

/** True when the message has more than its first line (worth a "Details" toggle). */
export function hasMoreDetails(error: unknown): boolean {
  return errorDetails(error).split("\n").filter((line) => line.trim()).length > 1;
}
