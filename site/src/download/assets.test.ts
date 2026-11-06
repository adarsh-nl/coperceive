// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { clientFromSignals, otherPlatforms, primaryOffer, slotFor, type ReleaseJson } from "./assets.ts";

const MAC_CHROME =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
const MAC_SAFARI =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
const LINUX = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function release(names: string[]): ReleaseJson {
  return {
    tag_name: "v9.9.9",
    assets: names.map((name) => ({ name, browser_download_url: `https://example.test/releases/${name}` })),
  };
}

/** v0.1.0 shipped a Mac disk image and nothing else. */
const V010 = release(["Coperceive_0.1.0_aarch64.dmg"]);

/** Names from the release workflow: installers carry the version, updater archives do not. */
const MATRIX = release([
  "Coperceive_0.1.1_aarch64.dmg",
  "Coperceive_0.1.1_x64.dmg",
  "Coperceive_0.1.1_x64-setup.exe",
  "Coperceive_0.1.1_amd64.AppImage",
  "Coperceive_0.1.1_amd64.deb",
  "Coperceive_aarch64.app.tar.gz",
  "Coperceive_x64.app.tar.gz",
  "Coperceive_0.1.1_amd64.AppImage.tar.gz",
  "Coperceive_0.1.1_x64-setup.nsis.zip",
  "Coperceive_aarch64.app.tar.gz.sig",
  "Coperceive_0.1.1_x64-setup.exe.sig",
  "Coperceive_0.1.1_x64-setup.nsis.zip.sig",
  "SHA256SUMS",
]);

test("each operating system and architecture is offered its own file", () => {
  const arm = primaryOffer(MATRIX, clientFromSignals(MAC_CHROME, "arm"));
  assert.deepEqual(arm.links.map((link) => link.asset.name), ["Coperceive_0.1.1_aarch64.dmg"]);
  assert.equal(arm.coming.length, 0);

  const intel = primaryOffer(MATRIX, clientFromSignals(MAC_CHROME, "x86"));
  assert.deepEqual(intel.links.map((link) => link.asset.name), ["Coperceive_0.1.1_x64.dmg"]);

  const windows = primaryOffer(MATRIX, clientFromSignals(WINDOWS, "x86"));
  assert.deepEqual(windows.links.map((link) => link.asset.name), ["Coperceive_0.1.1_x64-setup.exe"]);
  assert.equal(windows.windowsNote, true);

  const linux = primaryOffer(MATRIX, clientFromSignals(LINUX, "x86"));
  assert.deepEqual(
    linux.links.map((link) => link.asset.name),
    ["Coperceive_0.1.1_amd64.AppImage", "Coperceive_0.1.1_amd64.deb"],
  );
  assert.equal(linux.links[1]?.label, "Debian package (.deb)");

  const linuxArm = primaryOffer(MATRIX, clientFromSignals("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36", null));
  assert.deepEqual(linuxArm.links, []);
  assert.equal(linuxArm.coming.length, 1);
});

test("Safari on a Mac is offered both disk images", () => {
  const safari = primaryOffer(MATRIX, clientFromSignals(MAC_SAFARI, null));
  assert.deepEqual(
    safari.links.map((link) => link.asset.name),
    ["Coperceive_0.1.1_aarch64.dmg", "Coperceive_0.1.1_x64.dmg"],
  );
});

test("a release without an NSIS updater archive does not list that slot", () => {
  const names = otherPlatforms(V010).map((item) => item.asset.name);
  assert.equal(names.some((name) => name.endsWith(".nsis.zip")), false);
});

test("a Mac-only release says Windows and Linux are coming, and never links them", () => {
  const windows = primaryOffer(V010, clientFromSignals(WINDOWS, null));
  assert.deepEqual(windows.links, []);
  assert.equal(windows.coming.length, 1);
  assert.equal(windows.windowsNote, false);

  const linux = primaryOffer(V010, clientFromSignals(LINUX, "x86"));
  assert.deepEqual(linux.links, []);
  assert.ok(linux.coming.length > 0);

  const intel = primaryOffer(V010, clientFromSignals(MAC_CHROME, "x86"));
  assert.deepEqual(intel.links, []);
  assert.equal(intel.coming.length, 1);
});

test("the release-workflow names map every platform, including proofs", () => {
  assert.equal(slotFor("Coperceive_0.1.1_aarch64.dmg"), "mac-arm64-dmg");
  assert.equal(slotFor("Coperceive_0.1.1_x64.dmg"), "mac-x64-dmg");
  assert.equal(slotFor("Coperceive_0.1.1_x64-setup.exe"), "windows-setup");
  assert.equal(slotFor("Coperceive_0.1.1_amd64.AppImage"), "linux-appimage");
  assert.equal(slotFor("Coperceive_0.1.1_amd64.deb"), "linux-deb");
  assert.equal(slotFor("Coperceive_aarch64.app.tar.gz"), "mac-arm64-updater");
  assert.equal(slotFor("Coperceive_x64.app.tar.gz"), "mac-x64-updater");
  assert.equal(slotFor("coperceive_0.1.1_amd64.AppImage"), "linux-appimage");
  assert.equal(slotFor("coperceive_0.1.1_amd64.deb"), "linux-deb");
  assert.equal(slotFor("Coperceive_0.1.1_amd64.AppImage.tar.gz"), "linux-appimage-updater");
  assert.equal(slotFor("Coperceive_0.1.1_x64-setup.nsis.zip"), "windows-updater");
  assert.equal(slotFor("Coperceive_aarch64.app.tar.gz.sig"), null);

  const listed = otherPlatforms(MATRIX);
  const names = listed.map((item) => item.asset.name);
  assert.ok(names.includes("Coperceive_0.1.1_aarch64.dmg"));
  assert.ok(names.includes("Coperceive_0.1.1_x64-setup.exe"));
  assert.ok(names.includes("Coperceive_0.1.1_amd64.AppImage"));
  assert.ok(names.includes("Coperceive_0.1.1_amd64.deb"));
  assert.ok(names.includes("Coperceive_aarch64.app.tar.gz"));
  assert.ok(names.includes("Coperceive_x64.app.tar.gz"));
  assert.ok(names.includes("Coperceive_0.1.1_amd64.AppImage.tar.gz"));
  assert.ok(names.includes("Coperceive_0.1.1_x64-setup.nsis.zip"));
  const zip = listed.find((item) => item.asset.name === "Coperceive_0.1.1_x64-setup.nsis.zip");
  assert.deepEqual(zip?.proof.map((item) => item.name), ["Coperceive_0.1.1_x64-setup.nsis.zip.sig"]);
  const updater = listed.find((item) => item.asset.name === "Coperceive_aarch64.app.tar.gz");
  assert.deepEqual(updater?.proof.map((item) => item.name), ["Coperceive_aarch64.app.tar.gz.sig"]);
  const setup = listed.find((item) => item.asset.name === "Coperceive_0.1.1_x64-setup.exe");
  assert.deepEqual(setup?.proof.map((item) => item.name), ["Coperceive_0.1.1_x64-setup.exe.sig"]);
  const image = listed.find((item) => item.asset.name === "Coperceive_0.1.1_amd64.AppImage");
  assert.deepEqual(image?.proof, []);
  assert.ok(listed.some((item) => item.asset.name === "SHA256SUMS"));
});

test("an older Mac release lists its updater archive and hides stray files", () => {
  const older = release([
    "Coperceive.app.tar.gz",
    "Coperceive.app.tar.gz.sig",
    "Coperceive_0.1.0_aarch64.dmg",
    "dateutil-zoneinfo.tar.gz",
    "latest.json",
  ]);
  const listed = otherPlatforms(older);
  const names = listed.map((item) => item.asset.name);
  assert.ok(names.includes("Coperceive.app.tar.gz"));
  assert.ok(names.includes("Coperceive_0.1.0_aarch64.dmg"));
  assert.equal(names.includes("dateutil-zoneinfo.tar.gz"), false);
  assert.equal(names.includes("latest.json"), false);
  const archive = listed.find((item) => item.asset.name === "Coperceive.app.tar.gz");
  assert.deepEqual(archive?.proof.map((item) => item.name), ["Coperceive.app.tar.gz.sig"]);
});
