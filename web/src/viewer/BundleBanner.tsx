// SPDX-License-Identifier: Apache-2.0
/** Read-only banner for a result bundle opened in the viewer. */
import { useState } from "react";

export type BundleSceneInfo = {
  sensor_data: "included" | "local-copy" | "omitted";
  dataset?: string | null;
  licence?: string | null;
  label?: string | null;
  note?: string | null;
  local_copy?: string | null;
  title?: string | null;
  run_id?: string | null;
  rerun_command?: string | null;
};

export default function BundleBanner({ bundle }: { bundle: BundleSceneInfo }) {
  const [copied, setCopied] = useState(false);
  const dataset = bundle.dataset ?? "this dataset";
  const state =
    bundle.sensor_data === "included"
      ? `Sensor data included (${bundle.licence ?? "recorded licence"}).`
      : bundle.sensor_data === "local-copy"
        ? `Frames from your local copy of ${dataset}.`
        : `Frames not included — ${bundle.note ?? "no sensor data in this bundle"}`;
  return (
    <div
      role="status"
      data-testid="bundle-banner"
      style={{
        margin: "0.5rem",
        padding: "0.5rem 0.75rem",
        border: "1px solid var(--line)",
        borderRadius: "6px",
        background: "var(--surface-raised)",
        whiteSpace: "normal",
        height: "auto",
        overflow: "visible",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: "0.2rem" }}>
        <strong>From a shared bundle: {bundle.title ?? bundle.run_id ?? "results"}</strong>
        <span>{state}</span>
        {bundle.licence ? <span>Licence: {bundle.licence}</span> : null}
        {bundle.rerun_command ? (
          <code style={{ fontSize: "0.75rem", wordBreak: "break-all" }}>{bundle.rerun_command}</code>
        ) : null}
      </div>
      {bundle.rerun_command ? (
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(bundle.rerun_command ?? "").then(() => setCopied(true));
          }}
        >
          {copied ? "Copied" : "Copy rerun"}
        </button>
      ) : null}
    </div>
  );
}
