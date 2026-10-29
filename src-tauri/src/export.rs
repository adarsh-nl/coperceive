// SPDX-License-Identifier: Apache-2.0
//! Desktop Export Service
//!
//! Handles safe writing and copying of exported files (stills, sequence clips,
//! bird's-eye figures, and .rrd recordings) to user-selected destination paths.

use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

/// Maximum allowed byte size for direct in-memory payloads (10 MB).
pub const MAX_EXPORT_BYTES: usize = 10 * 1024 * 1024;

/// Manages pending export destinations bound to user dialog confirmations,
/// and tracks the destination of the last successful export for Finder reveal.
///
/// Ensures destination paths are one-shot and cannot be reused or called
/// without a prior user confirmation via `pick_save_path`.
#[derive(Debug, Default)]
pub struct PendingExportDestination {
    path: Mutex<Option<PathBuf>>,
    last_saved: Mutex<Option<PathBuf>>,
}

impl PendingExportDestination {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(&self, path: Option<PathBuf>) {
        let mut guard = self.path.lock().expect("pending export lock poisoned");
        *guard = path;
    }

    pub fn take(&self) -> Result<PathBuf, String> {
        let mut guard = self.path.lock().expect("pending export lock poisoned");
        guard.take().ok_or_else(|| {
            "No pending export destination; pick_save_path must be called first".to_string()
        })
    }

    pub fn set_last_saved(&self, path: PathBuf) {
        let mut guard = self.last_saved.lock().expect("last saved export lock poisoned");
        *guard = Some(path);
    }

    pub fn last_saved(&self) -> Result<PathBuf, String> {
        let guard = self.last_saved.lock().expect("last saved export lock poisoned");
        guard.clone().ok_or_else(|| {
            "No exported file has been saved yet".to_string()
        })
    }
}

/// Writes or copies an exported file to a destination path.
///
/// Security and validation guarantees:
/// 1. Destination directory hierarchy is automatically created.
/// 2. If `bytes` is provided, ensures it does not exceed `MAX_EXPORT_BYTES` and writes directly.
/// 3. If `source_relative` is provided, ensures:
///    - It is not an absolute path.
///    - It contains no parent-directory (`..`) traversal components.
///    - It resolves strictly within the canonical data root.
///    - Copies directly from the data root without loading into memory.
pub fn write_export_file(
    source_root: Option<&Path>,
    source_relative: Option<&str>,
    destination_path: &Path,
    bytes: Option<&[u8]>,
) -> Result<(), String> {
    if let Some(data) = bytes {
        if data.len() > MAX_EXPORT_BYTES {
            return Err(format!(
                "Export payload exceeds maximum allowed size of {MAX_EXPORT_BYTES} bytes (got {} bytes)",
                data.len()
            ));
        }
        if let Some(parent) = destination_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        std::fs::write(destination_path, data)
            .map_err(|e| format!("Failed to write export file: {e}"))?;
        return Ok(());
    }

    let rel_str = source_relative
        .ok_or_else(|| "Neither source path nor byte payload was provided for export".to_string())?;

    let root = source_root
        .ok_or_else(|| "No data root set; cannot resolve exported source path".to_string())?;

    let rel_path = Path::new(rel_str);
    if rel_path.is_absolute() {
        return Err(format!("Absolute source path rejected: {rel_str}"));
    }

    for comp in rel_path.components() {
        if comp == Component::ParentDir {
            return Err(format!("Path traversal rejected in source path: {rel_str}"));
        }
    }

    let root_canon = root
        .canonicalize()
        .map_err(|e| format!("Failed to canonicalize data root {}: {e}", root.display()))?;

    let src = root.join(rel_path);
    if !src.is_file() {
        return Err(format!("Could not locate exported file: {}", src.display()));
    }

    let src_canon = src
        .canonicalize()
        .map_err(|e| format!("Failed to canonicalize source {}: {e}", src.display()))?;

    if !src_canon.starts_with(&root_canon) {
        return Err(format!("Source path escapes data root: {rel_str}"));
    }

    if let Some(parent) = destination_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    std::fs::copy(&src_canon, destination_path)
        .map_err(|e| format!("Failed to copy export file: {e}"))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_test_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("coperceive_export_test_{tag}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn traversal_rejected_in_source_path() {
        let dir = temp_test_dir("traversal");
        let dest = dir.join("out.png");

        let err1 = write_export_file(Some(&dir), Some("../secret.txt"), &dest, None);
        assert!(err1.is_err());
        assert!(err1.unwrap_err().contains("Path traversal rejected"));

        let err2 = write_export_file(Some(&dir), Some("sub/../../secret.txt"), &dest, None);
        assert!(err2.is_err());
        assert!(err2.unwrap_err().contains("Path traversal rejected"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn absolute_source_rejected() {
        let dir = temp_test_dir("absolute");
        let dest = dir.join("out.png");

        let err = write_export_file(Some(&dir), Some("/etc/passwd"), &dest, None);
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("Absolute source path rejected"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_without_a_prior_pick_rejected() {
        let pending = PendingExportDestination::new();
        let res = pending.take();
        assert!(res.is_err());
        assert!(res.unwrap_err().contains("No pending export destination; pick_save_path must be called first"));
    }

    #[test]
    fn second_write_after_one_use_rejected() {
        let pending = PendingExportDestination::new();
        pending.set(Some(PathBuf::from("/tmp/saved_output.png")));

        let first = pending.take();
        assert!(first.is_ok());
        assert_eq!(first.unwrap(), PathBuf::from("/tmp/saved_output.png"));

        // Second call must fail
        let second = pending.take();
        assert!(second.is_err());
        assert!(second.unwrap_err().contains("No pending export destination; pick_save_path must be called first"));
    }

    #[test]
    fn bytes_payload_written_and_parents_created() {
        let dir = temp_test_dir("bytes");
        let dest = dir.join("nested").join("deep").join("still.png");
        let payload = b"\x89PNG\r\n\x1a\nfake_image_bytes";

        let res = write_export_file(None, None, &dest, Some(payload));
        assert!(res.is_ok());
        assert_eq!(fs::read(&dest).unwrap(), payload);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn bytes_payload_over_limit_rejected() {
        let dir = temp_test_dir("limit");
        let dest = dir.join("out.bin");
        let oversized = vec![0u8; MAX_EXPORT_BYTES + 1];

        let err = write_export_file(None, None, &dest, Some(&oversized));
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("exceeds maximum allowed size"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn copy_from_root_succeeds() {
        let dir = temp_test_dir("copy_ok");
        let root = dir.join("root");
        let exports_dir = root.join("exports");
        fs::create_dir_all(&exports_dir).unwrap();

        let src_file = exports_dir.join("synth_001_clip.mp4");
        fs::write(&src_file, b"fake_mp4_contents").unwrap();

        let dest = dir.join("saved_exports").join("custom_clip.mp4");
        let res = write_export_file(
            Some(&root),
            Some("exports/synth_001_clip.mp4"),
            &dest,
            None,
        );
        assert!(res.is_ok());
        assert_eq!(fs::read(&dest).unwrap(), b"fake_mp4_contents");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_file_under_root_rejected() {
        let dir = temp_test_dir("missing");
        let root = dir.join("root");
        fs::create_dir_all(&root).unwrap();
        let dest = dir.join("out.mp4");

        let err = write_export_file(
            Some(&root),
            Some("exports/nonexistent.mp4"),
            &dest,
            None,
        );
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("Could not locate exported file"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_source_or_bytes_rejected() {
        let dir = temp_test_dir("empty");
        let dest = dir.join("out.png");

        let err = write_export_file(Some(&dir), None, &dest, None);
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("Neither source path nor byte payload was provided"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn last_saved_tracks_and_errors_when_empty() {
        let pending = PendingExportDestination::new();
        assert!(pending.last_saved().is_err());
        assert_eq!(pending.last_saved().unwrap_err(), "No exported file has been saved yet");

        let file = PathBuf::from("/path/to/exported.png");
        pending.set_last_saved(file.clone());
        assert_eq!(pending.last_saved().unwrap(), file);
    }
}
