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

fn fnv1a(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

fn hash_file(path: &Path) -> Result<u64, String> {
    let bytes = fs::read(path).map_err(|error| format!("Could not read {}: {error}", path.display()))?;
    Ok(fnv1a(&bytes))
}

fn collect_files(
    root: &Path,
    dir: &Path,
    out: &mut Vec<(String, u64, u64)>,
) -> Result<(), String> {
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
            let hash = hash_file(&path)?;
            out.push((relative, size, hash));
        }
    }
    Ok(())
}

/// A deterministic `hash size relative/path` line per file, sorted by path.
///
/// The content hash (FNV-1a, local only) is what makes the cache skip safe: a
/// same-size but different file re-uploads instead of being served.
pub fn manifest_for(root: &Path) -> Result<String, String> {
    let mut files: Vec<(String, u64, u64)> = Vec::new();
    collect_files(root, root, &mut files)?;
    files.sort_by(|left, right| left.0.cmp(&right.0));
    let mut text = String::new();
    for (relative, size, hash) in files {
        text.push_str(&format!("{hash:016x} {size} {relative}\n"));
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
    scp: &str,
) -> Result<Option<String>, String> {
    let _ = fs::remove_file(destination);
    let output = Command::new(scp)
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

fn upload_static(alias: &str, local: &Path, remote_rel: &str, scp: &str) -> Result<(), String> {
    let source = format!("{}/.", local.display());
    let destination = format!("{alias}:{remote_rel}");
    let output = Command::new(scp)
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
    let local = static_bundle_dir()?;
    prepare_remote_static_with(alias, version, &local, &ssh_bin(), &scp_bin())
}

/// Same, with the transport and the local bundle injected (unit tests).
fn prepare_remote_static_with(
    alias: &str,
    version: &str,
    local: &Path,
    ssh: &str,
    scp: &str,
) -> Result<String, String> {
    validate_alias(alias)?;
    let version = validate_version(version)?;
    if !local.join("index.html").is_file() {
        return Err(format!(
            "The built viewer bundle at {} has no index.html. Run scripts/build_web_static.sh.",
            local.display()
        ));
    }
    let manifest = manifest_for(local)?;
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
    let existing = fetch_remote_manifest(alias, &remote_rel, &temporary, scp)?;
    let _ = fs::remove_file(&temporary);
    if existing.as_deref() == Some(manifest.as_str()) {
        return Ok(remote_display);
    }

    let runner = SshRunner::new(ssh, SshTarget::alias(alias));
    let version_quoted = shell_quote(&version);
    let mkdir = format!(
        "sh -c 'mkdir -p -- \"$HOME/{STATIC_REL}/$1\"' sh {version_quoted}"
    );
    run_ssh(&runner, &mkdir, None)?;
    upload_static(alias, local, &remote_rel, scp)?;
    let write_manifest = format!(
        "sh -c 'cat > \"$HOME/{STATIC_REL}/$1/{MANIFEST_NAME}\"' sh {version_quoted}"
    );
    run_ssh(&runner, &write_manifest, Some(&manifest))?;
    Ok(remote_display)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn write_script(path: &Path, body: &str) {
        fs::write(path, body).expect("write script");
        let mut perms = fs::metadata(path).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(path, perms).expect("permissions");
    }

    fn write_static(root: &Path) {
        let assets = root.join("assets");
        fs::create_dir_all(&assets).expect("assets");
        fs::write(
            root.join("index.html"),
            "<script src=\"./assets/app.js\"></script>\n",
        )
        .expect("index");
        fs::write(assets.join("app.js"), "console.log('viewer')\n").expect("js");
    }

    fn fake_ssh_script(remote_root: &Path, log: &Path) -> String {
        format!(
            r#"#!/usr/bin/env bash
set -u
remote="${{!#}}"
printf '%s\n' "$remote" >> "{log}"
version="${{remote##* }}"; version="${{version#\'}}"; version="${{version%\'}}"
case "$remote" in
  *"mkdir -p -- \"\$HOME/.cache/coperceive/static/\$1\""*)
      mkdir -p "{root}/home/.cache/coperceive/static/$version"
      exit 0
      ;;
  *"cat > \"\$HOME/.cache/coperceive/static/\$1/.coperceive-static-manifest\""*)
      mkdir -p "{root}/home/.cache/coperceive/static/$version"
      cat > "{root}/home/.cache/coperceive/static/$version/.coperceive-static-manifest"
      exit 0
      ;;
esac
echo "fake ssh: unsupported: $remote" >&2
exit 1
"#,
            log = log.display(),
            root = remote_root.display(),
        )
    }

    fn fake_scp_script(remote_root: &Path, log: &Path) -> String {
        format!(
            r#"#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "{log}"
src="${{@: -2:1}}"
dst="${{@: -1:1}}"
case "$src" in
  *:*)
      rel="${{src#*:}}"
      cp "{root}/home/$rel" "$dst"
      ;;
  *)
      rel="${{dst#*:}}"
      mkdir -p "{root}/home/$rel"
      cp -R "$src" "{root}/home/$rel/"
      ;;
esac
"#,
            log = log.display(),
            root = remote_root.display(),
        )
    }

    fn temp_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "coperceive-static-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ))
    }

    fn uploads(scp_log: &Path) -> usize {
        fs::read_to_string(scp_log)
            .unwrap_or_default()
            .lines()
            .filter(|line| line.starts_with("-r "))
            .count()
    }

    fn ssh_writes(ssh_log: &Path) -> usize {
        fs::read_to_string(ssh_log).unwrap_or_default().lines().count()
    }

    #[test]
    fn uploads_once_then_skips_a_complete_cache() {
        let (root, local, remote, ssh, scp, ssh_log, scp_log) = setup_with_logs("complete");
        let first = prepare_remote_static_with("cluster-a", "0.1.0", &local, ssh.to_str().unwrap(), scp.to_str().unwrap())
            .expect("first upload");
        assert_eq!(first, "~/.cache/coperceive/static/0.1.0");
        let cached = remote.join("home/.cache/coperceive/static/0.1.0");
        assert!(cached.join("index.html").is_file());
        assert!(cached.join("assets/app.js").is_file());
        assert!(cached.join(MANIFEST_NAME).is_file());
        assert_eq!(uploads(&scp_log), 1, "first start uploads once");
        assert_eq!(ssh_writes(&ssh_log), 2, "mkdir + manifest");

        let second = prepare_remote_static_with("cluster-a", "0.1.0", &local, ssh.to_str().unwrap(), scp.to_str().unwrap())
            .expect("second start");
        assert_eq!(second, first);
        assert_eq!(uploads(&scp_log), 1, "a complete cache is not uploaded again");
        assert_eq!(ssh_writes(&ssh_log), 2, "a complete cache needs no ssh writes");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_partial_cache_is_uploaded_again() {
        let (root, local, remote, ssh, scp, _ssh_log, scp_log) = setup_with_logs("partial");
        prepare_remote_static_with("cluster-a", "0.1.0", &local, ssh.to_str().unwrap(), scp.to_str().unwrap())
            .expect("upload");
        let cached = remote.join("home/.cache/coperceive/static/0.1.0");
        fs::write(cached.join("assets/app.js"), "console.log('stale')\n").expect("stale");
        prepare_remote_static_with("cluster-a", "0.1.0", &local, ssh.to_str().unwrap(), scp.to_str().unwrap())
            .expect("re-upload");
        assert_eq!(uploads(&scp_log), 2, "a stale cache is uploaded again");
        let current = fs::read_to_string(cached.join("assets/app.js")).expect("js");
        assert!(current.contains("viewer"), "{current}");
        let _ = fs::remove_dir_all(&root);
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
        let root = temp_root("manifest");
        write_static(&root);
        let manifest = manifest_for(&root).expect("manifest");
        assert!(manifest.contains("assets/app.js"));
        assert!(manifest.contains("index.html"));
        let lines: Vec<&str> = manifest.lines().collect();
        assert_eq!(lines[0].split(' ').nth(2), Some("assets/app.js"));
        let _ = fs::remove_dir_all(&root);
    }

    fn setup_with_logs(
        label: &str,
    ) -> (PathBuf, PathBuf, PathBuf, PathBuf, PathBuf, PathBuf, PathBuf) {
        let root = temp_root(label);
        let local = root.join("static");
        let remote = root.join("remote");
        fs::create_dir_all(&remote).expect("remote root");
        write_static(&local);
        let ssh = root.join("fake_ssh.sh");
        let scp = root.join("fake_scp.sh");
        let ssh_log = root.join("ssh.log");
        let scp_log = root.join("scp.log");
        write_script(&ssh, &fake_ssh_script(&remote, &ssh_log));
        write_script(&scp, &fake_scp_script(&remote, &scp_log));
        (root, local, remote, ssh, scp, ssh_log, scp_log)
    }
}
