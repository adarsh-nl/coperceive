// SPDX-License-Identifier: Apache-2.0
//! Ship the desktop app's built viewer bundle to a remote HPC login home.
//!
//! The viewer page is built into the app; a remote checkout of coperceive has no
//! `coperceive/app/static` after this project stopped committing it. Before a
//! remote session starts, the app uploads its own bundle once per app version to
//! `~/.cache/coperceive/static/<version>/` on the remote (over the user's SSH
//! connection) and passes that directory to `coperceive remote start --static`.
//! A manifest of file sizes catches a partial/old upload and re-uploads it.
//!
//! Nothing is built or installed on the login node; the upload is a plain
//! `scp`/`ssh` copy and a bounded file list.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::slurm_adapter::{RemoteRunner, SshRunner};
use crate::ssh::{shell_quote, SshTarget};

const MANIFEST_NAME: &str = ".coperceive-static-manifest";
const STATIC_REL: &str = ".cache/coperceive/static";
const SSH_TIMEOUT: Duration = Duration::from_secs(20);
const SCP_TIMEOUT: Duration = Duration::from_secs(180);

fn ssh_bin() -> String {
    std::env::var("COPERCEIVE_SSH_BIN").unwrap_or_else(|_| "ssh".to_string())
}

fn scp_bin() -> String {
    std::env::var("COPERCEIVE_SCP_BIN").unwrap_or_else(|_| "scp".to_string())
}

/// The built viewer directory shipped with the app (or built in a dev checkout).
pub fn static_bundle_dir() -> Result<PathBuf, String> {
    if let Ok(configured) = std::env::var("COPERCEIVE_STATIC_DIR") {
        let path = PathBuf::from(configured);
        if path.join("index.html").is_file() {
            return Ok(path);
        }
        return Err(format!(
            "COPERCEIVE_STATIC_DIR={} has no index.html. Run scripts/build_web_static.sh.",
            path.display()
        ));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(path) = find_packaged_static(&exe) {
            return Ok(path);
        }
    }
    let dev = Path::new(env!("CARGO_MANIFEST_DIR")).join("../coperceive/app/static");
    if dev.join("index.html").is_file() {
        return Ok(dev);
    }
    Err(
        "The app's built viewer bundle is missing. Reinstall the app or run \
         scripts/build_web_static.sh in a source checkout."
            .to_string(),
    )
}

/// `<App>.app/Contents/Resources/resources/python/lib/python*/site-packages/coperceive/app/static`
fn find_packaged_static(exe: &Path) -> Option<PathBuf> {
    let contents = exe.parent()?.parent()?;
    let lib = contents.join("Resources/resources/python/lib");
    for entry in fs::read_dir(lib).ok()? {
        let entry = entry.ok()?;
        let candidate = entry
            .path()
            .join("site-packages/coperceive/app/static/index.html");
        if candidate.is_file() {
            return candidate.parent().map(Path::to_path_buf);
        }
    }
    None
}

fn validate_version(version: &str) -> Result<String, String> {
    let valid = !version.is_empty()
        && version.len() <= 64
        && version
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '.' || ch == '-' || ch == '_');
    if !valid || version.contains("..") {
        return Err(format!("Unsupported app version for the static cache: {version:?}."));
    }
    Ok(version.to_string())
}

fn validate_alias(alias: &str) -> Result<(), String> {
    if alias.is_empty() || alias.starts_with('-') || alias.contains(':') || alias.contains('\n') {
        return Err(format!(
            "{alias:?} is not a valid ssh host alias for the static upload."
        ));
    }
    Ok(())
}

fn collect_files(root: &Path, dir: &Path, out: &mut Vec<(String, u64)>) -> Result<(), String> {
    for entry in fs::read_dir(dir).map_err(|error| {
        format!("Could not read {}: {error}", dir.display())
    })? {
        let entry = entry.map_err(|error| format!("Could not read an entry: {error}"))?;
        let path = entry.path();
        let file_type = entry
            .file_type()
            .map_err(|error| format!("Could not stat {}: {error}", path.display()))?;
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() {
            collect_files(root, &path, out)?;
        } else if file_type.is_file() {
            let relative = path
                .strip_prefix(root)
                .map_err(|_| "Static path escaped its root.".to_string())?;
            let relative = relative
                .components()
                .map(|part| part.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join("/");
            let size = entry
                .metadata()
                .map_err(|error| format!("Could not stat {}: {error}", path.display()))?
                .len();
            out.push((relative, size));
        }
    }
    Ok(())
}

/// A deterministic `size relative/path` line per file, sorted by path.
pub fn manifest_for(root: &Path) -> Result<String, String> {
    let mut files: Vec<(String, u64)> = Vec::new();
    collect_files(root, root, &mut files)?;
    files.sort_by(|left, right| left.0.cmp(&right.0));
    let mut text = String::new();
    for (relative, size) in files {
        text.push_str(&format!("{size} {relative}\n"));
    }
    Ok(text)
}

fn run_ssh(
    runner: &dyn RemoteRunner,
    command: &str,
    stdin: Option<&str>,
) -> Result<String, String> {
    let result = runner
        .run(command, SSH_TIMEOUT, stdin)
        .map_err(|error| format!("The remote shell did not answer: {error}"))?;
    if !result.success {
        let detail = if result.stderr.trim().is_empty() {
            result.stdout.trim()
        } else {
            result.stderr.trim()
        };
        return Err(format!(
            "Setting up the remote viewer page failed: {detail}"
        ));
    }
    Ok(result.stdout)
}

fn fetch_remote_manifest(
    alias: &str,
    remote_rel: &str,
    destination: &Path,
) -> Result<Option<String>, String> {
    let _ = fs::remove_file(destination);
    let output = Command::new(scp_bin())
        .args(["-q", "-p"])
        .arg(format!("{alias}:{remote_rel}/{MANIFEST_NAME}"))
        .arg(destination)
        .output()
        .map_err(|error| format!("Could not run scp: {error}"))?;
    if !output.status.success() || !destination.is_file() {
        return Ok(None);
    }
    fs::read_to_string(destination)
        .map(Some)
        .map_err(|error| format!("Could not read the fetched manifest: {error}"))
}

fn upload_static(alias: &str, local: &Path, remote_rel: &str) -> Result<(), String> {
    let source = format!("{}/.", local.display());
    let destination = format!("{alias}:{remote_rel}");
    let output = Command::new(scp_bin())
        .args(["-r", "-q", "-p"])
        .arg(&source)
        .arg(&destination)
        .output()
        .map_err(|error| format!("Could not run scp: {error}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!(
            "Could not upload the viewer page: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

/// Ensures the remote cache holds this app version's static bundle and returns
/// the remote directory to pass as `--static` (a `~/...` path).
pub fn prepare_remote_static(alias: &str, version: &str) -> Result<String, String> {
    validate_alias(alias)?;
    let version = validate_version(version)?;
    let local = static_bundle_dir()?;
    let manifest = manifest_for(&local)?;
    let remote_rel = format!("{STATIC_REL}/{version}");
    let remote_display = format!("~/{remote_rel}");

    let temporary = std::env::temp_dir().join(format!(
        "coperceive-static-manifest-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or_default()
    ));
    let existing = fetch_remote_manifest(alias, &remote_rel, &temporary)?;
    let _ = fs::remove_file(&temporary);
    if existing.as_deref() == Some(manifest.as_str()) {
        return Ok(remote_display);
    }

    let runner = SshRunner::new(ssh_bin(), SshTarget::alias(alias));
    let version_quoted = shell_quote(&version);
    let mkdir = format!(
        "sh -c 'mkdir -p -- \"$HOME/{STATIC_REL}/$1\"' sh {version_quoted}"
    );
    run_ssh(&runner, &mkdir, None)?;
    upload_static(alias, &local, &remote_rel)?;
    let write_manifest = format!(
        "sh -c 'cat > \"$HOME/{STATIC_REL}/$1/{MANIFEST_NAME}\"' sh {version_quoted}"
    );
    run_ssh(&runner, &write_manifest, Some(&manifest))?;
    Ok(remote_display)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    struct EnvGuard {
        keys: Vec<&'static str>,
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            for key in &self.keys {
                std::env::remove_var(key);
            }
        }
    }

    fn write_script(path: &Path, body: &str) {
        fs::write(path, body).expect("write script");
        let mut perms = fs::metadata(path).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(path, perms).expect("permissions");
    }

    fn fake_ssh_script() -> &'static str {
        r#"#!/usr/bin/env bash
set -u
remote="${!#}"
if [ -n "${FAKE_SSH_LOG:-}" ]; then printf '%s\n' "$remote" >> "$FAKE_SSH_LOG"; fi
version="${remote##* }"; version="${version#\'}"; version="${version%\'}"
case "$remote" in
  *"mkdir -p -- \"\$HOME/.cache/coperceive/static/\$1\""*)
      mkdir -p "$FAKE_REMOTE_ROOT/home/.cache/coperceive/static/$version"
      exit 0
      ;;
  *"cat > \"\$HOME/.cache/coperceive/static/\$1/.coperceive-static-manifest\""*)
      mkdir -p "$FAKE_REMOTE_ROOT/home/.cache/coperceive/static/$version"
      cat > "$FAKE_REMOTE_ROOT/home/.cache/coperceive/static/$version/.coperceive-static-manifest"
      exit 0
      ;;
esac
echo "fake ssh: unsupported: $remote" >&2
exit 1
"#
    }

    fn fake_scp_script() -> &'static str {
        r#"#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${FAKE_SCP_LOG:?}"
src="${@: -2:1}"
dst="${@: -1:1}"
case "$src" in
  *:*)
      rel="${src#*:}"
      cp "$FAKE_REMOTE_ROOT/home/$rel" "$dst"
      ;;
  *)
      rel="${dst#*:}"
      mkdir -p "$FAKE_REMOTE_ROOT/home/$rel"
      cp -R "$src" "$FAKE_REMOTE_ROOT/home/$rel/"
      ;;
esac
"#
    }

    fn write_static(root: &Path) {
        let assets = root.join("assets");
        fs::create_dir_all(&assets).expect("assets");
        fs::write(root.join("index.html"), "<script src=\"./assets/app.js\"></script>\n")
            .expect("index");
        fs::write(assets.join("app.js"), "console.log('viewer')\n").expect("js");
    }

    #[test]
    fn uploads_once_then_skips_a_complete_cache() {
        let _lock = ENV_LOCK.lock().unwrap_or_else(|error| error.into_inner());
        let root = std::env::temp_dir().join(format!(
            "coperceive-static-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        let local = root.join("static");
        let remote = root.join("remote");
        fs::create_dir_all(&remote).expect("remote root");
        write_static(&local);
        let ssh = root.join("fake_ssh.sh");
        let scp = root.join("fake_scp.sh");
        write_script(&ssh, fake_ssh_script());
        write_script(&scp, fake_scp_script());
        let ssh_log = root.join("ssh.log");
        let scp_log = root.join("scp.log");

        let guard = EnvGuard {
            keys: vec![
                "COPERCEIVE_SSH_BIN",
                "COPERCEIVE_SCP_BIN",
                "COPERCEIVE_STATIC_DIR",
                "FAKE_REMOTE_ROOT",
                "FAKE_SSH_LOG",
                "FAKE_SCP_LOG",
            ],
        };
        std::env::set_var("COPERCEIVE_SSH_BIN", &ssh);
        std::env::set_var("COPERCEIVE_SCP_BIN", &scp);
        std::env::set_var("COPERCEIVE_STATIC_DIR", &local);
        std::env::set_var("FAKE_REMOTE_ROOT", &remote);
        std::env::set_var("FAKE_SSH_LOG", &ssh_log);
        std::env::set_var("FAKE_SCP_LOG", &scp_log);

        let first = prepare_remote_static("cluster-a", "0.1.0").expect("first upload");
        assert_eq!(first, "~/.cache/coperceive/static/0.1.0");
        let cached = remote.join("home/.cache/coperceive/static/0.1.0");
        assert!(cached.join("index.html").is_file());
        assert!(cached.join("assets/app.js").is_file());
        assert!(cached.join(MANIFEST_NAME).is_file());
        let scp_calls = fs::read_to_string(&scp_log).unwrap_or_default();
        assert_eq!(
            scp_calls.matches(" -r ").count(),
            1,
            "first start uploads once: {scp_calls}"
        );
        let ssh_calls = fs::read_to_string(&ssh_log).unwrap_or_default();
        assert_eq!(ssh_calls.lines().count(), 2, "mkdir + manifest: {ssh_calls}");

        let second = prepare_remote_static("cluster-a", "0.1.0").expect("second start");
        assert_eq!(second, first);
        let scp_calls = fs::read_to_string(&scp_log).unwrap_or_default();
        assert_eq!(
            scp_calls.matches(" -r ").count(),
            1,
            "a complete cache must not be uploaded again: {scp_calls}"
        );
        let ssh_calls = fs::read_to_string(&ssh_log).unwrap_or_default();
        assert_eq!(
            ssh_calls.lines().count(),
            2,
            "a complete cache needs no ssh writes: {ssh_calls}"
        );

        let _ = fs::remove_dir_all(&root);
        drop(guard);
    }

    #[test]
    fn a_partial_cache_is_uploaded_again() {
        let _lock = ENV_LOCK.lock().unwrap_or_else(|error| error.into_inner());
        let root = std::env::temp_dir().join(format!(
            "coperceive-static-partial-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        let local = root.join("static");
        let remote = root.join("remote");
        fs::create_dir_all(&remote).expect("remote root");
        write_static(&local);
        let ssh = root.join("fake_ssh.sh");
        let scp = root.join("fake_scp.sh");
        write_script(&ssh, fake_ssh_script());
        write_script(&scp, fake_scp_script());
        let ssh_log = root.join("ssh.log");
        let scp_log = root.join("scp.log");
        let guard = EnvGuard {
            keys: vec![
                "COPERCEIVE_SSH_BIN",
                "COPERCEIVE_SCP_BIN",
                "COPERCEIVE_STATIC_DIR",
                "FAKE_REMOTE_ROOT",
                "FAKE_SSH_LOG",
                "FAKE_SCP_LOG",
            ],
        };
        std::env::set_var("COPERCEIVE_SSH_BIN", &ssh);
        std::env::set_var("COPERCEIVE_SCP_BIN", &scp);
        std::env::set_var("COPERCEIVE_STATIC_DIR", &local);
        std::env::set_var("FAKE_REMOTE_ROOT", &remote);
        std::env::set_var("FAKE_SSH_LOG", &ssh_log);
        std::env::set_var("FAKE_SCP_LOG", &scp_log);

        prepare_remote_static("cluster-a", "0.1.0").expect("upload");
        let cached = remote.join("home/.cache/coperceive/static/0.1.0");
        // A stale file (or a previous app build) invalidates the manifest.
        fs::write(cached.join("assets/app.js"), "console.log('stale')\n").expect("stale");
        prepare_remote_static("cluster-a", "0.1.0").expect("re-upload");
        let scp_calls = fs::read_to_string(&scp_log).unwrap_or_default();
        assert_eq!(
            scp_calls.matches(" -r ").count(),
            2,
            "a stale cache must be uploaded again: {scp_calls}"
        );
        let current = fs::read_to_string(cached.join("assets/app.js")).expect("js");
        assert!(current.contains("viewer"), "{current}");

        let _ = fs::remove_dir_all(&root);
        drop(guard);
    }

    #[test]
    fn rejects_unsafe_versions_and_aliases() {
        assert!(validate_version("../../etc").is_err());
        assert!(validate_version("0.1.0").is_ok());
        assert!(validate_alias("-oProxyCommand=evil").is_err());
        assert!(validate_alias("host:path").is_err());
        assert!(validate_alias("cluster-a").is_ok());
    }

    #[test]
    fn manifest_lists_files_with_sizes_sorted() {
        let root = std::env::temp_dir().join(format!(
            "coperceive-static-manifest-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        write_static(&root);
        let manifest = manifest_for(&root).expect("manifest");
        assert!(manifest.contains("assets/app.js"));
        assert!(manifest.contains("index.html"));
        let lines: Vec<&str> = manifest.lines().collect();
        assert_eq!(lines[0].split(' ').nth(1), Some("assets/app.js"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_static_dir_without_a_manifest_sized_file_upload_is_still_complete() {
        // The unpacked static dir itself has no index -> static_bundle_dir errors.
        let root = std::env::temp_dir().join(format!(
            "coperceive-static-missing-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("root");
        let guard = EnvGuard {
            keys: vec!["COPERCEIVE_STATIC_DIR"],
        };
        std::env::set_var("COPERCEIVE_STATIC_DIR", &root);
        assert!(static_bundle_dir().is_err());
        drop(guard);
        let _ = fs::remove_dir_all(&root);
    }
}
