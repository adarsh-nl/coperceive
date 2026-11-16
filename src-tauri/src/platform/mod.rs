// SPDX-License-Identifier: Apache-2.0
//! Platform-specific operations for Coperceive Desktop.

use std::path::{Path, PathBuf};

use crate::remote_static::{default_resource_dir_for_exe, OsLayout};

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
pub use macos::*;

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::*;

#[cfg(target_os = "windows")]
mod windows;
#[cfg(target_os = "windows")]
pub use windows::*;

fn push_unique_dir(dirs: &mut Vec<PathBuf>, dir: PathBuf) {
    if !dirs.iter().any(|existing| existing == &dir) {
        dirs.push(dir);
    }
}

/// Returns candidate paths for the bundled Python interpreter inside a resource
/// directory for a given OS bundle layout.
pub fn resolve_python_candidates_for_layout(res_dir: &Path, layout: OsLayout) -> Vec<PathBuf> {
    match layout {
        OsLayout::Windows => vec![
            res_dir.join("resources").join("python").join("python.exe"),
            res_dir.join("python").join("python.exe"),
            res_dir.join("python").join("install").join("python.exe"),
            res_dir.join("resources").join("python").join("bin").join("python3.exe"),
            res_dir.join("python").join("bin").join("python3.exe"),
        ],
        OsLayout::MacOS | OsLayout::Linux => vec![
            res_dir.join("resources").join("python").join("bin").join("python3"),
            res_dir.join("python").join("bin").join("python3"),
            res_dir.join("python").join("install").join("bin").join("python3"),
        ],
    }
}

/// Resolves the bundled Python interpreter and its matching resource directory.
///
/// Checks `tauri_resource_dir` first, then infers the bundle resource directory
/// from `current_exe` (including its canonicalized target). This recovers on
/// macOS when `tauri::path::PathResolver::resource_dir` fails because the app
/// path traverses a symlink (such as `/tmp -> /private/tmp` or symlinked folders).
pub fn resolve_bundled_python_for_layout(
    tauri_resource_dir: Option<&Path>,
    current_exe: Option<&Path>,
    layout: OsLayout,
) -> Option<(PathBuf, PathBuf)> {
    let mut resource_dirs = Vec::new();
    if let Some(dir) = tauri_resource_dir {
        push_unique_dir(&mut resource_dirs, dir.to_path_buf());
    }
    if let Some(exe) = current_exe {
        let mut exe_paths = Vec::new();
        if let Ok(canon) = exe.canonicalize() {
            exe_paths.push(canon);
        }
        exe_paths.push(exe.to_path_buf());
        for exe_path in &exe_paths {
            if let Some(res) = default_resource_dir_for_exe(exe_path, layout) {
                push_unique_dir(&mut resource_dirs, res);
            }
            if let Some(parent) = exe_path.parent() {
                push_unique_dir(&mut resource_dirs, parent.to_path_buf());
            }
        }
    }

    for res_dir in resource_dirs {
        for candidate in resolve_python_candidates_for_layout(&res_dir, layout) {
            if candidate.is_file() {
                return Some((candidate, res_dir));
            }
        }
    }
    None
}

/// Resolves the bundled Python interpreter for the current OS layout.
pub fn resolve_bundled_python(
    tauri_resource_dir: Option<&Path>,
    current_exe: Option<&Path>,
) -> Option<(PathBuf, PathBuf)> {
    resolve_bundled_python_for_layout(tauri_resource_dir, current_exe, OsLayout::current())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "coperceive-platform-{}-{}-{}",
            label,
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[cfg(unix)]
    #[test]
    fn resolves_macos_bundled_python_when_resource_dir_is_missing_behind_symlink() {
        use std::os::unix::fs::symlink;

        let root = make_temp_dir("macos-symlink");
        let real_app = root.join("real").join("Coperceive.app");
        let macos_dir = real_app.join("Contents").join("MacOS");
        let res_dir = real_app.join("Contents").join("Resources");
        let py_bin_dir = res_dir.join("resources").join("python").join("bin");
        fs::create_dir_all(&macos_dir).expect("mkdir MacOS");
        fs::create_dir_all(&py_bin_dir).expect("mkdir python bin");
        let exe_path = macos_dir.join("coperceive-desktop");
        fs::write(&exe_path, b"stub").expect("write stub exe");
        let py313 = py_bin_dir.join("python3.13");
        fs::write(&py313, b"stub-python").expect("write python3.13");
        let py3 = py_bin_dir.join("python3");
        symlink("python3.13", &py3).expect("symlink python3");

        let link_root = root.join("via-symlink");
        fs::create_dir_all(&link_root).expect("mkdir via-symlink");
        let linked_app = link_root.join("Coperceive.app");
        symlink(&real_app, &linked_app).expect("symlink Coperceive.app");
        let linked_exe = linked_app.join("Contents").join("MacOS").join("coperceive-desktop");

        // Simulates Tauri's resource_dir() returning Err on macOS when the
        // executable path contains a symlink ancestor (e.g. /tmp -> /private/tmp).
        let (found_py, found_res) =
            resolve_bundled_python_for_layout(None, Some(&linked_exe), OsLayout::MacOS)
                .expect("should resolve bundled python from symlinked bundle exe");

        assert_eq!(found_res, res_dir.canonicalize().expect("canon res_dir"));
        assert_eq!(found_py, py3.canonicalize().expect("canon py3").parent().unwrap().join("python3"));
        assert!(found_py.is_file());

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn resolves_windows_and_linux_bundled_python_layouts() {
        let root = make_temp_dir("win-linux");

        let win_exe = root.join("win-app").join("coperceive-desktop.exe");
        let win_py = root.join("win-app").join("resources").join("python").join("python.exe");
        fs::create_dir_all(win_exe.parent().unwrap()).expect("mkdir win");
        fs::create_dir_all(win_py.parent().unwrap()).expect("mkdir win py");
        fs::write(&win_exe, b"exe").expect("write win exe");
        fs::write(&win_py, b"py").expect("write win py");
        let (found_win, _) =
            resolve_bundled_python_for_layout(None, Some(&win_exe), OsLayout::Windows)
                .expect("should resolve windows bundled python");
        assert!(found_win.is_file());

        let linux_exe = root.join("usr").join("bin").join("coperceive-desktop");
        let linux_py = root
            .join("usr")
            .join("lib")
            .join("coperceive-desktop")
            .join("resources")
            .join("python")
            .join("bin")
            .join("python3");
        fs::create_dir_all(linux_exe.parent().unwrap()).expect("mkdir linux bin");
        fs::create_dir_all(linux_py.parent().unwrap()).expect("mkdir linux py");
        fs::write(&linux_exe, b"exe").expect("write linux exe");
        fs::write(&linux_py, b"py").expect("write linux py");
        let (found_linux, _) =
            resolve_bundled_python_for_layout(None, Some(&linux_exe), OsLayout::Linux)
                .expect("should resolve linux bundled python");
        assert!(found_linux.is_file());

        let unbundled_exe = root.join("dev").join("target").join("debug").join("coperceive-desktop");
        fs::create_dir_all(unbundled_exe.parent().unwrap()).expect("mkdir dev");
        fs::write(&unbundled_exe, b"exe").expect("write dev exe");
        assert!(resolve_bundled_python_for_layout(None, Some(&unbundled_exe), OsLayout::MacOS).is_none());

        let _ = fs::remove_dir_all(&root);
    }
}
