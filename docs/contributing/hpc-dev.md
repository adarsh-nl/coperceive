# Rust checks on a headless Linux host

The pre-push hook runs `cargo check` in `src-tauri/` when a push changes Rust files. On
Linux, the desktop crate needs GTK 3 and WebKit2GTK 4.1 development files through
`pkg-config`. A shared login node often lacks these files, and you cannot install system
packages there. This page builds them in user space with conda-forge. You need no root
access and no system packages.

Paths below are placeholders. Replace `/home/alice` and `~/src/coperceive` with your own.

## 1. A separate conda environment

Do not install into `base`.

```bash
conda create -y -n coperceive-tauri-deps --override-channels -c conda-forge \
  pkg-config dbus gtk3 webkit2gtk4.1 libsoup openssl glib librsvg zlib expat
```

Notes:

- `webkit2gtk4.1` provides the `webkit2gtk-4.1` module that Tauri v2 links on Linux.
- Keep `zlib` and `expat` in the list. They provide `zlib.pc` and `expat.pc`, which
  `gio-2.0` and `gtk+-3.0` require. Without them, `gio-sys` fails with "Package 'zlib' not
  found".
- The environment takes about 0.6 GB.

## 2. An environment file to source

Put it in your clone, for example `~/src/coperceive/.hpc-env.sh`, and add it to
`.git/info/exclude` so it is never committed:

```bash
_tauri_env="$HOME/.conda/envs/coperceive-tauri-deps"
export PKG_CONFIG="$_tauri_env/bin/pkg-config"
export PKG_CONFIG_PATH="$_tauri_env/lib/pkgconfig:$_tauri_env/share/pkgconfig"
export CARGO_TARGET_DIR="$HOME/src/coperceive/.cargo-target"   # in your clone, not shared
export CARGO_BUILD_JOBS=1                                      # one core on a shared node
export PATH="/home/alice/node/bin:$PATH"                       # node, npm, npx
unset _tauri_env
```

Many clusters limit login-node commands to about a minute on one core, so this file
keeps Cargo to a single job. Check your site's rules.

Do not export `LD_LIBRARY_PATH` in this file. The environment ships its own OpenSSL and
other libraries, so `git` and `ssh` in the same shell could load them.

Add `.cargo-target/` to `.git/info/exclude` too.

Source the file in the shell you push from. The hook calls plain `cargo check` and
`npx tsc -b`, so it picks up these variables.

Without them:

- `cargo check` fails, or rebuilds from scratch into `src-tauri/target` at full
  parallelism.
- The hook skips `tsc -b` with only a warning when `npx` is not on `PATH`.

## 3. The web bundle

`tauri::generate_context!` needs `coperceive/app/static`, which is built and never
committed. Build it once, and again after web changes:

```bash
bash scripts/build_web_static.sh
```

This needs Node and npm on `PATH`.

## 4. Check

```bash
source ~/src/coperceive/.hpc-env.sh
cd src-tauri && nice -n 19 cargo check --quiet
```

- **Warm checks:** after the first build, the hook's `cargo check` and `tsc -b` take
  seconds, which fits a login node.
- **Cold rebuilds:** the first `cargo check`, or one after a dependency change, compiles
  every dependency and takes several minutes. Run it only when needed, at `nice -n 19`
  with `CARGO_BUILD_JOBS=1`. If your site does not allow a build of that length on a
  login node, run it in a batch job instead.
- **Tests:** do not run `cargo test` on a login node. Its test build is as long as a cold
  rebuild. CI runs the Rust tests on pull requests that change desktop code.

Never bypass the hook with `--no-verify`.
