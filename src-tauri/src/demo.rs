// SPDX-License-Identifier: Apache-2.0
//! Synthetic demo scene management for first-run onboarding and offline exploration.
//!
//! Stored strictly in the app's persistent data directory under `demo/`.
//! Generated on demand by bundled Python via `coperceive demo create <dir>`.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::Instant;
use serde::{Deserialize, Serialize};

/// Current format version for the demo cache.
pub const DEMO_VERSION: &str = "1";

static DEMO_MUTEX: Mutex<()> = Mutex::new(());

/// The outcome of ensuring the demo scene exists.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DemoOutcome {
    /// Full path to the `demo-junction` scene directory.
    pub path: String,
    /// Total size in bytes of the demo cache.
    pub bytes: u64,
    /// Whether the scene was already cached (true) or freshly generated (false).
    pub cached: bool,
    /// Wall-clock elapsed seconds during generation (0.0 if cached).
    pub elapsed_seconds: f64,
}

/// Returns the path to the app's demo root directory (`<state_dir>/demo`).
pub fn demo_dir() -> PathBuf {
    let state_file = crate::platform::get_state_file_path();
    let state_parent = state_file
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    state_parent.join("demo")
}

/// Returns the path to the `demo-junction` scene directory (`<state_dir>/demo/scenes/demo-junction`).
pub fn demo_scene_path() -> PathBuf {
    demo_dir().join("scenes").join("demo-junction")
}

/// Returns true if the path is inside or equals the demo directory or scene path.
pub fn is_demo_path(path: &Path) -> bool {
    let dir = demo_dir();
    let scene = demo_scene_path();
    path == dir || path == scene || path.starts_with(&dir)
}

/// Computes the total byte size of a directory recursively.
pub fn dir_size_bytes(dir: &Path) -> u64 {
    if !dir.is_dir() {
        return 0;
    }
    let mut total: u64 = 0;
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                if let Ok(meta) = p.metadata() {
                    total = total.saturating_add(meta.len());
                }
            } else if p.is_dir() {
                total = total.saturating_add(dir_size_bytes(&p));
            }
        }
    }
    total
}

/// Validates that the demo scene cache is intact, complete, and on the right version.
pub fn is_demo_valid() -> bool {
    is_demo_valid_at(&demo_dir())
}

/// Validates demo scene cache at a given directory path.
pub fn is_demo_valid_at(root: &Path) -> bool {
    let version_file = root.join(".version");
    if !version_file.is_file() {
        return false;
    }
    if let Ok(content) = fs::read_to_string(&version_file) {
        if content.trim() != DEMO_VERSION {
            return false;
        }
    } else {
        return false;
    }

    let scene_dir = root.join("scenes").join("demo-junction");
    let manifest = scene_dir.join("manifest.json");
    let complete = scene_dir.join("COMPLETE.json");
    let points = scene_dir.join("points.bin");
    let run_json = root.join("predictions").join("demo").join("run.json");
    let pred_jsonl = root.join("predictions").join("demo").join("predictions.jsonl");

    if !manifest.is_file() || !complete.is_file() || !points.is_file() || !run_json.is_file() || !pred_jsonl.is_file() {
        return false;
    }

    // Verify manifest is valid JSON and scene_id is demo-junction
    if let Ok(manifest_text) = fs::read_to_string(&manifest) {
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&manifest_text) {
            if val.get("scene_id").and_then(|s| s.as_str()) != Some("demo-junction") {
                return false;
            }
        } else {
            return false;
        }
    } else {
        return false;
    }

    true
}

/// Deletes the demo cache directory completely, leaving user data untouched.
pub fn delete_demo() -> Result<(), String> {
    delete_demo_at(&demo_dir())
}

/// Deletes the demo cache directory at the given path.
pub fn delete_demo_at(root: &Path) -> Result<(), String> {
    let _lock = DEMO_MUTEX.lock().map_err(|e| e.to_string())?;
    if root.exists() {
        fs::remove_dir_all(root).map_err(|e| format!("Failed to delete demo directory {}: {e}", root.display()))?;
    }
    Ok(())
}

/// Ensures the demo scene is present and valid; generates it using bundled Python if missing or corrupt.
pub fn ensure_demo(python_bin: &Path) -> Result<DemoOutcome, String> {
    let root = demo_dir();
    ensure_demo_at(python_bin, &root)
}

/// Ensures the demo scene is present and valid at the given directory path.
pub fn ensure_demo_at(python_bin: &Path, root: &Path) -> Result<DemoOutcome, String> {
    let _lock = DEMO_MUTEX.lock().map_err(|e| e.to_string())?;

    let scene_path = root.join("scenes").join("demo-junction");

    if is_demo_valid_at(root) {
        let size = dir_size_bytes(root);
        return Ok(DemoOutcome {
            path: scene_path.to_string_lossy().into_owned(),
            bytes: size,
            cached: true,
            elapsed_seconds: 0.0,
        });
    }

    // Corrupt, outdated, or absent: clean up existing partial/stale directory
    if root.exists() {
        let _ = fs::remove_dir_all(root);
    }
    if let Some(parent) = root.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Failed to create parent directory {}: {e}", parent.display()))?;
    }

    let start = Instant::now();
    let root_str = root.to_string_lossy().into_owned();

    let output = Command::new(python_bin)
        .args([
            "-m",
            "coperceive",
            "demo",
            "create",
            &root_str,
            "--seed",
            "0",
        ])
        .output()
        .map_err(|e| format!("Failed to run demo generator ({}): {e}", python_bin.display()))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        // Clean up partial failure
        let _ = fs::remove_dir_all(root);
        return Err(format!(
            "Demo generation failed with exit code {:?}:\n{}\n{}",
            output.status.code(),
            stderr.trim(),
            stdout.trim()
        ));
    }

    // Write version marker
    fs::write(root.join(".version"), DEMO_VERSION)
        .map_err(|e| format!("Failed to write demo version marker: {e}"))?;

    if !is_demo_valid_at(root) {
        let _ = fs::remove_dir_all(root);
        return Err("Generated demo scene failed post-creation validation".into());
    }

    let elapsed = start.elapsed().as_secs_f64();
    let size = dir_size_bytes(root);

    Ok(DemoOutcome {
        path: scene_path.to_string_lossy().into_owned(),
        bytes: size,
        cached: false,
        elapsed_seconds: elapsed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_demo_dir_and_scene_path_resolution() {
        let dir = demo_dir();
        let scene = demo_scene_path();
        assert_eq!(scene, dir.join("scenes").join("demo-junction"));
        assert!(is_demo_path(&dir));
        assert!(is_demo_path(&scene));
        assert!(is_demo_path(&dir.join("scenes")));
        assert!(is_demo_path(&dir.join("predictions").join("demo")));
        assert!(!is_demo_path(Path::new("/tmp/unrelated")));
    }

    fn unique_temp_dir(prefix: &str) -> PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let seq = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "{prefix}-{}-{seq}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or_default()
        ))
    }

    #[test]
    fn test_demo_validity_and_cleanup() {
        let tmp = unique_temp_dir("coperceive-test-demo");
        let _ = fs::create_dir_all(&tmp);
        let root = tmp.join("demo");

        assert!(!is_demo_valid_at(&root));

        // Create incomplete layout
        let scene_dir = root.join("scenes").join("demo-junction");
        fs::create_dir_all(&scene_dir).unwrap();
        assert!(!is_demo_valid_at(&root));

        // Add version but still missing files
        fs::write(root.join(".version"), DEMO_VERSION).unwrap();
        assert!(!is_demo_valid_at(&root));

        // Add manifest, complete, points, run.json, predictions.jsonl
        fs::write(scene_dir.join("manifest.json"), r#"{"scene_id":"demo-junction"}"#).unwrap();
        fs::write(scene_dir.join("COMPLETE.json"), r#"{}"#).unwrap();
        fs::write(scene_dir.join("points.bin"), b"1234").unwrap();

        let pred_dir = root.join("predictions").join("demo");
        fs::create_dir_all(&pred_dir).unwrap();
        fs::write(pred_dir.join("run.json"), r#"{}"#).unwrap();
        fs::write(pred_dir.join("predictions.jsonl"), "line\n").unwrap();

        assert!(is_demo_valid_at(&root));

        // Corrupt version
        fs::write(root.join(".version"), "0").unwrap();
        assert!(!is_demo_valid_at(&root));
        fs::write(root.join(".version"), DEMO_VERSION).unwrap();
        assert!(is_demo_valid_at(&root));

        // Delete demo
        delete_demo_at(&root).unwrap();
        assert!(!root.exists());
        assert!(!is_demo_valid_at(&root));
    }
}
