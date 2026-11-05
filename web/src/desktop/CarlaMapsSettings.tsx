// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { Button } from "./components/Button";
import {
  installCarlaMap,
  listCarlaMaps,
  onCarlaMapProgress,
  removeCarlaMap,
  type CarlaMapTown,
} from "./local";

function size(bytes: number): string {
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export function CarlaMapsSettings() {
  const [towns, setTowns] = useState<CarlaMapTown[]>([]);
  const [busy, setBusy] = useState<string>("");
  const [progress, setProgress] = useState<string>("");
  const [error, setError] = useState<string>("");

  const refresh = async () => {
    const list = await listCarlaMaps();
    setTowns(list?.towns ?? []);
  };

  useEffect(() => {
    void refresh();
    let stop: (() => void) | undefined;
    void onCarlaMapProgress((event) => {
      if (event.message) setProgress(event.message);
    }).then((unlisten) => {
      stop = unlisten;
    });
    return () => stop?.();
  }, []);

  const install = async (town: string) => {
    setBusy(town);
    setError("");
    setProgress(`Downloading ${town} (${size(towns.find((item) => item.town === town)?.download_bytes ?? 0)}) from CARLA's pinned mirror…`);
    try {
      await installCarlaMap(town);
      setProgress(`${town} installed and aligned.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const remove = async (town: string) => {
    setBusy(town);
    setError("");
    try {
      await removeCarlaMap(town);
      setProgress(`${town} removed.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  return (
    <section aria-label="CARLA road maps" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
        Road maps come from CARLA 0.9.15 assets (CC-BY; the licence does not state a version).
        Files are downloaded from a pinned mirror only when their SHA-256 matches ADR 0008.
        They are cached under the library's maps folder, never in the dataset.
      </p>
      {error ? <p role="alert" className="alert">{error}</p> : null}
      {progress ? <p className="muted" role="status" style={{ margin: 0, fontSize: "12px" }}>{progress}</p> : null}
      {towns.map((item) => (
        <div key={item.town} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "6px 0", borderTop: "1px solid var(--line)" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "13px", fontWeight: 500 }}>{item.town}</div>
            <div className="muted" style={{ fontSize: "11px" }}>
              {item.installed
                ? `Installed · ${size(item.installed_info?.bytes ?? 0)}`
                : `${size(item.download_bytes)} download · CC-BY · ${item.verification_label}`}
            </div>
          </div>
          {item.installed ? (
            <Button variant="danger" size="sm" disabled={busy === item.town} onClick={() => void remove(item.town)}>Remove</Button>
          ) : (
            <Button variant="secondary" size="sm" disabled={Boolean(busy)} onClick={() => void install(item.town)}>
              {busy === item.town ? "Installing…" : "Install"}
            </Button>
          )}
        </div>
      ))}
      <p className="muted" style={{ margin: 0, fontSize: "11px" }}>
        Town10HD is included. Culver City is a digital twin, not a stock CARLA town, so it has no downloadable map.
        If a town cannot be mirrored, use “coperceive maps fetch” with your own CARLA 0.9.15 package.
      </p>
    </section>
  );
}
