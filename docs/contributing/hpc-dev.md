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

Put it outside the repository, for example `/home/alice/env/coperceive-rust.sh`:

```bash
_tauri_env="$HOME/.conda/envs/coperceive-tauri-deps"
export PKG_CONFIG="$_tauri_env/bin/pkg-config"
export PKG_CONFIG_PATH="$_tauri_env/lib/pkgconfig:$_tauri_env/share/pkgconfig"
export CARGO_TARGET_DIR="$HOME/src/coperceive/.cargo-target"   # in your clone, not shared
export CARGO_BUILD_JOBS=2                                      # be gentle on a shared node
unset _tauri_env
```

Do not export `LD_LIBRARY_PATH` in this file. The environment ships its own OpenSSL and
other libraries, so `git` and `ssh` in the same shell could load them. Set it only on the
`cargo test` command line (step 4).

Add `.cargo-target/` to `.git/info/exclude`.

Source the file in the shell you push from. The hook calls plain `cargo check`, so it
picks up these variables. Without them, the hook fails, or it rebuilds from scratch into
`src-tauri/target` at full parallelism.

## 3. The web bundle

`tauri::generate_context!` needs `coperceive/app/static`, which is built and never
committed. Build it once, and again after web changes:

```bash
bash scripts/build_web_static.sh
```

This needs Node and npm on `PATH`.

## 4. Check

```bash
source /home/alice/env/coperceive-rust.sh
cd src-tauri && nice -n 19 cargo check --quiet
LD_LIBRARY_PATH="$HOME/.conda/envs/coperceive-tauri-deps/lib" nice -n 19 cargo test --lib
```

With `CARGO_BUILD_JOBS=2`, expect these times:

- The first `cargo check` compiles every dependency and takes a few minutes.
- Later checks take seconds.
- `cargo test --lib` builds the test profile once.

Run long checks at low priority and follow your site's login-node policy. If the
policy does not allow a build of this size, run it in a batch job instead.

Never bypass the hook with `--no-verify`.
