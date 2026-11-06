// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import {
  COMING,
  WINDOWS_NOTE,
  clientFromSignals,
  otherPlatforms,
  primaryOffer,
  type ClientInfo,
  type ReleaseJson,
} from "./assets.ts";
import { loadRelease } from "./releaseLoad.ts";

interface UserAgentData {
  getHighEntropyValues?: (hints: string[]) => Promise<{ architecture?: string }>;
}

async function detectClient(): Promise<ClientInfo> {
  const data = (navigator as Navigator & { userAgentData?: UserAgentData }).userAgentData;
  let architecture: string | null | undefined;
  if (data?.getHighEntropyValues) {
    try {
      const values = await data.getHighEntropyValues(["architecture"]);
      architecture = values.architecture ?? null;
    } catch {
      architecture = null;
    }
  }
  return clientFromSignals(navigator.userAgent, architecture);
}

export function DownloadPanel() {
  const [release, setRelease] = useState<ReleaseJson | null>(null);
  const [client, setClient] = useState<ClientInfo | null>(null);
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    let live = true;
    void Promise.all([detectClient(), loadRelease()]).then(([nextClient, nextRelease]) => {
      if (!live) return;
      setClient(nextClient);
      setRelease(nextRelease);
      setSettled(true);
    });
    return () => {
      live = false;
    };
  }, []);

  const offer = release && client ? primaryOffer(release, client) : null;
  const others = release ? otherPlatforms(release) : [];
  const waiting = !settled;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 440 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {waiting ? (
          <span className="lm-btn-primary" aria-busy="true">
            Checking this computer…
          </span>
        ) : offer && offer.links.length > 0 ? (
          offer.links.map((link) => (
            <a
              key={link.asset.name}
              className={link.role === "primary" ? "lm-btn-primary" : "lm-btn-ghost"}
              href={link.asset.browser_download_url}
            >
              {link.label}
            </a>
          ))
        ) : !offer ? (
          <a className="lm-btn-ghost" href="https://github.com/adarsh-nl/coperceive/releases/latest">
            See the releases
          </a>
        ) : null}
      </div>
      {offer?.coming.map((label) => (
        <p key={label} style={{ margin: 0, fontSize: 15, lineHeight: 1.45, color: "#d2d2d7" }}>
          {label}: {COMING}
        </p>
      ))}
      {settled && !release ? (
        <p style={{ margin: 0, fontSize: 15, lineHeight: 1.45, color: "#d2d2d7" }}>The latest release could not be read.</p>
      ) : null}
      {offer?.windowsNote ? (
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.45, color: "#a1a1a6" }}>{WINDOWS_NOTE}</p>
      ) : null}
      {others.length > 0 ? (
        <details>
          <summary style={{ cursor: "pointer", fontSize: 14, color: "#d2d2d7" }}>Other platforms</summary>
          <ul style={{ margin: "10px 0 0", padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
            {others.map((item) => (
              <li key={item.asset.name} style={{ fontSize: 14, lineHeight: 1.4 }}>
                <a href={item.asset.browser_download_url} style={{ color: "#f5f5f7" }}>
                  {item.asset.name}
                </a>
                {item.proof.map((proof) => (
                  <a key={proof.name} href={proof.browser_download_url} style={{ color: "#a1a1a6", marginLeft: 10 }}>
                    {proof.name.endsWith(".sig") ? "signature" : "checksum"}
                  </a>
                ))}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
