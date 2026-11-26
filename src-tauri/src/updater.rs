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
    Unsupported {
        message: String,
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

pub const INTEL_MAC_LAST_RELEASE_NOTICE: &str =
    "Coperceive v0.1.2 is the last version for Intel Macs.";

/// When the updater manifest has a newer release that omits the running platform
/// (`TargetNotFound` / `TargetsNotFound`), Intel Macs receive a plain last-release
/// notice instead of surfacing the raw updater error string.
pub fn missing_target_check_outcome(is_intel_mac: bool) -> Option<UpdateCheckOutcome> {
    if is_intel_mac {
        Some(UpdateCheckOutcome::Unsupported {
            message: INTEL_MAC_LAST_RELEASE_NOTICE.to_string(),
        })
    } else {
        None
    }
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
    resolve_update_endpoint_override_with_flags(
        override_url_opt,
        cfg!(feature = "test-updater-override"),
        test_env_active,
        env_update_url,
    )
}

/// Resolves an update endpoint override only when BOTH the compile-time
/// `test-updater-override` feature AND the runtime test environment are active.
/// Release builds are compiled without `test-updater-override`, so they always
/// return `None` regardless of runtime environment variables or IPC arguments.
pub fn resolve_update_endpoint_override_with_flags(
    override_url_opt: Option<&str>,
    feature_enabled: bool,
    test_env_active: bool,
    env_update_url: Option<&str>,
) -> Option<String> {
    if !feature_enabled || !test_env_active {
        return None;
    }
    override_url_opt
        .map(|s| s.trim().to_string())
        .or_else(|| env_update_url.map(|s| s.trim().to_string()))
        .filter(|s| !s.is_empty())
}

/// Returns `true` when updater installation/restart should be skipped in a test
/// run (e.g. UI mock feed smoke checks that inspect the download flow without
/// replacing or restarting the running binary).
///
/// Only when BOTH the compile-time `test-updater-override` feature AND the
/// runtime test environment AND `COPERCEIVE_TEST_REAL_UPDATE_INSTALL=1|true|yes`
/// are active does a test run execute `update.install` + restart.
pub fn should_skip_updater_install_for_test_with_flags(
    feature_enabled: bool,
    test_env_active: bool,
    real_install_env: Option<&str>,
) -> bool {
    if !test_env_active {
        return false;
    }
    if feature_enabled
        && matches!(
            real_install_env.map(str::trim),
            Some("1" | "true" | "TRUE" | "yes" | "YES")
        )
    {
        return false;
    }
    true
}

pub fn should_skip_updater_install_for_test() -> bool {
    should_skip_updater_install_for_test_with_flags(
        cfg!(feature = "test-updater-override"),
        crate::test_seams::test_env_active(),
        std::env::var("COPERCEIVE_TEST_REAL_UPDATE_INSTALL")
            .ok()
            .as_deref(),
    )
}

/// Applies `COPERCEIVE_*` test environment variables from a JSON file named by
/// `--e2e-updater-env=<path>` (or `--e2e-updater-env <path>`).
///
/// Only compiled when the `test-updater-override` feature is enabled so that
/// macOS LaunchServices restarts (`open -n ... --args ...`) preserve the test
/// harness's `COPERCEIVE_STATE_DIR` / `COPERCEIVE_UPDATE_URL` overrides while
/// release builds never parse or honour the flag.
#[cfg(feature = "test-updater-override")]
pub fn apply_test_updater_env_from_args() {
    let mut args = std::env::args_os().skip(1);
    let mut env_path: Option<std::path::PathBuf> = None;
    while let Some(arg) = args.next() {
        let lossy = arg.to_string_lossy();
        if let Some(rest) = lossy.strip_prefix("--e2e-updater-env=") {
            env_path = Some(std::path::PathBuf::from(rest));
            break;
        }
        if lossy == "--e2e-updater-env" {
            if let Some(next_arg) = args.next() {
                env_path = Some(std::path::PathBuf::from(next_arg));
            }
            break;
        }
    }
    let Some(path) = env_path else {
        return;
    };
    let Ok(raw) = std::fs::read_to_string(&path) else {
        return;
    };
    let Ok(map) = serde_json::from_str::<std::collections::HashMap<String, String>>(&raw) else {
        return;
    };
    for (key, value) in map {
        if key.starts_with("COPERCEIVE_") {
            // SAFETY: called once at the very start of main() before threads spawn.
            unsafe {
                std::env::set_var(key, value);
            }
        }
    }
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
        let checker = &main[main.find("fn run_updater_check(").expect("run_updater_check")..];
        let checker = &checker[..checker.find("fn run_updater_install(").unwrap_or(checker.len())];
        assert!(!checker.contains("CARGO_PKG_VERSION"), "the up-to-date reply reads CARGO_PKG_VERSION");
        assert_eq!(checker.matches("app_version(&handle.package_info().version)").count(), 2);
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
            resolve_update_endpoint_override_with_flags(
                Some("http://127.0.0.1:8080/latest.json"),
                true,
                true,
                None,
            ),
            Some("http://127.0.0.1:8080/latest.json".to_string())
        );
        assert_eq!(
            resolve_update_endpoint_override_with_flags(
                None,
                true,
                true,
                Some("http://127.0.0.1:9090/latest.json"),
            ),
            Some("http://127.0.0.1:9090/latest.json".to_string())
        );
        assert_eq!(
            resolve_update_endpoint_override_with_flags(
                Some("   "),
                true,
                true,
                Some("   "),
            ),
            None
        );

        // With the compile-time feature gate, default/release builds ignore overrides
        // even when test_env_active is true.
        let expected_env = if cfg!(feature = "test-updater-override") {
            Some("http://127.0.0.1:8080/latest.json".to_string())
        } else {
            None
        };
        assert_eq!(
            resolve_update_endpoint_override_with_env(
                Some("http://127.0.0.1:8080/latest.json"),
                true,
                None,
            ),
            expected_env
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

        let unsupported = UpdateCheckOutcome::Unsupported {
            message: INTEL_MAC_LAST_RELEASE_NOTICE.into(),
        };
        let json_unsupported = serde_json::to_value(&unsupported).unwrap();
        assert_eq!(json_unsupported["status"], "unsupported");
        assert_eq!(
            json_unsupported["message"],
            "Coperceive v0.1.2 is the last version for Intel Macs."
        );
    }

    #[test]
    fn test_missing_target_check_outcome_for_intel_mac() {
        assert_eq!(
            missing_target_check_outcome(true),
            Some(UpdateCheckOutcome::Unsupported {
                message: INTEL_MAC_LAST_RELEASE_NOTICE.to_string(),
            })
        );
        assert_eq!(missing_target_check_outcome(false), None);

        let main = include_str!("main.rs");
        let checker = &main[main.find("fn run_updater_check(").expect("run_updater_check")..];
        let checker = &checker[..checker.find("fn run_updater_install(").unwrap_or(checker.len())];
        assert!(checker.contains("Error::TargetNotFound"));
        assert!(checker.contains("Error::TargetsNotFound"));
        assert!(checker.contains("missing_target_check_outcome"));
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

    #[test]
    fn endpoint_override_requires_compile_time_feature_and_test_env() {
        let local_url = "http://127.0.0.1:45678/latest.json";
        // Release builds compile without `test-updater-override`, so overrides
        // are always refused even when test env + override URL are present.
        assert_eq!(
            resolve_update_endpoint_override_with_flags(
                Some(local_url),
                false,
                true,
                Some(local_url),
            ),
            None
        );
        // Feature compiled in without active test env must also refuse.
        assert_eq!(
            resolve_update_endpoint_override_with_flags(
                Some(local_url),
                true,
                false,
                Some(local_url),
            ),
            None
        );
        // Both feature + test env allow body override, falling back to env var.
        assert_eq!(
            resolve_update_endpoint_override_with_flags(
                Some("  http://127.0.0.1:9000/latest.json  "),
                true,
                true,
                Some(local_url),
            ),
            Some("http://127.0.0.1:9000/latest.json".to_string())
        );
        assert_eq!(
            resolve_update_endpoint_override_with_flags(None, true, true, Some(local_url)),
            Some(local_url.to_string())
        );

        // The compiled cfg!(feature = "test-updater-override") gate must match.
        let compiled = resolve_update_endpoint_override_with_env(Some(local_url), true, None);
        if cfg!(feature = "test-updater-override") {
            assert_eq!(compiled, Some(local_url.to_string()));
        } else {
            assert_eq!(compiled, None);
        }
    }

    #[test]
    fn real_update_install_requires_feature_and_opt_in_env() {
        // Production launches (test_env_active = false) never skip real install.
        assert!(!should_skip_updater_install_for_test_with_flags(false, false, None));
        assert!(!should_skip_updater_install_for_test_with_flags(true, false, None));

        // Standard test runs (e.g. e2e_mac.sh Step 5d) skip real install.
        assert!(should_skip_updater_install_for_test_with_flags(true, true, None));
        assert!(should_skip_updater_install_for_test_with_flags(true, true, Some("0")));

        // Release builds without the feature still skip even if env var is set.
        assert!(should_skip_updater_install_for_test_with_flags(false, true, Some("1")));

        // Only feature + test_env + opt-in env triggers real install + restart.
        assert!(!should_skip_updater_install_for_test_with_flags(true, true, Some("1")));
        assert!(!should_skip_updater_install_for_test_with_flags(true, true, Some("true")));
    }
}
