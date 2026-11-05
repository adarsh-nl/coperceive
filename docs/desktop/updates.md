# Desktop updates

Coperceive checks GitHub Releases for a newer macOS build and asks before restarting. The check is not Apple code signing. It uses a Tauri updater keypair. The private key must never be committed, pasted into a chat, or written into `state.json`, logs, or this file.

The public key that used to be in `src-tauri/tauri.conf.json` matched a private key that was committed in `docs/updater-secrets.md` (commit `e7f6fef`). Treat that keypair as public. It was replaced on 2026-10-30: the current public key (minisign key id `9AB294B56FC3C444`) is in `src-tauri/tauri.conf.json`, and its private key and password exist only on Adarsh's Mac and in the GitHub Actions secrets below.

## What to generate

On your Mac, with the Tauri CLI installed in `web/`:

```bash
cd web
npx tauri signer generate -w "$HOME/coperceive-updater.key"
```

The command asks for a password. Remember it. It writes:

- a private key file (keep this on your machine, not in git)
- a public key printed in the terminal

## What to paste

1. Public key only (`cat ~/coperceive-updater.key.pub`), into `src-tauri/tauri.conf.json` at `plugins.updater.pubkey`. Commit that public key.
2. GitHub repository secret `TAURI_SIGNING_PRIVATE_KEY`: the contents of the private key file.
3. GitHub repository secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: the password you chose. If you left the password empty, set the secret to an empty string.

Settings → Secrets and variables → Actions. Do not put either secret in the repo.

## What a tag does

`.github/workflows/release.yml` runs on tags `v*`:

1. It turns on `bundle.createUpdaterArtifacts` in the CI workspace only (ordinary CI builds have no signing key, so the committed config leaves it off). The step fails if `TAURI_SIGNING_PRIVATE_KEY` is not set.
2. The Tauri bundler builds the unsigned dmg plus `Coperceive.app.tar.gz` and `Coperceive.app.tar.gz.sig`.
3. `scripts/desktop/write_latest_json.py` writes `latest.json` from the signature and the bundle version. The bundler does not write this file (Tauri's docs leave it to tauri-action or to you). The script refuses a tag that does not match the bundle version, because an app that reports the old version after updating would be offered the same update forever.
4. The dmg, archive, signature and `latest.json` are uploaded to the GitHub Release.

The app reads:

`https://github.com/adarsh-nl/coperceive/releases/latest/download/latest.json`

The in-app banner can still be exercised in CI against a mock `latest.json` (`COPERCEIVE_UPDATE_URL`). That mock does not install a build.

## What the app does

- On launch, and again every 6 hours, if Settings → Automatically download updates is on (the default, stored in `state.json` as `auto_download_updates`).
- Help → Check for Updates… reports checking, up to date, or an error with Retry.
- A newer manifest shows a banner: "coperceive X is ready. Restart to update." with What's new, Restart now, and Later. Later leaves the running app alone. Restart is not sent during the automated UI test.

Opening an unsigned dmg still needs right-click → Open the first time. That is separate from this keypair.
