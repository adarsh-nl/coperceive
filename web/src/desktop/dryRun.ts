// SPDX-License-Identifier: Apache-2.0
/** Turn a Griffin dry-run failure into the card's cause + one next step. */

function lastLine(text: string): string {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines[lines.length - 1] ?? "unknown error";
}

export function dryRunFailureText(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  const line = lastLine(raw);
  if (/Hugging Face client|huggingface_hub|No module named/i.test(line)) {
    return "The app build is missing the Hugging Face client. Next: update the desktop app, then try again.";
  }
  if (/Next:/i.test(line)) {
    return line;
  }
  if (/401|403|unauthori[sz]ed|forbidden|token|gated/i.test(line)) {
    return `Could not check the size: ${line}. Next: check HF_TOKEN access to the dataset, then try again.`;
  }
  if (/network|VPN|timed? ?out|timeout|connect|resolve|SSL|offline/i.test(line)) {
    return `Could not check the size: ${line}. Next: check the network or VPN, then try again.`;
  }
  return `Could not check the size: ${line}. Next: choose this preset to try again.`;
}
