# Tauri Auto-Updater Configuration and Secrets

Coperceive Desktop uses Tauri v2's built-in Minisign updater. This allows unsigned macOS builds to self-update securely without requiring an Apple Developer Certificate or notary service.

## 1. GitHub Actions Secrets

To enable automated signing of updater bundles in `.github/workflows/release.yml`, add the following secret in your GitHub repository (**Settings → Secrets and variables → Actions**):

### Secret: `TAURI_SIGNING_PRIVATE_KEY`
Paste the exact private key block below:

```
dW50cnVzdGVkIGNvbW1lbnQ6IHJzaWduIGVuY3J5cHRlZCBzZWNyZXQga2V5ClJXUlRZMEl5VHpXR2JtSm5OY0xLdytyak9YcmNsT2EwQ284eVdPcms5MTFydno3Tm5iRUFBQkFBQUFBQUFBQUFBQUlBQUFBQXdLaXpwaTBBZDdjc2ZKOTZreEJubVVVeXNYU2RNMnhDaWNRMXVzS1I5UHF0SUlCR2JIMnRjcVkra1p5djhtSVZmZDNTd3ZoUis2djNHLy9uYUxYNnFLWFYzM1B6a0pUZmNRWm01T3RTbmRUTWFRZkEwNUdEZEdxTm9tODBYejl4RWxIY2tWeWNOVm89Cg==
```

### Secret: `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
Leave empty or set to empty string `""` (the key was generated with an empty passphrase for automated CI builds).

---

## 2. Public Key in `tauri.conf.json`

The corresponding public key is committed to `src-tauri/tauri.conf.json`:
```json
"plugins": {
  "updater": {
    "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDY0OTQ0QURBRUZCRjYyNEIKUldSTFlyL3Yya3FVWlBUR2tIUnVXTUhXSVJJWm5jb3ZxSCtUQk1CQlFXUUYzb1BoM2p5S3BXcmMK",
    "endpoints": [
      "https://github.com/adarsh-nl/coperceive/releases/latest/download/latest.json"
    ]
  }
}
```

---

## 3. Release Workflow (`.github/workflows/release.yml`)

1. Adarsh pushes a version tag:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
2. GitHub Actions builds the macOS `.dmg`, `.app.tar.gz`, and generates `.app.tar.gz.sig` using the private key secret.
3. It creates a GitHub Release for `v0.2.0` and uploads:
   - `Coperceive_<version>_aarch64.dmg`
   - `Coperceive.app.tar.gz`
   - `Coperceive.app.tar.gz.sig`
   - `latest.json`
4. Existing desktop clients querying `releases/latest/download/latest.json` will prompt:
   **"coperceive 0.2.0 is ready. Restart to update."**
