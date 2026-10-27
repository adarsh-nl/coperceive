// SPDX-License-Identifier: Apache-2.0
//! Local storage for user-added HPC servers.
//!
//! A [`ServerEntry`] wraps the shared slice-0 [`ServerProfile`] with app-local
//! fields (id, schema version, last read-only discovery) that must not leak into
//! the shared profile format. Entries live in
//! `~/Library/Application Support/nl.adarsh.coperceive/servers.json` with
//! directory 0700 and file 0600. They never contain credentials: sign-in uses
//! the user's own ssh-agent and `~/.ssh/config`.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::hpc::{
    DiscoveredPartition, EnvironmentKind, EnvironmentSetup, SchedulerKind, ServerProfile,
};

pub const SERVER_ENTRY_SCHEMA_VERSION: u32 = 1;

/// Effective settings for a `~/.ssh/config` alias, read with `ssh -G`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct SshHostSettings {
    pub alias: String,
    #[serde(default)]
    pub hostname: Option<String>,
    #[serde(default)]
    pub user: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub proxy_jump: Option<String>,
    #[serde(default)]
    pub proxy_command: Option<String>,
}

/// Environment facts observed on the login node in the last test.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct DiscoveredEnvironment {
    #[serde(default)]
    pub module_system: bool,
    #[serde(default)]
    pub conda: Option<String>,
    #[serde(default)]
    pub virtualenv: Option<String>,
    #[serde(default)]
    pub python: Option<String>,
    #[serde(default)]
    pub container_runtime: Option<String>,
    #[serde(default)]
    pub coperceive_path: Option<String>,
    #[serde(default)]
    pub coperceive_version: Option<String>,
    /// Directories that look like per-user scratch space on this cluster.
    #[serde(default)]
    pub scratch_candidates: Vec<String>,
}

/// Read-only discovery kept for showing choices next time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct DiscoveredServer {
    #[serde(default)]
    pub partitions: Vec<DiscoveredPartition>,
    #[serde(default)]
    pub gpu_types: Vec<String>,
    #[serde(default)]
    pub environment: DiscoveredEnvironment,
    #[serde(default)]
    pub checked_unix: u64,
}

/// One server the user added, as stored locally.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ServerEntry {
    #[serde(default)]
    pub id: String,
    #[serde(default = "default_schema_version")]
    pub schema_version: u32,
    pub profile: ServerProfile,
    #[serde(default)]
    pub discovered: Option<DiscoveredServer>,
    #[serde(default)]
    pub ssh: Option<SshHostSettings>,
}

fn default_schema_version() -> u32 {
    SERVER_ENTRY_SCHEMA_VERSION
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct ServerFile {
    #[serde(default = "default_schema_version")]
    schema_version: u32,
    #[serde(default)]
    servers: Vec<ServerEntry>,
}

impl ServerEntry {
    pub fn new(profile: ServerProfile) -> Self {
        Self {
            id: new_server_id(),
            schema_version: SERVER_ENTRY_SCHEMA_VERSION,
            profile,
            discovered: None,
            ssh: None,
        }
    }

    /// ssh destination this entry resolves to. An alias is preferred so the
    /// user's own ssh config decides host, user, port and jump hosts.
    pub fn ssh_target(&self) -> Result<crate::ssh::SshTarget, String> {
        let port = self.ssh.as_ref().and_then(|settings| settings.port);
        let proxy_jump = self.ssh.as_ref().and_then(|settings| settings.proxy_jump.clone());
        Ok(crate::ssh::SshTarget {
            destination: self.profile.ssh_target()?,
            port,
            proxy_jump,
        })
    }

    pub fn raw_alias(&self) -> Option<&str> {
        self.profile
            .ssh_alias
            .as_deref()
            .map(str::trim)
            .filter(|alias| !alias.is_empty())
    }

    /// Validates what the user typed before it is saved or used.
    pub fn validate(&self) -> Result<(), String> {
        self.profile.validate()?;
        let profile = &self.profile;
        if let Some(host) = &profile.host {
            reject_control_chars(host, "The host name")?;
        }
        if let Some(user) = &profile.user {
            reject_control_chars(user, "The user name")?;
        }
        if let Some(alias) = &profile.ssh_alias {
            reject_control_chars(alias, "The SSH host name")?;
        }
        for root in &profile.data_roots {
            validate_inside_roots(root, "data folder")?;
        }
        if !profile.scratch_template.trim().is_empty() {
            validate_inside_roots(&profile.scratch_template, "scratch folder")?;
        }
        if profile.scheduler == SchedulerKind::None && profile.defaults.partition.is_some() {
            return Err(
                "This server has no scheduler, so it cannot submit to a partition. Clear the \
                 partition or set the scheduler to Slurm."
                    .to_string(),
            );
        }
        if let Some(interpreter) = &profile.environment.interpreter {
            validate_inside_roots(interpreter, "the environment interpreter")?;
        }
        if let Some(image) = &profile.environment.container_image {
            validate_inside_roots(image, "the container image")?;
        }
        if let Some(wheelhouse) = &profile.environment.wheelhouse {
            validate_inside_roots(wheelhouse, "the wheelhouse folder")?;
        }
        for module in &profile.environment.modules {
            reject_control_chars(module, "The module name")?;
        }
        // A stored profile must never contain anything credential-like. This is a
        // belt-and-braces check: the shared struct refuses unknown fields and has
        // no credential fields at all.
        let value = serde_json::to_value(profile)
            .map_err(|e| format!("Could not check the profile for secrets: {e}"))?;
        if let Some(key) = first_secret_like_key(&value, "") {
            return Err(format!(
                "This profile contains a field that looks like a secret ({key}). Coperceive \
                 stores no passwords or keys; sign-in uses your ssh-agent."
            ));
        }
        Ok(())
    }
}

/// Recursively finds a JSON key that looks like a credential.
fn first_secret_like_key(value: &serde_json::Value, prefix: &str) -> Option<String> {
    match value {
        serde_json::Value::Object(map) => {
            for (key, child) in map {
                let lower = key.to_lowercase();
                let path = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                if lower.contains("password")
                    || lower.contains("passwd")
                    || lower.contains("passphrase")
                    || lower.contains("private_key")
                    || lower.contains("privatekey")
                    || lower.contains("secret")
                    || lower.contains("token")
                {
                    return Some(path);
                }
                if let Some(found) = first_secret_like_key(child, &path) {
                    return Some(found);
                }
            }
            None
        }
        serde_json::Value::Array(items) => {
            for (index, child) in items.iter().enumerate() {
                if let Some(found) = first_secret_like_key(child, &format!("{prefix}[{index}]")) {
                    return Some(found);
                }
            }
            None
        }
        _ => None,
    }
}

/// A remote path may contain spaces, quotes and shell metacharacters (they are
/// quoted when used), but never control characters or `..` segments that would
/// escape the profile root.
pub fn validate_inside_roots(path: &str, what: &str) -> Result<(), String> {
    if path.trim().is_empty() {
        return Err(format!(
            "The {what} is empty. Choose a folder on the cluster."
        ));
    }
    if path.chars().any(|c| c == '\n' || c == '\r' || c == '\0') {
        return Err(format!(
            "The {what} contains a line break or control character, which is not a valid path."
        ));
    }
    if path.split('/').any(|segment| segment == "..") {
        return Err(format!(
            "The {what} may not contain '..'. Paths must stay inside the folders you chose."
        ));
    }
    Ok(())
}

fn reject_control_chars(value: &str, what: &str) -> Result<(), String> {
    if value.chars().any(|c| c.is_control()) {
        return Err(format!(
            "{what} contains a control character, which is not allowed."
        ));
    }
    Ok(())
}

/// Returns the server store path.
///
/// macOS: `~/Library/Application Support/nl.adarsh.coperceive/servers.json`
/// Other Unix: `$XDG_DATA_HOME/nl.adarsh.coperceive/servers.json` or the
/// `~/.local/share/nl.adarsh.coperceive` fallback. `COPERCEIVE_STATE_DIR`
/// overrides the directory (used by tests and the e2e harness).
pub fn servers_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        return PathBuf::from(dir).join("servers.json");
    }
    if let Some(home) = std::env::var_os("HOME") {
        #[cfg(target_os = "macos")]
        {
            return PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join("nl.adarsh.coperceive")
                .join("servers.json");
        }
        #[cfg(not(target_os = "macos"))]
        {
            if let Some(xdg) = std::env::var_os("XDG_DATA_HOME") {
                return PathBuf::from(xdg)
                    .join("nl.adarsh.coperceive")
                    .join("servers.json");
            }
            return PathBuf::from(home)
                .join(".local")
                .join("share")
                .join("nl.adarsh.coperceive")
                .join("servers.json");
        }
    }
    PathBuf::from(".coperceive-servers.json")
}

pub fn load_servers() -> Vec<ServerEntry> {
    load_servers_from_path(&servers_file_path())
}

/// Loads saved servers. A corrupt or unreadable file never takes the app down:
/// the user simply starts with no servers and can add one again.
pub fn load_servers_from_path(path: &Path) -> Vec<ServerEntry> {
    let Ok(contents) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(file) = serde_json::from_str::<ServerFile>(&contents) else {
        return Vec::new();
    };
    file.servers
        .into_iter()
        .filter(|entry| !entry.id.trim().is_empty())
        .collect()
}

/// Saves servers with directory 0700 and file 0600, replacing atomically.
pub fn save_servers(servers: &[ServerEntry]) -> Result<(), String> {
    save_servers_to_path(servers, &servers_file_path())
}

pub fn save_servers_to_path(servers: &[ServerEntry], path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Could not create the settings folder: {e}"))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700));
        }
    }
    let file = ServerFile {
        schema_version: SERVER_ENTRY_SCHEMA_VERSION,
        servers: servers.to_vec(),
    };
    let serialized = serde_json::to_string_pretty(&file)
        .map_err(|e| format!("Could not save the server list: {e}"))?;

    #[cfg(unix)]
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let tmp = path.with_extension("json.tmp");
        let mut handle = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(|e| format!("Could not save the server list: {e}"))?;
        handle
            .write_all(serialized.as_bytes())
            .map_err(|e| format!("Could not save the server list: {e}"))?;
        handle
            .sync_all()
            .map_err(|e| format!("Could not save the server list: {e}"))?;
        drop(handle);
        std::fs::rename(&tmp, path)
            .map_err(|e| format!("Could not save the server list: {e}"))?;
    }
    #[cfg(not(unix))]
    {
        std::fs::write(path, serialized)
            .map_err(|e| format!("Could not save the server list: {e}"))?;
    }
    Ok(())
}

/// Adds or replaces an entry by id, returning the saved entry.
pub fn upsert_server(
    servers: &mut Vec<ServerEntry>,
    mut entry: ServerEntry,
) -> Result<ServerEntry, String> {
    if entry.id.trim().is_empty() {
        entry.id = new_server_id();
    }
    entry.schema_version = SERVER_ENTRY_SCHEMA_VERSION;
    entry.validate()?;
    if let Some(existing) = servers.iter_mut().find(|s| s.id == entry.id) {
        *existing = entry.clone();
    } else {
        servers.push(entry.clone());
    }
    save_servers(servers)?;
    Ok(entry)
}

pub fn remove_server(servers: &mut Vec<ServerEntry>, id: &str) -> Result<(), String> {
    servers.retain(|s| s.id != id);
    save_servers(servers)
}

/// Parses `Host` aliases from an ssh config file, skipping wildcard patterns.
pub fn parse_ssh_config_hosts(contents: &str) -> Vec<String> {
    let mut hosts: Vec<String> = Vec::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some(rest) = line
            .strip_prefix("Host ")
            .or_else(|| line.strip_prefix("Host\t"))
            .or_else(|| {
                // Case-insensitive "Host" keyword.
                let lower = line.to_lowercase();
                if lower.starts_with("host ") || lower.starts_with("host\t") {
                    line.get(5..)
                } else {
                    None
                }
            })
        else {
            continue;
        };
        for alias in rest.split_whitespace() {
            if alias.contains('*') || alias.contains('?') || alias.starts_with('!') {
                continue;
            }
            if !hosts.iter().any(|host| host == alias) {
                hosts.push(alias.to_string());
            }
        }
    }
    hosts
}

/// Parses `ssh -G <alias>` output into the settings we show the user.
pub fn parse_ssh_g_output(alias: &str, output: &str) -> SshHostSettings {
    let mut settings = SshHostSettings {
        alias: alias.to_string(),
        ..Default::default()
    };
    for line in output.lines() {
        let mut parts = line.splitn(2, char::is_whitespace);
        let Some(key) = parts.next() else { continue };
        let value = parts.next().unwrap_or("").trim();
        if value.is_empty() || value == "none" {
            continue;
        }
        match key.to_lowercase().as_str() {
            "hostname" => settings.hostname = Some(value.to_string()),
            "user" => settings.user = Some(value.to_string()),
            "port" => settings.port = value.parse::<u16>().ok(),
            "proxyjump" => settings.proxy_jump = Some(value.to_string()),
            "proxycommand" => settings.proxy_command = Some(value.to_string()),
            _ => {}
        }
    }
    settings
}

/// Builds a profile from a chosen ssh config alias and its `ssh -G` settings.
/// Host/user are left empty on purpose: the alias owns them.
pub fn profile_from_ssh_alias(alias: &str, settings: &SshHostSettings) -> ServerProfile {
    ServerProfile {
        name: settings
            .hostname
            .clone()
            .unwrap_or_else(|| alias.to_string()),
        ssh_alias: Some(alias.to_string()),
        host: None,
        user: None,
        data_roots: Vec::new(),
        scratch_template: String::new(),
        results_root: String::new(),
        code_roots: Vec::new(),
        scheduler: SchedulerKind::None,
        defaults: Default::default(),
        environment: EnvironmentSetup::default(),
        notes: None,
    }
}

/// Builds a profile from a typed host and user. No secret is accepted here.
pub fn profile_from_typed_host(name: &str, host: &str, user: Option<&str>) -> ServerProfile {
    ServerProfile {
        name: name.to_string(),
        ssh_alias: None,
        host: Some(host.trim().to_string()),
        user: user
            .map(str::trim)
            .filter(|user| !user.is_empty())
            .map(str::to_string),
        data_roots: Vec::new(),
        scratch_template: String::new(),
        results_root: String::new(),
        code_roots: Vec::new(),
        scheduler: SchedulerKind::None,
        defaults: Default::default(),
        environment: EnvironmentSetup::default(),
        notes: None,
    }
}

/// Environment setup proposals based on what was detected. The user accepts or
/// overrides every value.
pub fn proposed_environment(discovered: &DiscoveredEnvironment) -> EnvironmentSetup {
    let mut setup = EnvironmentSetup::default();
    if discovered.conda.is_some() {
        setup.kind = EnvironmentKind::Conda;
        setup.interpreter = discovered.python.clone();
    } else if let Some(virtualenv) = discovered.virtualenv.as_deref() {
        setup.kind = EnvironmentKind::Venv;
        // Prefer the interpreter that is actually inside the virtualenv.
        setup.interpreter = discovered
            .python
            .clone()
            .filter(|python| python.starts_with(virtualenv))
            .or_else(|| Some(format!("{}/bin/python", virtualenv.trim_end_matches('/'))));
    } else if discovered.module_system {
        setup.kind = EnvironmentKind::Modules;
    }
    if setup.kind == EnvironmentKind::Modules {
        setup.interpreter = None;
    }
    if setup.interpreter.is_none() {
        setup.interpreter = discovered.python.clone();
    }
    setup.container_image = discovered.container_runtime.as_ref().map(|_| String::new());
    setup.coperceive_command = discovered
        .coperceive_path
        .clone()
        .map(|path| vec![path])
        .unwrap_or_else(|| vec!["coperceive".to_string()]);
    setup.compute_has_internet = false;
    setup
}

fn new_server_id() -> String {
    use std::io::Read;
    let mut bytes = [0u8; 8];
    let ok = std::fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .is_ok();
    if ok {
        return bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    }
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    format!("{nanos:x}{:x}", std::process::id())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{EnvironmentKind, JobDefaults};

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "coperceive-servers-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|duration| duration.as_nanos())
                .unwrap_or(0)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    fn sample_entry(name: &str, alias: &str) -> ServerEntry {
        ServerEntry::new(ServerProfile {
            name: name.to_string(),
            ssh_alias: Some(alias.to_string()),
            host: None,
            user: None,
            data_roots: vec!["/data/scenes".to_string()],
            scratch_template: "/scratch/$USER".to_string(),
            results_root: "/data/projects/runs".to_string(),
            code_roots: Vec::new(),
            scheduler: SchedulerKind::Slurm,
            defaults: JobDefaults {
                partition: Some("gpu-a100".to_string()),
                ..Default::default()
            },
            environment: EnvironmentSetup::default(),
            notes: None,
        })
    }

    #[test]
    fn save_and_load_multiple_profiles_round_trip() {
        let dir = temp_dir("roundtrip");
        let path = dir.join("servers.json");
        let entries = vec![
            sample_entry("Cluster A", "cluster-a"),
            sample_entry("Cluster B", "cluster-b"),
        ];
        save_servers_to_path(&entries, &path).expect("save");
        let loaded = load_servers_from_path(&path);
        assert_eq!(loaded, entries);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path)
                .expect("metadata")
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600, "server file must not be world readable");
            let dir_mode = std::fs::metadata(&dir)
                .expect("dir metadata")
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(dir_mode, 0o700);
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_file_loads_as_empty_instead_of_panicking() {
        let dir = temp_dir("corrupt");
        let path = dir.join("servers.json");
        std::fs::write(&path, "{ not json").expect("write");
        assert!(load_servers_from_path(&path).is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn secret_like_json_keys_are_detected_and_never_stored() {
        let value = serde_json::json!({
            "profile": {"password": "hunter2"},
            "nested": [{"private_key": "..."}],
        });
        assert!(
            first_secret_like_key(&value, "").is_some(),
            "a credential-like key must be reported"
        );
        assert_eq!(
            first_secret_like_key(&value, "").as_deref(),
            Some("nested[0].private_key")
        );

        // The shared profile refuses unknown fields entirely, so a credential
        // cannot even be deserialised into it.
        let parsed = serde_json::from_value::<ServerProfile>(serde_json::json!({
            "name": "Cluster A",
            "password": "hunter2",
            "data_roots": ["/data"],
            "scratch_template": "/scratch/$USER",
            "scheduler": "slurm",
            "defaults": {},
            "environment": {"kind": "none"},
        }));
        assert!(parsed.is_err(), "credential-shaped keys are refused");
    }

    #[test]
    fn rejects_path_parent_escapes_and_control_characters() {
        assert!(validate_inside_roots("/data/scenes", "data folder").is_ok());
        assert!(validate_inside_roots("/data/my scenes", "data folder").is_ok());
        assert!(validate_inside_roots("/data/x; touch /tmp/x", "data folder").is_ok());
        assert!(validate_inside_roots("/data/../../etc", "data folder").is_err());
        assert!(validate_inside_roots("/data/a\nb", "data folder").is_err());

        let mut entry = sample_entry("Cluster A", "cluster-a");
        entry.profile.data_roots = vec!["/data/../root".to_string()];
        assert!(entry.validate().is_err());
    }

    #[test]
    fn rejects_schedulerless_profile_with_partition() {
        let mut entry = sample_entry("Files only", "files-only");
        entry.profile.scheduler = SchedulerKind::None;
        entry.profile.defaults.partition = Some("gpu-a100".to_string());
        let err = entry.validate().expect_err("must reject inconsistent profile");
        assert!(err.contains("no scheduler"));
    }

    #[test]
    fn shared_profile_validation_is_applied() {
        let mut entry = sample_entry("Cluster A", "cluster-a");
        entry.profile.data_roots.clear();
        assert!(entry.validate().is_err());
        let mut both = sample_entry("Cluster A", "cluster-a");
        both.profile.host = Some("also.example.org".to_string());
        assert!(both.validate().is_err(), "alias and host are exclusive");
    }

    #[test]
    fn parse_ssh_config_hosts_skips_wildcards_and_negations() {
        let config = r#"
# ~/.ssh/config
Host *
    ServerAliveInterval 60

Host cluster-a
    HostName cluster-a.example.org
    User alice

Host cluster-b jump-host !excluded
    ProxyJump jump.example.org

host lowercase
    HostName lower.example.org
"#;
        assert_eq!(
            parse_ssh_config_hosts(config),
            vec![
                "cluster-a".to_string(),
                "cluster-b".to_string(),
                "jump-host".to_string(),
                "lowercase".to_string()
            ]
        );
    }

    #[test]
    fn parse_ssh_g_output_reads_effective_settings() {
        let output = "\
host cluster-a
user alice
hostname cluster-a.example.org
port 2222
proxyjump jump.example.org
proxycommand none
identityfile ~/.ssh/id_ed25519
";
        let settings = parse_ssh_g_output("cluster-a", output);
        assert_eq!(settings.hostname.as_deref(), Some("cluster-a.example.org"));
        assert_eq!(settings.user.as_deref(), Some("alice"));
        assert_eq!(settings.port, Some(2222));
        assert_eq!(settings.proxy_jump.as_deref(), Some("jump.example.org"));
        assert_eq!(settings.proxy_command, None);
    }

    #[test]
    fn profile_from_ssh_alias_keeps_alias_and_no_credentials() {
        let settings = parse_ssh_g_output(
            "cluster-a",
            "hostname cluster-a.example.org\nuser alice\nport 22\n",
        );
        let profile = profile_from_ssh_alias("cluster-a", &settings);
        assert_eq!(profile.ssh_alias.as_deref(), Some("cluster-a"));
        assert_eq!(profile.host, None);
        assert!(profile.data_roots.is_empty(), "the user chooses folders");
        let json = serde_json::to_string(&profile).expect("serialize");
        assert!(!json.to_lowercase().contains("password"));
        assert!(!json.to_lowercase().contains("private"));
        assert!(!json.contains("alice@cluster-a.example.org"));
    }

    #[test]
    fn ssh_target_uses_alias_first_then_typed_host() {
        let mut entry = sample_entry("Cluster A", "cluster-a");
        entry.profile.host = None;
        assert_eq!(entry.ssh_target().expect("target").destination, "cluster-a");

        let mut typed = ServerEntry::new(profile_from_typed_host(
            "Typed",
            "cluster-a.example.org",
            Some("alice"),
        ));
        typed.ssh = Some(SshHostSettings {
            alias: "typed".to_string(),
            port: Some(2222),
            ..Default::default()
        });
        let target = typed.ssh_target().expect("typed target");
        assert_eq!(target.destination, "alice@cluster-a.example.org");
        assert_eq!(target.port, Some(2222));
    }

    #[test]
    fn proposed_environment_prefers_conda_then_venv_then_modules() {
        let conda = DiscoveredEnvironment {
            conda: Some("/opt/conda/bin/conda".to_string()),
            module_system: true,
            python: Some("/usr/bin/python3".to_string()),
            ..Default::default()
        };
        let setup = proposed_environment(&conda);
        assert_eq!(setup.kind, EnvironmentKind::Conda);

        let venv = DiscoveredEnvironment {
            virtualenv: Some("/home/alice/venvs/coperceive".to_string()),
            module_system: true,
            ..Default::default()
        };
        let setup = proposed_environment(&venv);
        assert_eq!(setup.kind, EnvironmentKind::Venv);
        assert_eq!(
            setup.interpreter.as_deref(),
            Some("/home/alice/venvs/coperceive/bin/python")
        );

        let modules = DiscoveredEnvironment {
            module_system: true,
            ..Default::default()
        };
        let setup = proposed_environment(&modules);
        assert_eq!(setup.kind, EnvironmentKind::Modules);
        assert!(
            !setup.compute_has_internet,
            "compute nodes are offline by default"
        );
    }
}
