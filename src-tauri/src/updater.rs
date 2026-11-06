// SPDX-License-Identifier: Apache-2.0
//! Desktop Application Updater
//!
//! Provides update checking outcome models, test-mode endpoint override resolution,
//! and quiet background check handling for macOS Coperceive.

use serde::{Deserialize, Serialize};

pub const DEFAULT_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDlBQjI5NEI1NkZDM0M0NDQKUldSRXhNTnZ0WlN5bXVJWXpKOWIrNHJ6TEdrRkNPYTlxWWFENnIrbkZuQ2VLekk3OXhrN2NoTGEK";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "status", rename_all = "lowercase")]
pub enum UpdateCheckOutcome {
    Available {
        version: String,
        current_version: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        notes: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub_date: Option<String>,
    },
    UpToDate {
        current_version: String,
    },
    Error {
        error: String,
        is_network: bool,
    },
}

/// Resolves an update endpoint override URL, if allowed.
///
/// Overrides (from request body or `COPERCEIVE_UPDATE_URL`) are strictly honoured
/// ONLY when `coperceive_desktop::test_seams::test_env_active()` is true.
/// In normal runs, any override is ignored and `None` is returned so the configured
/// production endpoint in `tauri.conf.json` is used.
/// The version the app reports to people: the bundle version (`tauri.conf.json`,
/// which `scripts/desktop/sync_version.sh` sets from the release tag), the
/// same one the updater compares. Not `CARGO_PKG_VERSION`: `Cargo.toml` stays
/// at its committed version, so a v0.1.1 build would say 0.1.0.
pub fn app_version(bundle_version: &impl std::fmt::Display) -> String {
    bundle_version.to_string()
}

pub fn resolve_update_endpoint_override(override_url_opt: Option<&str>) -> Option<String> {
    resolve_update_endpoint_override_with_env(
        override_url_opt,
        crate::test_seams::test_env_active(),
        std::env::var("COPERCEIVE_UPDATE_URL").ok().as_deref(),
    )
}

pub const DEB_UPDATE_NOTICE: &str = "In-app updates are supported when running from AppImage. On Debian/Ubuntu (.deb), update using your package manager or download the latest .deb from GitHub Releases.";

/// Checks whether an in-app update check on Linux should be bypassed because the
/// application is running from an installed package (.deb) rather than a portable AppImage.
///
/// When running from a Debian/Ubuntu `.deb` package (i.e. `APPIMAGE` is not set):
/// - A manual check (`is_manual = true`) reports an error directing the user to `apt` / GitHub Releases.
/// - A background check (`is_manual = false`) reports `UpToDate` with the bundle version, so no update card is shown.
/// - If `APPIMAGE` is set or if running in test environment, returns `None` to allow the normal updater to run.
pub fn linux_deb_check_outcome(
    is_manual: bool,
    appimage_var: Option<&std::ffi::OsStr>,
    test_env: bool,
    bundle_version: &impl std::fmt::Display,
) -> Option<UpdateCheckOutcome> {
    if test_env || appimage_var.is_some() {
        return None;
    }
    if is_manual {
        Some(UpdateCheckOutcome::Error {
            error: DEB_UPDATE_NOTICE.to_string(),
            is_network: false,
        })
    } else {
        Some(UpdateCheckOutcome::UpToDate {
            current_version: app_version(bundle_version),
        })
    }
}

/// Checks whether an in-app update install on Linux should be prevented because the
/// application is running from an installed package (.deb) rather than a portable AppImage.
pub fn linux_deb_install_prevented(
    appimage_var: Option<&std::ffi::OsStr>,
    test_env: bool,
) -> bool {
    !test_env && appimage_var.is_none()
}

pub fn resolve_update_endpoint_override_with_env(
    override_url_opt: Option<&str>,
    test_env_active: bool,
    env_update_url: Option<&str>,
) -> Option<String> {
    if !test_env_active {
        return None;
    }
    override_url_opt
        .map(|s| s.trim().to_string())
        .or_else(|| env_update_url.map(|s| s.trim().to_string()))
        .filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn about_diagnostics_and_up_to_date_report_the_bundle_version() {
        // Every place that shows the app's version to a person goes through
        // app_version(package_info().version); Cargo's version is never shown.
        let main = include_str!("main.rs");
        for (what, anchor) in [
            ("About", "\"about_coperceive\" => {"),
            ("Copy Diagnostics", "fn copy_diagnostics_to_clipboard("),
        ] {
            let start = main.find(anchor).unwrap_or_else(|| panic!("{what}: {anchor} not found"));
            let body = &main[start..(start + 400).min(main.len())];
            assert!(
                body.contains("updater::app_version(&app.package_info().version)"),
                "{what} must report the bundle version"
            );
            assert!(!body.contains("CARGO_PKG_VERSION"), "{what} reads CARGO_PKG_VERSION");
        }
        let checker = &main[main.find("cfg.updater_checker = Some(").expect("checker")..];
        let checker = &checker[..checker.find("cfg.updater_installer").unwrap_or(checker.len())];
        assert!(!checker.contains("CARGO_PKG_VERSION"), "the up-to-date reply reads CARGO_PKG_VERSION");
        assert_eq!(checker.matches("app_version(&check_handle.package_info().version)").count(), 2);
        assert_eq!(app_version(&"0.1.1"), "0.1.1");
    }

    #[test]
    fn test_tauri_conf_has_no_insecure_transport_protocol() {
        let conf_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json");
        let content = std::fs::read_to_string(&conf_path).expect("read tauri.conf.json");
        assert!(
            !content.contains("dangerousInsecureTransportProtocol"),
            "Committed tauri.conf.json must not enable dangerousInsecureTransportProtocol"
        );
    }

    #[test]
    fn test_endpoint_overrides_honoured_only_when_test_env_active() {
        assert_eq!(
            resolve_update_endpoint_override_with_env(
                Some("http://override.example.com/manifest.json"),
                false,
                Some("http://evil.example.com/manifest.json"),
            ),
            None
        );
        assert_eq!(
            resolve_update_endpoint_override_with_env(
                None,
                false,
                Some("http://evil.example.com/manifest.json"),
            ),
            None
        );

        assert_eq!(
            resolve_update_endpoint_override_with_env(
                Some("http://127.0.0.1:8080/latest.json"),
                true,
                None,
            ),
            Some("http://127.0.0.1:8080/latest.json".to_string())
        );
        assert_eq!(
            resolve_update_endpoint_override_with_env(
                None,
                true,
                Some("http://127.0.0.1:9090/latest.json"),
            ),
            Some("http://127.0.0.1:9090/latest.json".to_string())
        );
        assert_eq!(
            resolve_update_endpoint_override_with_env(
                Some("   "),
                true,
                Some("   "),
            ),
            None
        );
    }

    #[test]
    fn test_outcome_serialization() {
        let available = UpdateCheckOutcome::Available {
            version: "0.2.0".into(),
            current_version: "0.1.0".into(),
            notes: Some("Notes".into()),
            pub_date: None,
        };
        let json = serde_json::to_value(&available).unwrap();
        assert_eq!(json["status"], "available");
        assert_eq!(json["version"], "0.2.0");

        let uptodate = UpdateCheckOutcome::UpToDate {
            current_version: "0.1.0".into(),
        };
        let json_uptodate = serde_json::to_value(&uptodate).unwrap();
        assert_eq!(json_uptodate["status"], "uptodate");

        let error = UpdateCheckOutcome::Error {
            error: "Connection failed".into(),
            is_network: true,
        };
        let json_err = serde_json::to_value(&error).unwrap();
        assert_eq!(json_err["status"], "error");
        assert_eq!(json_err["is_network"], true);
    }

    #[test]
    fn test_linux_deb_update_decision() {
        let version = "0.1.1";

        // APPIMAGE unset on Linux plus a manual check gives the .deb error message
        let manual_deb = linux_deb_check_outcome(true, None, false, &version);
        assert_eq!(
            manual_deb,
            Some(UpdateCheckOutcome::Error {
                error: DEB_UPDATE_NOTICE.to_string(),
                is_network: false,
            })
        );

        // APPIMAGE unset plus a background check gives up to date, so no card
        let bg_deb = linux_deb_check_outcome(false, None, false, &version);
        assert_eq!(
            bg_deb,
            Some(UpdateCheckOutcome::UpToDate {
                current_version: "0.1.1".to_string(),
            })
        );

        // APPIMAGE set takes the normal path (returns None)
        let appimage_val = std::ffi::OsStr::new("/tmp/Coperceive.AppImage");
        let appimage_manual = linux_deb_check_outcome(true, Some(appimage_val), false, &version);
        assert_eq!(appimage_manual, None);
        let appimage_bg = linux_deb_check_outcome(false, Some(appimage_val), false, &version);
        assert_eq!(appimage_bg, None);

        // Test environment active also takes the normal path (returns None)
        assert_eq!(linux_deb_check_outcome(true, None, true, &version), None);
        assert_eq!(linux_deb_check_outcome(false, None, true, &version), None);

        // Install prevention decision
        assert!(linux_deb_install_prevented(None, false));
        assert!(!linux_deb_install_prevented(Some(appimage_val), false));
        assert!(!linux_deb_install_prevented(None, true));
    }
}
