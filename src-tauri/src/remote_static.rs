// SPDX-License-Identifier: Apache-2.0
//! Where the packaged app keeps its resources, per operating system layout.
//!
//! Python resolution uses `OsLayout` and `default_resource_dir_for_exe`
//! (`platform`). `static_bundle_dir` finds the app's built viewer bundle.
//!
//! This module used to upload that bundle to the cluster for the app's viewer
//! job (`prepare_remote_static`). That path was removed with `remote_connect`:
//! viewing a cluster dataset runs no job, and the viewer is served locally.

use std::fs;
use std::path::{Path, PathBuf};

static RESOURCE_DIR: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

/// Sets Tauri's application resource directory (called from app initialization).
pub fn set_resource_dir(path: PathBuf) {
    let _ = RESOURCE_DIR.set(path);
}

/// Supported operating system resource layouts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OsLayout {
    MacOS,
    Windows,
    Linux,
}

impl OsLayout {
    pub fn current() -> Self {
        #[cfg(target_os = "macos")]
        {
            OsLayout::MacOS
        }
        #[cfg(target_os = "windows")]
        {
            OsLayout::Windows
        }
        #[cfg(target_os = "linux")]
        {
            OsLayout::Linux
        }
        #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
        {
            OsLayout::Linux
        }
    }
}

/// Infers the default Tauri resource directory for the given executable path per OS layout.
pub fn default_resource_dir_for_exe(exe: &Path, layout: OsLayout) -> Option<PathBuf> {
    match layout {
        OsLayout::MacOS => {
            // <App>.app/Contents/MacOS/coperceive-desktop -> <App>.app/Contents/Resources
            exe.parent()?.parent().map(|c| c.join("Resources"))
        }
        OsLayout::Windows => {
            // <install_dir>\coperceive-desktop.exe -> <install_dir>
            exe.parent().map(Path::to_path_buf)
        }
        OsLayout::Linux => {
            // /usr/bin/coperceive-desktop -> /usr/lib/coperceive-desktop
            let parent = exe.parent()?;
            if let Some(grandparent) = parent.parent() {
                Some(grandparent.join("lib").join("coperceive-desktop"))
            } else {
                Some(parent.to_path_buf())
            }
        }
    }
}

/// Finds the packaged viewer bundle inside a Tauri resource directory for a specific OS layout.
pub fn find_packaged_static_for_layout(resource_dir: &Path, layout: OsLayout) -> Option<PathBuf> {
    let py_base = if resource_dir.join("resources").join("python").is_dir() {
        resource_dir.join("resources").join("python")
    } else if resource_dir.join("python").is_dir() {
        resource_dir.join("python")
    } else {
        return None;
    };

    match layout {
        OsLayout::Windows => {
            let candidate = py_base
                .join("Lib")
                .join("site-packages")
                .join("coperceive")
                .join("app")
                .join("static");
            if candidate.join("index.html").is_file() {
                Some(candidate)
            } else {
                let candidate_lower = py_base
                    .join("lib")
                    .join("site-packages")
                    .join("coperceive")
                    .join("app")
                    .join("static");
                if candidate_lower.join("index.html").is_file() {
                    Some(candidate_lower)
                } else {
                    None
                }
            }
        }
        OsLayout::MacOS | OsLayout::Linux => {
            let lib_dir = py_base.join("lib");
            if let Ok(entries) = fs::read_dir(&lib_dir) {
                for entry in entries.flatten() {
                    let candidate = entry
                        .path()
                        .join("site-packages")
                        .join("coperceive")
                        .join("app")
                        .join("static");
                    if candidate.join("index.html").is_file() {
                        return Some(candidate);
                    }
                }
            }
            None
        }
    }
}

/// Finds the packaged viewer bundle inside the application resources for the current OS.
pub fn find_packaged_static(resource_dir: &Path) -> Option<PathBuf> {
    find_packaged_static_for_layout(resource_dir, OsLayout::current())
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

    if let Some(res) = RESOURCE_DIR.get() {
        if let Some(path) = find_packaged_static(res) {
            return Ok(path);
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(res) = default_resource_dir_for_exe(&exe, OsLayout::current()) {
            if let Some(path) = find_packaged_static(&res) {
                return Ok(path);
            }
        }
        if let Some(parent) = exe.parent() {
            if let Some(path) = find_packaged_static(parent) {
                return Ok(path);
            }
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

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn temp_root(label: &str) -> PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let sequence = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "coperceive-static-{label}-{}-{sequence}",
            std::process::id()
        ))
    }

    #[test]
    fn test_find_packaged_static_macos_layout() {
        let root = temp_root("macos-layout");
        // macOS: <App>.app/Contents/Resources/resources/python/lib/python3.13/site-packages/coperceive/app/static/index.html
        let static_dir = root
            .join("Contents")
            .join("Resources")
            .join("resources")
            .join("python")
            .join("lib")
            .join("python3.13")
            .join("site-packages")
            .join("coperceive")
            .join("app")
            .join("static");
        fs::create_dir_all(&static_dir).expect("create macos static dir");
        fs::write(static_dir.join("index.html"), "<html>macos</html>").expect("write index");

        let res_dir = root.join("Contents").join("Resources");
        let found = find_packaged_static_for_layout(&res_dir, OsLayout::MacOS);
        assert_eq!(found, Some(static_dir));

        let exe = root.join("Contents").join("MacOS").join("coperceive-desktop");
        let inferred_res = default_resource_dir_for_exe(&exe, OsLayout::MacOS);
        assert_eq!(inferred_res, Some(res_dir));

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn test_find_packaged_static_windows_layout() {
        let root = temp_root("windows-layout");
        // Windows: <install_dir>/resources/python/Lib/site-packages/coperceive/app/static/index.html
        let static_dir = root
            .join("resources")
            .join("python")
            .join("Lib")
            .join("site-packages")
            .join("coperceive")
            .join("app")
            .join("static");
        fs::create_dir_all(&static_dir).expect("create windows static dir");
        fs::write(static_dir.join("index.html"), "<html>windows</html>").expect("write index");

        let found = find_packaged_static_for_layout(&root, OsLayout::Windows);
        assert_eq!(found, Some(static_dir));

        let exe = root.join("coperceive-desktop.exe");
        let inferred_res = default_resource_dir_for_exe(&exe, OsLayout::Windows);
        assert_eq!(inferred_res, Some(root.clone()));

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn test_find_packaged_static_linux_layout() {
        let root = temp_root("linux-layout");
        // Linux: /usr/lib/coperceive-desktop/resources/python/lib/python3.13/site-packages/coperceive/app/static/index.html
        let res_dir = root.join("usr").join("lib").join("coperceive-desktop");
        let static_dir = res_dir
            .join("resources")
            .join("python")
            .join("lib")
            .join("python3.13")
            .join("site-packages")
            .join("coperceive")
            .join("app")
            .join("static");
        fs::create_dir_all(&static_dir).expect("create linux static dir");
        fs::write(static_dir.join("index.html"), "<html>linux</html>").expect("write index");

        let found = find_packaged_static_for_layout(&res_dir, OsLayout::Linux);
        assert_eq!(found, Some(static_dir));

        let exe = root.join("usr").join("bin").join("coperceive-desktop");
        let inferred_res = default_resource_dir_for_exe(&exe, OsLayout::Linux);
        assert_eq!(inferred_res, Some(res_dir));

        let _ = fs::remove_dir_all(&root);
    }
}
