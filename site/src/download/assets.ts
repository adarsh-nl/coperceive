// SPDX-License-Identifier: Apache-2.0
/**
 * The only place a release asset name is matched to a platform.
 * Names follow the release workflow: updater archives have no version,
 * installers are Coperceive_<version>_<arch>.<ext>. The version is never
 * written here; it is whatever the latest release published.
 */

export interface ReleaseAsset {
  name: string;
  browser_download_url: string;
}

export interface ReleaseJson {
  tag_name?: string;
  assets: ReleaseAsset[];
}

export type OsName = "mac" | "windows" | "linux" | "other";
export type ArchName = "arm64" | "x64";

export interface ClientInfo {
  os: OsName;
  /** null when the browser will not say (Safari on a Mac). */
  arch: ArchName | null;
}

export type AssetSlot =
  | "mac-arm64-dmg"
  | "mac-x64-dmg"
  | "windows-setup"
  | "linux-appimage"
  | "linux-appimage-updater"
  | "linux-deb"
  | "windows-updater"
  | "mac-arm64-updater"
  | "mac-x64-updater";

interface AssetRule {
  slot: AssetSlot;
  match: RegExp;
}

/**
 * Installers and updater archives. Proof files (.sig, checksums) are separate.
 * Names are the ones release.yml publishes: updater tarballs are renamed, and
 * the DMG, NSIS setup.exe, AppImage and deb keep Tauri's basename. The AppImage
 * and deb patterns are case-insensitive because the updater writer also accepts
 * a lowercase AppImage, and Debian packages are often lowercase.
 */
export const ASSET_TABLE: readonly AssetRule[] = [
  { slot: "mac-arm64-updater", match: /^Coperceive_aarch64\.app\.tar\.gz$/ },
  { slot: "mac-x64-updater", match: /^Coperceive_x64\.app\.tar\.gz$/ },
  { slot: "mac-arm64-dmg", match: /^Coperceive_.+_aarch64\.dmg$/ },
  { slot: "mac-x64-dmg", match: /^Coperceive_.+_x64\.dmg$/ },
  { slot: "windows-setup", match: /^Coperceive_.+_x64-setup\.exe$/ },
  { slot: "linux-appimage", match: /^Coperceive_.+_amd64\.AppImage$/i },
  { slot: "linux-appimage-updater", match: /^Coperceive_.+_amd64\.AppImage\.tar\.gz$/i },
  { slot: "linux-deb", match: /^Coperceive_.+_amd64\.deb$/i },
  { slot: "windows-updater", match: /^Coperceive_.+_x64-setup\.nsis\.zip$/ },
];

const PROOF = /\.(sig|sha256|sha512)$/i;
const CHECKSUM_NAME = /^(checksums?|sha256sums?)(\.|$)/i;

export const COMING = "Coming in the next release";

/** README wording for the unsigned Windows installer. */
export const WINDOWS_NOTE =
  "Unsigned release builds trigger Windows SmartScreen on first run. Click More info → Run anyway to launch the installer.";

export function slotFor(name: string): AssetSlot | null {
  if (PROOF.test(name) || CHECKSUM_NAME.test(name)) return null;
  return ASSET_TABLE.find((rule) => rule.match.test(name))?.slot ?? null;
}

export function isProof(name: string): boolean {
  return PROOF.test(name) || CHECKSUM_NAME.test(name);
}

export function slimRelease(payload: unknown): ReleaseJson | null {
  if (!payload || typeof payload !== "object") return null;
  const row = payload as { tag_name?: unknown; assets?: unknown };
  if (!Array.isArray(row.assets)) return null;
  const assets: ReleaseAsset[] = [];
  for (const item of row.assets) {
    if (!item || typeof item !== "object") continue;
    const asset = item as { name?: unknown; browser_download_url?: unknown };
    if (typeof asset.name !== "string" || typeof asset.browser_download_url !== "string") continue;
    if (!asset.browser_download_url) continue;
    assets.push({ name: asset.name, browser_download_url: asset.browser_download_url });
  }
  return {
    tag_name: typeof row.tag_name === "string" ? row.tag_name : undefined,
    assets,
  };
}

export function assetFor(release: ReleaseJson, slot: AssetSlot): ReleaseAsset | null {
  return release.assets.find((asset) => slotFor(asset.name) === slot) ?? null;
}

export interface DownloadLink {
  label: string;
  asset: ReleaseAsset;
  role: "primary" | "secondary";
}

export interface PrimaryOffer {
  links: DownloadLink[];
  /** A platform whose installer is not in this release. */
  coming: string[];
  windowsNote: boolean;
}

/**
 * The visitor's primary download. A missing installer is named in `coming`
 * and is never given a URL.
 */
export function primaryOffer(release: ReleaseJson, client: ClientInfo): PrimaryOffer {
  const offer: PrimaryOffer = { links: [], coming: [], windowsNote: false };
  const add = (label: string, slot: AssetSlot, role: DownloadLink["role"] = "primary") => {
    const asset = assetFor(release, slot);
    if (asset) offer.links.push({ label, asset, role });
    else if (role === "primary") offer.coming.push(label);
  };
  if (client.os === "windows") {
    add("Download for Windows", "windows-setup");
    offer.windowsNote = offer.links.length > 0;
    return offer;
  }
  if (client.os === "linux") {
    if (client.arch === "arm64") {
      offer.coming.push("Download for Linux");
      return offer;
    }
    add("Download for Linux", "linux-appimage");
    add("Debian package (.deb)", "linux-deb", "secondary");
    return offer;
  }
  if (client.os === "mac") {
    if (client.arch === "arm64") {
      add("Download for Mac (Apple Silicon)", "mac-arm64-dmg");
      return offer;
    }
    if (client.arch === "x64") {
      add("Download for Mac (Intel)", "mac-x64-dmg");
      return offer;
    }
    add("Download for Mac (Apple Silicon)", "mac-arm64-dmg");
    add("Download for Mac (Intel)", "mac-x64-dmg");
    return offer;
  }
  offer.coming.push("Download");
  return offer;
}

export interface ListedAsset {
  asset: ReleaseAsset;
  proof: ReleaseAsset[];
}

function isDownload(name: string): boolean {
  if (slotFor(name) !== null) return true;
  // Older releases used Coperceive.app.tar.gz before the arch was added to the name.
  return /^Coperceive.+\.(dmg|exe|deb|AppImage|tar\.gz|zip)$/i.test(name);
}

function attachedProof(assetName: string, proofName: string): boolean {
  return proofName === `${assetName}.sig` || proofName === `${assetName}.sha256` || proofName === `${assetName}.sha512`;
}

/** Every installer and updater, with a signature or checksum when the release has one. */
export function otherPlatforms(release: ReleaseJson): ListedAsset[] {
  const proofs = release.assets.filter((asset) => isProof(asset.name));
  const rows = release.assets
    .filter((asset) => isDownload(asset.name))
    .map((asset) => ({
      asset,
      proof: proofs.filter((item) => attachedProof(asset.name, item.name)),
    }));
  const attached = new Set(rows.flatMap((row) => row.proof.map((item) => item.name)));
  for (const proof of proofs) {
    if (!attached.has(proof.name)) rows.push({ asset: proof, proof: [] });
  }
  return rows;
}

/**
 * Architecture comes only from userAgentData. A Mac UA that says "Intel" is
 * not enough: Safari sends that on Apple Silicon and does not expose the chip.
 */
export function clientFromSignals(userAgent: string, architecture: string | null | undefined): ClientInfo {
  let os: OsName = "other";
  if (/Windows/i.test(userAgent)) os = "windows";
  else if (/Macintosh|Mac OS X/i.test(userAgent)) os = "mac";
  else if (/Android/i.test(userAgent)) os = "other";
  else if (/Linux/i.test(userAgent)) os = "linux";

  let arch: ArchName | null = null;
  const chip = architecture?.toLowerCase();
  if (chip === "arm" || chip === "arm64" || chip === "aarch64") arch = "arm64";
  else if (chip === "x86" || chip === "x64" || chip === "x86_64" || chip === "amd64") arch = "x64";
  else if (os === "linux") arch = /aarch64|arm64/i.test(userAgent) ? "arm64" : "x64";
  else if (os === "windows") arch = "x64";
  return { os, arch };
}
