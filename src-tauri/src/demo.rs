// SPDX-License-Identifier: Apache-2.0
//! Synthetic demo scene management for first-run onboarding and offline exploration.
//!
//! Stored strictly in the app's persistent data directory under `demo/`.
//! Generated on demand by bundled Python via `coperceive demo create <dir>`.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use serde::{Deserialize, Serialize};

/// Current format version for the demo cache.
pub const DEMO_VERSION: &str = "1";

static DEMO_MUTEX: Mutex<()> = Mutex::new(());
static DEMO_GENERATING_COUNT: AtomicUsize = AtomicUsize::new(0);
static CANCEL_DEMO: AtomicBool = AtomicBool::new(false);
static ACTIVE_DEMO_PID: Mutex<Option<u32>> = Mutex::new(None);

struct GeneratingGuard<'a>(&'a AtomicUsize);

impl<'a> GeneratingGuard<'a> {
    fn new(count: &'a AtomicUsize) -> Self {
        count.fetch_add(1, Ordering::SeqCst);
        Self(count)
    }
}

impl Drop for GeneratingGuard<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

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
    /// Path to the Python interpreter used to generate or validate the demo.
    pub python_path: String,
}

/// Validates that a state directory is safe to operate under (non-empty, not `.`, not `/`, not a symlink).
fn validate_state_dir(state_dir: &Path) -> Result<PathBuf, String> {
    if state_dir.as_os_str().is_empty()
        || state_dir == Path::new(".")
        || state_dir == Path::new("/")
        || state_dir.parent().is_none()
    {
        return Err(format!(
            "Refusing demo operation: unsafe state directory '{}'",
            state_dir.display()
        ));
    }
    if let Ok(meta) = fs::symlink_metadata(state_dir) {
        if meta.file_type().is_symlink() {
            return Err(format!(
                "Refusing demo operation: state directory '{}' is a symlink",
                state_dir.display()
            ));
        }
    }
    Ok(state_dir.to_path_buf())
}

/// Resolves and validates the application's state directory.
pub fn resolved_state_dir() -> Result<PathBuf, String> {
    let state_file = crate::platform::get_state_file_path();
    let parent = state_file
        .parent()
        .ok_or_else(|| "State file path has no parent directory".to_string())?;
    validate_state_dir(parent)
}

/// Returns the path to the app's demo root directory (`<state_dir>/demo`).
pub fn demo_dir() -> Result<PathBuf, String> {
    Ok(resolved_state_dir()?.join("demo"))
}

/// Validates that `root` is safe to delete as a demo directory under `state_dir`.
/// Refuses unless `root` ends in `demo`, sits directly under `state_dir`, and is not
/// `state_dir` itself, a symlink, or an empty path.
pub fn validate_demo_target_in_state_dir(root: &Path, state_dir: &Path) -> Result<(), String> {
    if root.as_os_str().is_empty() {
        return Err("Refusing to delete demo: target path is empty".into());
    }
    validate_state_dir(state_dir)?;
    if root == state_dir {
        return Err("Refusing to delete demo: target path is the state directory itself".into());
    }
    if root.file_name().and_then(|n| n.to_str()) != Some("demo") {
        return Err(format!(
            "Refusing to delete demo: '{}' does not end in 'demo'",
            root.display()
        ));
    }
    if root.parent() != Some(state_dir) {
        return Err(format!(
            "Refusing to delete demo: '{}' is not directly under state directory '{}'",
            root.display(),
            state_dir.display()
        ));
    }
    if let Ok(meta) = fs::symlink_metadata(root) {
        if meta.file_type().is_symlink() {
            return Err(format!(
                "Refusing to delete demo: '{}' is a symlink",
                root.display()
            ));
        }
        if !meta.file_type().is_dir() {
            return Err(format!(
                "Refusing to delete demo: '{}' is not a directory",
                root.display()
            ));
        }
        if let (Ok(canon_state), Ok(canon_root)) =
            (fs::canonicalize(state_dir), fs::canonicalize(root))
        {
            if canon_root == canon_state {
                return Err(
                    "Refusing to delete demo: canonical target is the state directory itself"
                        .into(),
                );
            }
            if canon_root.file_name().and_then(|n| n.to_str()) != Some("demo")
                || canon_root.parent() != Some(canon_state.as_path())
            {
                return Err(format!(
                    "Refusing to delete demo: canonical path '{}' is not directly under canonical state directory '{}'",
                    canon_root.display(),
                    canon_state.display()
                ));
            }
        }
    }
    Ok(())
}

/// Returns the path to the `demo-junction` scene directory (`<state_dir>/demo/scenes/demo-junction`).
pub fn demo_scene_path() -> Result<PathBuf, String> {
    Ok(demo_dir()?.join("scenes").join("demo-junction"))
}

/// Returns true if the path is inside or equals the demo directory or scene path.
pub fn is_demo_path(path: &Path) -> bool {
    let Ok(dir) = demo_dir() else {
        return false;
    };
    let scene = dir.join("scenes").join("demo-junction");
    path == dir || path == scene || path.starts_with(&dir)
}

/// Returns `<demo_dir>/predictions` only when `path` is inside the demo directory and `predictions/` exists.
pub fn demo_predictions_for_path(path: &Path) -> Option<PathBuf> {
    if !is_demo_path(path) {
        return None;
    }
    let dir = demo_dir().ok()?;
    let preds = dir.join("predictions");
    preds.is_dir().then_some(preds)
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
    demo_dir().map(|dir| is_demo_valid_at(&dir)).unwrap_or(false)
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
    let dir = demo_dir()?;
    delete_demo_at(&dir)
}

/// Deletes the demo cache directory at the given path after verifying it sits directly
/// under the resolved state directory and passes all deletion safety checks.
pub fn delete_demo_at(root: &Path) -> Result<(), String> {
    let state_dir = resolved_state_dir()?;
    delete_demo_in_state_dir(root, &state_dir)
}

/// Deletes the demo cache directory at `root` after verifying it sits directly under `state_dir`.
pub fn delete_demo_in_state_dir(root: &Path, state_dir: &Path) -> Result<(), String> {
    delete_demo_in_state_dir_with(root, state_dir, &DEMO_MUTEX, &DEMO_GENERATING_COUNT)
}

fn delete_demo_in_state_dir_with(
    root: &Path,
    state_dir: &Path,
    mutex: &Mutex<()>,
    generating_count: &AtomicUsize,
) -> Result<(), String> {
    if generating_count.load(Ordering::SeqCst) > 0 {
        return Err("Cannot delete demo while demo is being generated".to_string());
    }
    let _lock = mutex.lock().map_err(|e| e.to_string())?;
    if generating_count.load(Ordering::SeqCst) > 0 {
        return Err("Cannot delete demo while demo is being generated".to_string());
    }
    validate_demo_target_in_state_dir(root, state_dir)?;
    if root.exists() {
        fs::remove_dir_all(root)
            .map_err(|e| format!("Failed to delete demo directory {}: {e}", root.display()))?;
    }
    Ok(())
}

/// Cancels an in-progress demo scene generation if one is running.
pub fn cancel_demo() -> Result<(), String> {
    CANCEL_DEMO.store(true, Ordering::SeqCst);
    if let Ok(mut guard) = ACTIVE_DEMO_PID.lock() {
        if let Some(pid) = guard.take() {
            let _ = crate::platform::terminate_process(pid);
        }
    }
    Ok(())
}

/// Inspects the demo directory during generation to report a human-readable stage.
fn current_demo_stage(root: &Path) -> &'static str {
    let scene_dir = root.join("scenes").join("demo-junction");
    if scene_dir.join("COMPLETE.json").exists() {
        "Validating scene cache…"
    } else if root.join("predictions").join("demo").join("predictions.jsonl").exists() {
        "Scoring synthetic predictions…"
    } else if scene_dir.join("points.bin").exists() {
        "Packing LiDAR frames & camera strip…"
    } else if root.join("raw").exists() {
        "Synthesising LiDAR & multi-agent cameras…"
    } else {
        "Starting bundled Python generator…"
    }
}

fn read_log_tail(log_path: &Path, max_lines: usize) -> String {
    match fs::read_to_string(log_path) {
        Ok(content) => {
            let lines: Vec<&str> = content.lines().collect();
            let start = lines.len().saturating_sub(max_lines);
            lines[start..].join("\n").trim().to_string()
        }
        Err(e) => format!("<failed to read log {}: {e}>", log_path.display()),
    }
}

/// Ensures the demo scene is present and valid; generates it using bundled Python if missing or corrupt.
pub fn ensure_demo(python_bin: &Path) -> Result<DemoOutcome, String> {
    let root = demo_dir()?;
    ensure_demo_at(python_bin, &root)
}

/// Ensures the demo scene is present and valid, invoking `on_progress(stage, elapsed_seconds)` periodically.
pub fn ensure_demo_with_progress<F: FnMut(&str, f64)>(
    python_bin: &Path,
    on_progress: F,
) -> Result<DemoOutcome, String> {
    let root = demo_dir()?;
    ensure_demo_at_with_progress(python_bin, &root, on_progress)
}

/// Ensures the demo scene is present and valid at the given directory path.
pub fn ensure_demo_at(python_bin: &Path, root: &Path) -> Result<DemoOutcome, String> {
    ensure_demo_at_with_progress(python_bin, root, |_, _| {})
}

/// Ensures the demo scene is present and valid at the given directory path with progress callbacks.
pub fn ensure_demo_at_with_progress<F: FnMut(&str, f64)>(
    python_bin: &Path,
    root: &Path,
    on_progress: F,
) -> Result<DemoOutcome, String> {
    let state_dir = resolved_state_dir()?;
    ensure_demo_in_state_dir_with_progress(python_bin, root, &state_dir, on_progress)
}

/// Ensures the demo scene is present and valid at `root` under `state_dir` with progress callbacks.
pub fn ensure_demo_in_state_dir_with_progress<F: FnMut(&str, f64)>(
    python_bin: &Path,
    root: &Path,
    state_dir: &Path,
    mut on_progress: F,
) -> Result<DemoOutcome, String> {
    let _generating = GeneratingGuard::new(&DEMO_GENERATING_COUNT);
    let _lock = DEMO_MUTEX.lock().map_err(|e| e.to_string())?;
    CANCEL_DEMO.store(false, Ordering::SeqCst);

    fs::create_dir_all(state_dir)
        .map_err(|e| format!("Failed to create state directory {}: {e}", state_dir.display()))?;
    validate_demo_target_in_state_dir(root, state_dir)?;

    let scene_path = root.join("scenes").join("demo-junction");
    let python_str = python_bin.to_string_lossy().into_owned();

    if is_demo_valid_at(root) {
        let size = dir_size_bytes(root);
        return Ok(DemoOutcome {
            path: scene_path.to_string_lossy().into_owned(),
            bytes: size,
            cached: true,
            elapsed_seconds: 0.0,
            python_path: python_str,
        });
    }

    // Corrupt, outdated, or absent: clean up existing partial/stale directory
    if root.exists() {
        let _ = fs::remove_dir_all(root);
    }

    let start = Instant::now();
    let root_str = root.to_string_lossy().into_owned();

    on_progress("Starting bundled Python generator…", 0.0);

    let log_path = state_dir.join("demo-generate.log");
    let log_file = fs::File::create(&log_path)
        .map_err(|e| format!("Failed to create demo generator log {}: {e}", log_path.display()))?;
    let log_file_err = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone demo generator log handle: {e}"))?;

    let mut child = Command::new(python_bin)
        .args([
            "-m",
            "coperceive",
            "demo",
            "create",
            &root_str,
            "--seed",
            "0",
        ])
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(log_file_err))
        .spawn()
        .map_err(|e| format!("Failed to run demo generator ({}): {e}", python_bin.display()))?;

    if let Ok(mut guard) = ACTIVE_DEMO_PID.lock() {
        *guard = Some(child.id());
    }

    let status = loop {
        if CANCEL_DEMO.load(Ordering::SeqCst) {
            let _ = crate::platform::terminate_process(child.id());
            let _ = child.kill();
            let _ = child.wait();
            if let Ok(mut guard) = ACTIVE_DEMO_PID.lock() {
                *guard = None;
            }
            if validate_demo_target_in_state_dir(root, state_dir).is_ok() && root.exists() {
                let _ = fs::remove_dir_all(root);
            }
            return Err("Demo generation cancelled".into());
        }

        match child.try_wait() {
            Ok(Some(status)) => {
                if let Ok(mut guard) = ACTIVE_DEMO_PID.lock() {
                    *guard = None;
                }
                break status;
            }
            Ok(None) => {
                let elapsed = start.elapsed().as_secs_f64();
                let stage = current_demo_stage(root);
                on_progress(stage, elapsed);
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(e) => {
                if let Ok(mut guard) = ACTIVE_DEMO_PID.lock() {
                    *guard = None;
                }
                return Err(format!("Failed while waiting for demo generator: {e}"));
            }
        }
    };

    if !status.success() {
        // Clean up partial failure
        if validate_demo_target_in_state_dir(root, state_dir).is_ok() && root.exists() {
            let _ = fs::remove_dir_all(root);
        }
        if CANCEL_DEMO.load(Ordering::SeqCst) {
            return Err("Demo generation cancelled".into());
        }
        let log_tail = read_log_tail(&log_path, 40);
        return Err(format!(
            "Demo generation failed with exit code {:?} (log: {}):\n{}",
            status.code(),
            log_path.display(),
            log_tail
        ));
    }

    // Write version marker
    fs::write(root.join(".version"), DEMO_VERSION)
        .map_err(|e| format!("Failed to write demo version marker: {e}"))?;

    if !is_demo_valid_at(root) {
        if validate_demo_target_in_state_dir(root, state_dir).is_ok() && root.exists() {
            let _ = fs::remove_dir_all(root);
        }
        return Err("Generated demo scene failed post-creation validation".into());
    }

    let elapsed = start.elapsed().as_secs_f64();
    let size = dir_size_bytes(root);

    Ok(DemoOutcome {
        path: scene_path.to_string_lossy().into_owned(),
        bytes: size,
        cached: false,
        elapsed_seconds: elapsed,
        python_path: python_str,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_demo_dir_and_scene_path_resolution() {
        let dir = demo_dir().unwrap();
        let scene = demo_scene_path().unwrap();
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

        // Delete demo outside resolved state dir via delete_demo_at must be refused
        assert!(delete_demo_at(&root).is_err());
        assert!(root.exists());

        // Delete demo under its explicit state_dir succeeds
        let mutex = Mutex::new(());
        let generating = AtomicUsize::new(0);
        delete_demo_in_state_dir_with(&root, &tmp, &mutex, &generating).unwrap();
        assert!(!root.exists());
        assert!(!is_demo_valid_at(&root));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_delete_demo_safety_guards() {
        let tmp = unique_temp_dir("coperceive-test-demo-safety");
        let other = unique_temp_dir("coperceive-test-demo-other");
        let mutex = Mutex::new(());
        let generating = AtomicUsize::new(0);
        fs::create_dir_all(&tmp).unwrap();
        fs::create_dir_all(&other).unwrap();

        // 1. Refuse empty target or empty state_dir
        assert!(delete_demo_at(Path::new("")).is_err());
        assert!(delete_demo_in_state_dir_with(Path::new(""), &tmp, &mutex, &generating).is_err());
        assert!(delete_demo_in_state_dir_with(&tmp.join("demo"), Path::new(""), &mutex, &generating).is_err());
        assert!(delete_demo_in_state_dir_with(Path::new("demo"), Path::new("."), &mutex, &generating).is_err());
        assert!(delete_demo_in_state_dir_with(Path::new("/demo"), Path::new("/"), &mutex, &generating).is_err());

        // 2. Refuse state_dir itself
        assert!(delete_demo_in_state_dir_with(&tmp, &tmp, &mutex, &generating).is_err());
        if let Ok(real_state) = resolved_state_dir() {
            assert!(delete_demo_at(&real_state).is_err());
        }

        // 3. Refuse directory that does not end in "demo"
        let wrong_name = tmp.join("user-datasets");
        fs::create_dir_all(&wrong_name).unwrap();
        fs::write(wrong_name.join("important.txt"), "keep").unwrap();
        assert!(delete_demo_in_state_dir_with(&wrong_name, &tmp, &mutex, &generating).is_err());
        assert!(delete_demo_at(&wrong_name).is_err());
        assert!(wrong_name.join("important.txt").exists());

        // 4. Refuse directory named "demo" that is not directly under state_dir
        let other_demo = other.join("demo");
        fs::create_dir_all(&other_demo).unwrap();
        fs::write(other_demo.join("keep.txt"), "keep").unwrap();
        assert!(delete_demo_in_state_dir_with(&other_demo, &tmp, &mutex, &generating).is_err());
        assert!(delete_demo_at(&other_demo).is_err());
        assert!(other_demo.join("keep.txt").exists());

        let nested_demo = tmp.join("nested").join("demo");
        fs::create_dir_all(&nested_demo).unwrap();
        assert!(delete_demo_in_state_dir_with(&nested_demo, &tmp, &mutex, &generating).is_err());
        assert!(nested_demo.exists());

        // 5. Refuse symlink at <state_dir>/demo pointing elsewhere
        #[cfg(unix)]
        {
            let symlink_demo = tmp.join("demo");
            std::os::unix::fs::symlink(&other_demo, &symlink_demo).unwrap();
            assert!(delete_demo_in_state_dir_with(&symlink_demo, &tmp, &mutex, &generating).is_err());
            assert!(other_demo.join("keep.txt").exists());
            fs::remove_file(&symlink_demo).unwrap();
        }

        // 6. Valid <state_dir>/demo directory is deleted cleanly without touching siblings
        let valid_demo = tmp.join("demo");
        fs::create_dir_all(&valid_demo).unwrap();
        fs::write(valid_demo.join("marker.txt"), "demo").unwrap();
        delete_demo_in_state_dir_with(&valid_demo, &tmp, &mutex, &generating).unwrap();
        assert!(!valid_demo.exists());
        assert!(wrong_name.join("important.txt").exists());

        let _ = fs::remove_dir_all(&tmp);
        let _ = fs::remove_dir_all(&other);
    }

    #[test]
    fn test_delete_refused_while_generating() {
        let tmp = unique_temp_dir("coperceive-test-demo-gen");
        let valid_demo = tmp.join("demo");
        fs::create_dir_all(&valid_demo).unwrap();
        let mutex = Mutex::new(());
        let generating = AtomicUsize::new(1);
        let err = delete_demo_in_state_dir_with(&valid_demo, &tmp, &mutex, &generating).unwrap_err();
        assert!(err.contains("being generated"), "{err}");
        assert!(valid_demo.exists());
        let _ = fs::remove_dir_all(&tmp);
    }
}
