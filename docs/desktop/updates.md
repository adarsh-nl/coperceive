# Desktop updates

Coperceive checks GitHub Releases for a newer macOS build and asks before restarting. The check is not Apple code signing. It uses a Tauri updater keypair. The private key must never be committed, pasted into a chat, or written into `state.json`, logs, or this file.

The public key that used to be in `src-tauri/tauri.conf.json` matched a private key that was committed in `docs/updater-secrets.md` (commit `e7f6fef`). Treat that keypair as public. Do not put it in GitHub Actions secrets. Generate a new pair.

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

1. Public key only, into `src-tauri/tauri.conf.json` at `plugins.updater.pubkey`, replacing `PASTE_THE_PUBLIC_KEY_FROM_A_NEW_TAURI_SIGNER_KEYPAIR`. Commit that public key.
2. GitHub repository secret `TAURI_SIGNING_PRIVATE_KEY`: the contents of the private key file.
3. GitHub repository secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: the password you chose. If you left the password empty, set the secret to an empty string.

Settings → Secrets and variables → Actions. Do not put either secret in the repo.

## What a tag does

`.github/workflows/release.yml` runs on tags `v*`. It builds the unsigned dmg, and when the signing secrets and a real public key are in place, the Tauri bundler also writes `Coperceive.app.tar.gz`, `Coperceive.app.tar.gz.sig`, and `latest.json`. Those files are uploaded to the GitHub Release. The app reads:

`https://github.com/adarsh-nl/coperceive/releases/latest/download/latest.json`

Until the new public key is pasted, a tag build does not produce a signature the app can trust. The in-app banner can still be exercised in CI against a mock `latest.json` (`COPERCEIVE_UPDATE_URL`). That mock does not install a build.

## What the app does

- On launch, and again every 6 hours, if Settings → Automatically download updates is on (the default, stored in `state.json` as `auto_download_updates`).
- Help → Check for Updates… reports checking, up to date, or an error with Retry.
- A newer manifest shows a banner: "coperceive X is ready. Restart to update." with What's new, Restart now, and Later. Later leaves the running app alone. Restart is not sent during the automated UI test.

Opening an unsigned dmg still needs right-click → Open the first time. That is separate from this keypair.
