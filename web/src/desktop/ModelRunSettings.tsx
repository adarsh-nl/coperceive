// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";

import { errorText } from "./errors";
import type { ServerEntry } from "./hpc";
import { getPluginPythonSetting, listServerProfiles, setPluginPythonSetting } from "./local";
import { pluginPythonProblem } from "./runPlan";

/**
 * Settings → Model runs: the plugin interpreter (`--plugin-python`) New evaluation
 * starts with for a built-in model, per server; each run can still change it. Empty
 * means the model manifest's environment. Plans already saved keep their own.
 */
export function ModelRunSettings() {
  const [servers, setServers] = useState<ServerEntry[]>([]);
  const [profileId, setProfileId] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void listServerProfiles()
      .then((list) => {
        const entries = Array.isArray(list) ? list : [];
        setServers(entries);
        if (entries.length > 0) setProfileId((current) => current || entries[0].id);
      })
      .catch(() => setServers([]));
  }, []);

  useEffect(() => {
    if (!profileId) return;
    let current = true;
    void getPluginPythonSetting(profileId)
      .then((value) => {
        if (!current) return;
        const path = typeof value === "string" ? value : null;
        setSaved(path);
        setDraft(path ?? "");
      })
      .catch(() => {
        if (!current) return;
        setSaved(null);
        setDraft("");
      });
    return () => {
      current = false;
    };
  }, [profileId]);

  const problem = pluginPythonProblem(draft);
  const changed = (draft.trim() === "" ? null : draft) !== saved;

  const save = async (path: string | null) => {
    setBusy(true);
    setStatus(null);
    try {
      const value = await setPluginPythonSetting(profileId, path);
      setSaved(value);
      setDraft(value ?? "");
      setStatus(value ? "Saved. New evaluations on this server start with it." : "Cleared: the model's manifest decides.");
    } catch (err) {
      setStatus(errorText(err, "Could not save the setting."));
    } finally {
      setBusy(false);
    }
  };

  if (servers.length === 0) return null;
  return (
    <section className="settings-section" aria-label="Model runs">
      <h3 className="settings-heading">Model runs</h3>
      <label className="settings-field">
        <span>Plugin interpreter for built-in models</span>
        <select value={profileId} onChange={(event) => {
            setStatus(null);
            setProfileId(event.target.value);
          }}
          aria-label="Server"
        >
          {servers.map((server) => (
            <option key={server.id} value={server.id}>
              {server.profile.name || server.id}
            </option>
          ))}
        </select>
      </label>
      <input
        className="c-path-input"
        aria-label="Plugin interpreter path"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="/path/to/envs/<env>/bin/python"
        aria-invalid={problem ? true : undefined}
        spellCheck={false}
      />
      <p className={problem ? "setting-note error" : "setting-note"} aria-live="polite">
        {problem ??
          status ??
          "A full path on this server, passed to coperceive model run as --plugin-python. Empty: the model's manifest environment. Each New evaluation can change it."}
      </p>
      <div className="confirm-row">
        <button type="button" onClick={() => void save(draft)} disabled={busy || !changed || problem !== null}>
          Save
        </button>
        <button type="button" className="ghost" onClick={() => void save(null)} disabled={busy || saved === null}>
          Clear
        </button>
      </div>
    </section>
  );
}
