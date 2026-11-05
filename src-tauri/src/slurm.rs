// SPDX-License-Identifier: Apache-2.0

#[derive(Debug, PartialEq, Eq, Clone)]
pub struct SlurmJobInfo {
    pub job_id: String,
    pub job_name: String,
    pub state: String,
    pub reason: String,
    pub time_elapsed: String,
    pub time_limit: String,
    pub nodelist: String,
}

pub const DESKTOP_JOB_PREFIX: &str = "coperceive-desktop-";

/// Returns whether a Slurm job name matches the Coperceive desktop session convention.
pub fn is_coperceive_desktop_job(name: &str) -> bool {
    name.starts_with(DESKTOP_JOB_PREFIX)
}

/// Parses the output of `squeue -h -o "%i|%j|%T|%r|%M|%l|%N"` into structured records.
pub fn parse_squeue_output(output: &str) -> Vec<SlurmJobInfo> {
    let mut jobs = Vec::new();
    for line in output.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with("JOBID|") {
            continue;
        }
        let parts: Vec<&str> = line.split('|').map(|s| s.trim()).collect();
        if parts.len() >= 7 {
            jobs.push(SlurmJobInfo {
                job_id: parts[0].to_string(),
                job_name: parts[1].to_string(),
                state: parts[2].to_string(),
                reason: parts[3].to_string(),
                time_elapsed: parts[4].to_string(),
                time_limit: parts[5].to_string(),
                nodelist: parts[6].to_string(),
            });
        }
    }
    jobs
}

/// Filters for leftover `coperceive-desktop-*` jobs not matching the current session UUID.
pub fn find_orphaned_desktop_jobs(jobs: &[SlurmJobInfo], current_uuid: &str) -> Vec<SlurmJobInfo> {
    let current_job_name = format!("{}{}", DESKTOP_JOB_PREFIX, current_uuid);
    jobs.iter()
        .filter(|j| is_coperceive_desktop_job(&j.job_name) && j.job_name != current_job_name)
        .cloned()
        .collect()
}

/// Parses a time string formatted as [[days-]hours:]minutes:seconds into total seconds.
pub fn parse_slurm_duration(duration: &str) -> Option<u64> {
    let mut s = duration.trim();
    if s.is_empty() || s == "UNLIMITED" || s == "NOT_SET" {
        return None;
    }

    let mut days = 0u64;
    if let Some((d_str, rem)) = s.split_once('-') {
        days = d_str.parse().ok()?;
        s = rem;
    }

    let parts: Vec<&str> = s.split(':').collect();
    let (hours, mins, secs): (u64, u64, u64) = match parts.len() {
        1 => (0, 0, parts[0].parse::<u64>().ok()?),
        2 => (0, parts[0].parse::<u64>().ok()?, parts[1].parse::<u64>().ok()?),
        3 => (parts[0].parse::<u64>().ok()?, parts[1].parse::<u64>().ok()?, parts[2].parse::<u64>().ok()?),
        _ => return None,
    };

    Some(days * 86400 + hours * 3600 + mins * 60 + secs)
}

/// Checks whether a running job has less than `warning_threshold_secs` (e.g. 600s = 10 min) remaining.
pub fn is_near_walltime_expiry(time_elapsed: &str, time_limit: &str, warning_threshold_secs: u64) -> bool {
    if let (Some(elapsed), Some(limit)) = (parse_slurm_duration(time_elapsed), parse_slurm_duration(time_limit)) {
        if limit > elapsed {
            return (limit - elapsed) <= warning_threshold_secs;
        }
    }
    false
}

/// Parses credentials output file (`NODE=<ip>\nPORT=<port>\nTOKEN=<token>`).
pub fn parse_credentials_file(contents: &str) -> Result<(String, u16, String), String> {
    let mut node = None;
    let mut port = None;
    let mut token = None;

    for line in contents.lines() {
        let line = line.trim();
        if let Some((k, v)) = line.split_once('=') {
            match k {
                "NODE" => node = Some(v.to_string()),
                "PORT" => port = v.parse::<u16>().ok(),
                "TOKEN" => token = Some(v.to_string()),
                _ => {}
            }
        }
    }

    match (node, port, token) {
        (Some(n), Some(p), Some(t)) => Ok((n, p, t)),
        _ => Err("Invalid or incomplete credentials file format".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_squeue_output() {
        let sample = r#"
JOBID|NAME|STATE|REASON|TIME|TIME_LIMIT|NODELIST
631300|coperceive-desktop-abc-123|RUNNING|None|00:15:30|02:00:00|hpc-node11
631301|coperceive-desktop-old-456|PENDING|Priority|00:00:12|02:00:00|
631302|other-job|RUNNING|None|01:05:00|04:00:00|hpc-node12
"#;
        let jobs = parse_squeue_output(sample);
        assert_eq!(jobs.len(), 3);
        assert_eq!(jobs[0].job_id, "631300");
        assert_eq!(jobs[0].state, "RUNNING");
        assert_eq!(jobs[0].nodelist, "hpc-node11");

        assert_eq!(jobs[1].state, "PENDING");
        assert_eq!(jobs[1].reason, "Priority");

        let orphaned = find_orphaned_desktop_jobs(&jobs, "abc-123");
        assert_eq!(orphaned.len(), 1);
        assert_eq!(orphaned[0].job_id, "631301");
        assert_eq!(orphaned[0].job_name, "coperceive-desktop-old-456");
    }

    #[test]
    fn test_parse_slurm_duration() {
        assert_eq!(parse_slurm_duration("45"), Some(45));
        assert_eq!(parse_slurm_duration("10:30"), Some(630));
        assert_eq!(parse_slurm_duration("02:00:00"), Some(7200));
        assert_eq!(parse_slurm_duration("1-02:00:00"), Some(86400 + 7200));
        assert_eq!(parse_slurm_duration("INVALID"), None);
    }

    #[test]
    fn test_near_walltime_expiry() {
        // Limit 2 hours (7200s), Elapsed 1h 52m (6720s) -> Remaining 480s <= 600s
        assert!(is_near_walltime_expiry("01:52:00", "02:00:00", 600));

        // Limit 2 hours (7200s), Elapsed 1h 30m (5400s) -> Remaining 1800s > 600s
        assert!(!is_near_walltime_expiry("01:30:00", "02:00:00", 600));
    }

    #[test]
    fn test_parse_credentials_file() {
        let content = "NODE=10.20.240.48\nPORT=49812\nTOKEN=a1b2c3d4e5f6\n";
        let (node, port, token) = parse_credentials_file(content).expect("valid parse");
        assert_eq!(node, "10.20.240.48");
        assert_eq!(port, 49812);
        assert_eq!(token, "a1b2c3d4e5f6");
    }

}
