//! Thin wrapper over the `docker` CLI (方案 §3.5 “栈编排”): the shell never talks to the Docker API
//! directly, so whatever Docker Desktop / Docker Engine the user runs is what we drive.

use serde::Serialize;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use crate::error::{DesktopError, Result};

/// Compose project name: containers are `formunex-api-1`, volumes `formunex_fx-postgres`, …
pub const PROJECT: &str = "formunex";

#[derive(Clone, Debug)]
pub struct Docker {
    pub bin: PathBuf,
    pub compose_file: PathBuf,
    pub env_file: PathBuf,
}

#[derive(Serialize, Clone, Debug, Default, PartialEq, Eq)]
pub struct DockerStatus {
    pub installed: bool,
    pub path: Option<String>,
    pub client_version: Option<String>,
    /// `None` when the daemon (Docker Desktop) is not running.
    pub server_version: Option<String>,
    pub compose_version: Option<String>,
    pub mem_total_bytes: Option<u64>,
    pub cpus: Option<u64>,
    pub message: Option<String>,
}

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct ServiceState {
    pub service: String,
    pub state: String,
    pub health: String,
    pub status: String,
}

/// Where Docker usually lives when the app is started from the Dock / Start menu, whose PATH does not
/// include the shell's.
fn well_known_locations() -> Vec<PathBuf> {
    if cfg!(target_os = "windows") {
        vec![
            PathBuf::from(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"),
            PathBuf::from(r"C:\ProgramData\DockerDesktop\version-bin\docker.exe"),
        ]
    } else if cfg!(target_os = "macos") {
        vec![
            PathBuf::from("/usr/local/bin/docker"),
            PathBuf::from("/opt/homebrew/bin/docker"),
            PathBuf::from("/Applications/Docker.app/Contents/Resources/bin/docker"),
        ]
    } else {
        vec![PathBuf::from("/usr/bin/docker"), PathBuf::from("/usr/local/bin/docker")]
    }
}

fn exe_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "docker.exe"
    } else {
        "docker"
    }
}

/// Find the docker executable: an explicit override, then PATH, then the usual install locations.
pub fn find_docker(override_path: Option<&str>, path_var: Option<OsString>) -> Option<PathBuf> {
    if let Some(p) = override_path.filter(|p| !p.trim().is_empty()) {
        let p = PathBuf::from(p.trim());
        return p.is_file().then_some(p);
    }
    if let Some(paths) = path_var {
        for dir in std::env::split_paths(&paths) {
            let candidate = dir.join(exe_name());
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    well_known_locations().into_iter().find(|p| p.is_file())
}

fn command(bin: &Path) -> Command {
    let mut cmd = Command::new(bin);
    // Docker Desktop's credential helpers live next to docker; make sure they are on PATH even when the app
    // was launched without the user's shell environment.
    if let Some(dir) = bin.parent() {
        let mut paths: Vec<PathBuf> = vec![dir.to_path_buf()];
        if let Some(existing) = std::env::var_os("PATH") {
            paths.extend(std::env::split_paths(&existing));
        }
        if let Ok(joined) = std::env::join_paths(paths) {
            cmd.env("PATH", joined);
        }
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

fn describe(args: &[String]) -> String {
    let shown: Vec<&str> = args.iter().map(String::as_str).take(6).collect();
    format!("docker {}", shown.join(" "))
}

fn check(args: &[String], out: Output) -> Result<Output> {
    if out.status.success() {
        return Ok(out);
    }
    let stderr = String::from_utf8_lossy(&out.stderr);
    let stdout = String::from_utf8_lossy(&out.stdout);
    let msg = if stderr.trim().is_empty() { stdout } else { stderr };
    let tail: Vec<&str> = msg.trim().lines().rev().take(8).collect();
    Err(DesktopError::Docker {
        command: describe(args),
        message: tail.into_iter().rev().collect::<Vec<_>>().join("\n"),
    })
}

impl Docker {
    pub fn new(bin: PathBuf, compose_file: PathBuf, env_file: PathBuf) -> Self {
        Self { bin, compose_file, env_file }
    }

    /// `docker compose -p formunex -f <file> --env-file <env> <extra…>`
    pub fn compose_args<S: AsRef<str>>(&self, extra: &[S]) -> Vec<String> {
        let mut args = vec![
            "compose".to_string(),
            "-p".into(),
            PROJECT.into(),
            "-f".into(),
            self.compose_file.to_string_lossy().into_owned(),
            "--env-file".into(),
            self.env_file.to_string_lossy().into_owned(),
        ];
        args.extend(extra.iter().map(|s| s.as_ref().to_string()));
        args
    }

    pub fn run(&self, args: &[String], envs: &[(&str, &str)]) -> Result<Output> {
        let mut cmd = command(&self.bin);
        cmd.args(args).stdin(Stdio::null());
        for (k, v) in envs {
            cmd.env(k, v);
        }
        let out = cmd.output().map_err(|e| DesktopError::Docker { command: describe(args), message: e.to_string() })?;
        check(args, out)
    }

    /// Run and hand every output line (stdout and stderr) to `on_line` as it arrives — for pulls and
    /// `up`, which can take minutes.
    pub fn run_streaming(&self, args: &[String], mut on_line: impl FnMut(&str)) -> Result<()> {
        let mut child = command(&self.bin)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| DesktopError::Docker { command: describe(args), message: e.to_string() })?;
        let stderr = child.stderr.take().expect("piped");
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        let tx_err = tx.clone();
        let err_thread = std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(std::result::Result::ok) {
                let _ = tx_err.send(line);
            }
        });
        let stdout = child.stdout.take().expect("piped");
        let out_thread = std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(std::result::Result::ok) {
                let _ = tx.send(line);
            }
        });
        let mut last = Vec::new();
        for line in rx {
            on_line(&line);
            last.push(line);
            if last.len() > 20 {
                last.remove(0);
            }
        }
        let _ = out_thread.join();
        let _ = err_thread.join();
        let status = child.wait()?;
        if status.success() {
            Ok(())
        } else {
            Err(DesktopError::Docker { command: describe(args), message: last.join("\n") })
        }
    }

    pub fn compose(&self, extra: &[&str]) -> Result<Output> {
        self.run(&self.compose_args(extra), &[])
    }

    pub fn status(&self) -> DockerStatus {
        let mut st = DockerStatus { installed: true, path: Some(self.bin.display().to_string()), ..Default::default() };
        let fmt = |f: &str| vec!["version".to_string(), "--format".into(), f.into()];
        if let Ok(out) = self.run(&fmt("{{.Client.Version}}"), &[]) {
            st.client_version = Some(String::from_utf8_lossy(&out.stdout).trim().to_string());
        }
        match self.run(&["info".into(), "--format".into(), "{{json .}}".into()], &[]) {
            Ok(out) => {
                let info: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap_or_default();
                st.server_version = info["ServerVersion"].as_str().map(str::to_string);
                st.mem_total_bytes = info["MemTotal"].as_u64();
                st.cpus = info["NCPU"].as_u64();
                if st.server_version.is_none() {
                    st.message = Some("Docker 引擎未运行：请启动 Docker Desktop".into());
                }
            }
            Err(e) => st.message = Some(format!("Docker 引擎未运行：请启动 Docker Desktop（{e}）")),
        }
        if let Ok(out) = self.run(&["compose".into(), "version".into(), "--short".into()], &[]) {
            st.compose_version = Some(String::from_utf8_lossy(&out.stdout).trim().to_string());
        } else if st.server_version.is_some() {
            st.message = Some("缺少 docker compose v2 插件（Docker Desktop 自带）".into());
        }
        st
    }

    pub fn services(&self) -> Result<Vec<ServiceState>> {
        let out = self.compose(&["ps", "--all", "--format", "json"])?;
        Ok(parse_ps(&String::from_utf8_lossy(&out.stdout)))
    }
}

/// `docker compose ps --format json` prints a JSON array (compose < 2.21) or one object per line (newer).
pub fn parse_ps(text: &str) -> Vec<ServiceState> {
    let text = text.trim();
    let values: Vec<serde_json::Value> = if text.starts_with('[') {
        serde_json::from_str(text).unwrap_or_default()
    } else {
        text.lines().filter_map(|l| serde_json::from_str(l.trim()).ok()).collect()
    };
    let mut out: Vec<ServiceState> = values
        .iter()
        .map(|v| ServiceState {
            service: v["Service"].as_str().unwrap_or_default().to_string(),
            state: v["State"].as_str().unwrap_or_default().to_string(),
            health: v["Health"].as_str().unwrap_or_default().to_string(),
            status: v["Status"].as_str().unwrap_or_default().to_string(),
        })
        .filter(|s| !s.service.is_empty())
        .collect();
    out.sort_by(|a, b| a.service.cmp(&b.service));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn docker() -> Docker {
        Docker::new(PathBuf::from("docker"), PathBuf::from("/data/compose.yml"), PathBuf::from("/data/.env"))
    }

    #[test]
    fn compose_args_pin_project_file_and_env() {
        assert_eq!(
            docker().compose_args(&["up", "-d"]),
            ["compose", "-p", "formunex", "-f", "/data/compose.yml", "--env-file", "/data/.env", "up", "-d"]
        );
    }

    #[test]
    fn parses_both_ps_formats() {
        let array = r#"[{"Service":"api","State":"running","Health":"healthy","Status":"Up 2 minutes (healthy)"},
                        {"Service":"migrate","State":"exited","Health":"","Status":"Exited (0)"}]"#;
        let lines = "{\"Service\":\"redis\",\"State\":\"running\",\"Health\":\"healthy\",\"Status\":\"Up\"}\n\
                     {\"Service\":\"api\",\"State\":\"starting\",\"Health\":\"starting\",\"Status\":\"Up 3s\"}\n";
        let a = parse_ps(array);
        assert_eq!(a.len(), 2);
        assert_eq!(a[0].service, "api");
        assert_eq!(a[0].health, "healthy");
        let b = parse_ps(lines);
        assert_eq!(b.iter().map(|s| s.service.as_str()).collect::<Vec<_>>(), ["api", "redis"]);
        assert!(parse_ps("").is_empty());
        assert!(parse_ps("garbage").is_empty());
    }

    #[test]
    fn override_path_must_exist() {
        let dir = tempfile::tempdir().unwrap();
        let fake = dir.path().join(exe_name());
        std::fs::write(&fake, "").unwrap();
        assert_eq!(find_docker(Some(fake.to_str().unwrap()), None), Some(fake.clone()));
        assert_eq!(find_docker(Some("/nope/docker"), None), None);
        assert_eq!(find_docker(None, Some(dir.path().as_os_str().to_owned())), Some(fake));
    }

    #[cfg(unix)]
    #[test]
    fn run_reports_failures_with_stderr_and_streams_lines() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let bin = dir.path().join("docker");
        std::fs::write(&bin, "#!/bin/sh\necho \"out:$*\"\nif [ \"$1\" = fail ]; then echo boom >&2; exit 3; fi\n")
            .unwrap();
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
        let d = Docker::new(bin, dir.path().join("c.yml"), dir.path().join(".env"));
        let ok = d.run(&["ps".into()], &[]).unwrap();
        assert_eq!(String::from_utf8_lossy(&ok.stdout).trim(), "out:ps");
        match d.run(&["fail".into()], &[]) {
            Err(DesktopError::Docker { message, .. }) => assert!(message.contains("boom")),
            other => panic!("{other:?}"),
        }
        let mut seen = Vec::new();
        d.run_streaming(&["pull".into()], |l| seen.push(l.to_string())).unwrap();
        assert_eq!(seen, ["out:pull"]);
        assert!(d.run_streaming(&["fail".into()], |_| {}).is_err());
    }
}
