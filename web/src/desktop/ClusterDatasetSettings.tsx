// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";

import {
  cameraImageChoices,
  datasetPreviews,
  formatBytes,
  getCameraImagesSetting,
  setCameraImagesSetting,
  type CameraImagesMode,
  type CameraImagesSetting,
  type CameraPreviewsUsage,
} from "./clusterDatasets";
import { errorText } from "./errors";
import type { ServerEntry } from "./hpc";
import { listServerProfiles } from "./local";
import { SettingRow } from "./SettingRow";

const ALL = "";

/**
 * Settings → Cluster datasets: what the next prepare preview starts with for
 * camera images, per server (or for every server), and the disk the saved
 * previews use with a confirmed "Remove previews". Changing the choice never
 * changes scenes that are already prepared.
 */
export function ClusterDatasetSettings() {
  const [servers, setServers] = useState<ServerEntry[]>([]);
  const [scope, setScope] = useState<string>(ALL);
  const [setting, setSetting] = useState<CameraImagesSetting | null>(null);
  const [usage, setUsage] = useState<CameraPreviewsUsage | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void listServerProfiles()
      .then((list) => setServers(Array.isArray(list) ? list : []))
      .catch(() => setServers([]));
  }, []);

  const profileId = scope === ALL ? null : scope;

  const loadUsage = useCallback(async (id: string) => {
    setUsage(null);
    setUsageError(null);
    try {
      const value = await datasetPreviews(id, false);
      setUsage(value && typeof value.bytes === "number" ? value : null);
    } catch (err) {
      setUsageError(errorText(err, "Could not read the previews on this server."));
    }
  }, []);

  useEffect(() => {
    setConfirming(false);
    void getCameraImagesSetting(profileId)
      .then((value) => setSetting(value && typeof value.effective === "string" ? value : null))
      .catch(() => setSetting(null));
    if (profileId) {
      void loadUsage(profileId);
    } else {
      setUsage(null);
      setUsageError(null);
    }
  }, [profileId, loadUsage]);

  // For one server the choice is its own, or "same as for every server".
  const chosen: CameraImagesMode | "inherit" | null = setting
    ? profileId
      ? (setting.profile ?? "inherit")
      : setting.default
    : null;

  const choose = async (mode: CameraImagesMode | null) => {
    const value = await setCameraImagesSetting(profileId, mode);
    if (value && typeof value.effective === "string") setSetting(value);
  };

  const removePreviews = async () => {
    if (!profileId) return;
    setBusy(true);
    try {
      await datasetPreviews(profileId, true);
      setConfirming(false);
      await loadUsage(profileId);
    } catch (err) {
      setUsageError(errorText(err, "Could not remove the previews."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section" aria-label="Cluster datasets">
      <h3 className="settings-heading">Cluster datasets</h3>
      <label className="settings-field">
        <span>Camera images when preparing</span>
        <select value={scope} onChange={(event) => setScope(event.target.value)} aria-label="Server">
          <option value={ALL}>Every server</option>
          {servers.map((server) => (
            <option key={server.id} value={server.id}>
              {server.profile.name || server.id}
            </option>
          ))}
        </select>
      </label>
      {chosen === null ? null : (
        <div role="radiogroup" aria-label="Camera images when preparing">
          {profileId ? (
            <SettingRow
              type="radio"
              name="camera-images-setting"
              value="inherit"
              checked={chosen === "inherit"}
              title="Same as for every server"
              description={`Now: ${setting?.default === "previews" ? "save previews" : "read originals"}.`}
              onChange={() => void choose(null)}
            />
          ) : null}
          {cameraImageChoices(0).map((choice) => (
            <SettingRow
              key={choice.mode}
              type="radio"
              name="camera-images-setting"
              value={choice.mode}
              checked={chosen === choice.mode}
              title={choice.label}
              description={
                choice.mode === "previews"
                  ? "Uses disk in the results folder; the prepare preview shows how much."
                  : choice.detail
              }
              onChange={() => void choose(choice.mode)}
            />
          ))}
          <p className="setting-note">Applies to the next prepare; prepared scenes keep theirs. You can still change it in each prepare preview.</p>
        </div>
      )}
      {profileId ? (
        <div className="previews-usage" aria-live="polite">
          {usageError ? <p className="setting-note error">{usageError}</p> : null}
          {usage ? (
            <p className="setting-note">
              Previews on this server: {usage.bytes > 0 ? `${formatBytes(usage.bytes)} in ${usage.scenes} scene${usage.scenes === 1 ? "" : "s"}` : "none"}.
            </p>
          ) : usageError ? null : (
            <p className="setting-note">Reading previews on this server…</p>
          )}
          {usage && usage.bytes > 0 && !confirming ? (
            <button type="button" className="ghost" onClick={() => setConfirming(true)}>
              Remove previews…
            </button>
          ) : null}
          {confirming && usage ? (
            <div role="alertdialog" aria-label="Remove previews" className="confirm-row">
              <p className="setting-note">
                Remove {formatBytes(usage.bytes)} of camera previews? Only the previews are deleted; the scenes stay viewable from the dataset's originals.
              </p>
              <button type="button" onClick={() => void removePreviews()} disabled={busy}>
                {busy ? "Removing…" : "Remove previews"}
              </button>
              <button type="button" className="ghost" onClick={() => setConfirming(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
