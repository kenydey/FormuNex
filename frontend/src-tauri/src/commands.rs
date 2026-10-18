//! Commands invoked from the UI (`invoke("…")`, see frontend/src/desktop/bridge.ts).

use serde::Serialize;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::config::{DesktopConfig, Mode};
use crate::docker::{find_docker, Docker, DockerStatus, ServiceState};
use crate::error::{DesktopError, Result};
use crate::health::{self, Health};
use crate::secrets;
use crate::stack::{self, BackupInfo, StackFiles};

/// Event carrying one line of `docker compose` output while a long operation runs.
pub const LOG_EVENT: &str = "stack://log";

pub struct AppState {
    pub config_dir: PathBuf,
    pub data_dir: PathBuf,
    /// Serialises stack operations (up / down / upgrade / import) so two clicks cannot interleave.
    pub busy: Mutex<()>,
}

impl AppState {
    pub fn new(app: &AppHandle) -> Result<Self> {
        let paths = app.path();
        let config_dir = paths.app_config_dir().map_err(|e| DesktopError::Config(e.to_string()))?;
        let data_dir = paths.app_data_dir().map_err(|e| DesktopError::Config(e.to_string()))?.join("stack");
        Ok(Self { config_dir, data_dir, busy: Mutex::new(()) })
    }

    fn config(&self) -> Result<DesktopConfig> {
        DesktopConfig::load(&self.config_dir)
    }

    fn files(&self) -> StackFiles {
        StackFiles::new(&self.data_dir)
    }

    fn docker(&self, cfg: &DesktopConfig) -> Result<Docker> {
        let bin =
            find_docker(cfg.docker_path.as_deref(), std::env::var_os("PATH")).ok_or(DesktopError::DockerMissing)?;
        Ok(self.files().docker(bin))
    }
}

#[derive(Serialize)]
pub struct DesktopInfo {
    pub config: DesktopConfig,
    pub api_base: Option<String>,
    pub data_dir: String,
    pub config_dir: String,
    pub app_version: String,
    pub platform: String,
}

#[tauri::command]
pub fn desktop_info(state: State<'_, AppState>) -> Result<DesktopInfo> {
    let config = state.config()?;
    Ok(DesktopInfo {
        api_base: config.api_base(),
        config,
        data_dir: state.data_dir.display().to_string(),
        config_dir: state.config_dir.display().to_string(),
        app_version: env!("CARGO_PKG_VERSION").to_string(),
        platform: std::env::consts::OS.to_string(),
    })
}

#[tauri::command]
pub fn save_config(state: State<'_, AppState>, config: DesktopConfig) -> Result<DesktopConfig> {
    let config = config.validated()?;
    config.save(&state.config_dir)?;
    Ok(config)
}

#[tauri::command]
pub async fn docker_status(state: State<'_, AppState>) -> Result<DockerStatus> {
    let cfg = state.config()?;
    match state.docker(&cfg) {
        Ok(docker) => Ok(tauri::async_runtime::spawn_blocking(move || docker.status()).await.unwrap_or_default()),
        Err(_) => Ok(DockerStatus {
            installed: false,
            message: Some(DesktopError::DockerMissing.to_string()),
            ..Default::default()
        }),
    }
}

#[derive(Serialize)]
pub struct StackStatus {
    pub services: Vec<ServiceState>,
    pub health: Health,
}

#[tauri::command]
pub async fn stack_status(state: State<'_, AppState>) -> Result<StackStatus> {
    let cfg = state.config()?;
    let base = cfg.api_base().unwrap_or_else(|| format!("http://127.0.0.1:{}", cfg.api_port));
    let services = match state.docker(&cfg) {
        Ok(docker) if state.files().compose().exists() => {
            tauri::async_runtime::spawn_blocking(move || docker.services())
                .await
                .unwrap_or(Ok(vec![]))
                .unwrap_or_default()
        }
        _ => vec![],
    };
    Ok(StackStatus { services, health: health::probe(&base).await })
}

#[tauri::command]
pub async fn probe_health(base: String) -> Result<Health> {
    Ok(health::probe(&base).await)
}

/// Run a stack operation on a blocking thread, streaming its output to the UI.
async fn streamed<F>(app: AppHandle, op: F) -> Result<()>
where
    F: FnOnce(&AppState, &Docker, &mut dyn FnMut(&str)) -> Result<()> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        let _guard = state.busy.try_lock().map_err(|_| DesktopError::Config("另一个栈操作正在进行".into()))?;
        let cfg = state.config()?;
        let docker = state.docker(&cfg)?;
        state.files().prepare(&cfg)?;
        let mut emit = |line: &str| {
            let _ = app.emit(LOG_EVENT, line.to_string());
        };
        op(&state, &docker, &mut emit)
    })
    .await
    .map_err(|e| DesktopError::Config(e.to_string()))?
}

#[tauri::command]
pub async fn stack_up(app: AppHandle) -> Result<()> {
    streamed(app, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["up", "-d", "--remove-orphans"]), emit))
        .await
}

#[tauri::command]
pub async fn stack_down(app: AppHandle) -> Result<()> {
    streamed(app, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["down"]), emit)).await
}

#[tauri::command]
pub async fn stack_restart(app: AppHandle) -> Result<()> {
    streamed(app, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["restart"]), emit)).await
}

#[tauri::command]
pub async fn stack_pull(app: AppHandle) -> Result<()> {
    streamed(app, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["pull", "--ignore-buildable"]), emit))
        .await
}

/// Offline install: `docker load -i formunex-images.tar` (方案 §2.2).
#[tauri::command]
pub async fn import_images(app: AppHandle, path: String) -> Result<()> {
    let tar = PathBuf::from(&path);
    if !tar.is_file() {
        return Err(DesktopError::Config(format!("镜像包不存在：{path}")));
    }
    streamed(app, move |_, docker, emit| {
        docker.run_streaming(&["load".into(), "-i".into(), tar.display().to_string()], emit)
    })
    .await
}

#[tauri::command]
pub async fn stack_logs(state: State<'_, AppState>, service: Option<String>, tail: Option<u32>) -> Result<String> {
    let cfg = state.config()?;
    let docker = state.docker(&cfg)?;
    let tail = tail.unwrap_or(300).clamp(10, 5000).to_string();
    let mut args = vec!["logs", "--no-color", "--tail", tail.as_str()];
    let service = service.filter(|s| s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
    if let Some(s) = service.as_deref() {
        args.push(s);
    }
    let args = docker.compose_args(&args);
    let out = tauri::async_runtime::spawn_blocking(move || docker.run(&args, &[]))
        .await
        .map_err(|e| DesktopError::Config(e.to_string()))??;
    Ok(format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr)))
}

#[tauri::command]
pub async fn backup_now(app: AppHandle) -> Result<BackupInfo> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        let _guard = state.busy.try_lock().map_err(|_| DesktopError::Config("另一个栈操作正在进行".into()))?;
        let cfg = state.config()?;
        let docker = state.docker(&cfg)?;
        stack::backup(&state.files(), &docker, "manual")
    })
    .await
    .map_err(|e| DesktopError::Config(e.to_string()))?
}

#[tauri::command]
pub fn list_backups(state: State<'_, AppState>) -> Vec<BackupInfo> {
    state.files().list_backups()
}

/// Upgrade the stack images: pg_dump first (refuses to continue if the backup fails while the database
/// runs), then switch the tag, pull and recreate.
#[tauri::command]
pub async fn stack_upgrade(app: AppHandle, version: String) -> Result<DesktopConfig> {
    if !crate::config::valid_tag(&version) {
        return Err(DesktopError::Config(format!("镜像版本 “{version}” 不合法")));
    }
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        let _guard = state.busy.try_lock().map_err(|_| DesktopError::Config("另一个栈操作正在进行".into()))?;
        let mut cfg = state.config()?;
        let docker = state.docker(&cfg)?;
        let files = state.files();
        let emit = |line: &str| {
            let _ = app.emit(LOG_EVENT, line.to_string());
        };
        let db_running =
            docker.services().unwrap_or_default().iter().any(|s| s.service == "postgres" && s.state == "running");
        if db_running {
            emit("备份数据库（pg_dump）…");
            let b = stack::backup(&files, &docker, &format!("pre-{version}"))?;
            emit(&format!("已备份：{}", b.path));
        }
        cfg.version = version.clone();
        let cfg = cfg.validated()?;
        cfg.save(&state.config_dir)?;
        files.prepare(&cfg)?;
        docker.run_streaming(&docker.compose_args(&["pull", "--ignore-buildable"]), emit)?;
        docker.run_streaming(&docker.compose_args(&["up", "-d", "--remove-orphans"]), emit)?;
        Ok(cfg)
    })
    .await
    .map_err(|e| DesktopError::Config(e.to_string()))?
}

/// Create the first administrator; the generated password goes to the keychain and back to the UI once.
#[tauri::command]
pub async fn create_admin(app: AppHandle, username: String, display_name: String) -> Result<String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        let cfg = state.config()?;
        if cfg.mode != Some(Mode::Local) {
            return Err(DesktopError::Config("只有本机栈需要创建管理员".into()));
        }
        let docker = state.docker(&cfg)?;
        let password = stack::random_secret(20);
        stack::create_admin(&docker, &username, &display_name, &password)?;
        if let Err(e) = secrets::set(secrets::ADMIN_PASSWORD, &password) {
            let _ = app.emit(LOG_EVENT, format!("管理员密码未能写入钥匙串：{e}"));
        }
        Ok(password)
    })
    .await
    .map_err(|e| DesktopError::Config(e.to_string()))?
}

#[tauri::command]
pub fn keychain_get(key: String) -> Result<Option<String>> {
    secrets::get(&key)
}

#[tauri::command]
pub fn keychain_set(key: String, value: String) -> Result<()> {
    secrets::set(&key, &value)
}

#[tauri::command]
pub fn keychain_delete(key: String) -> Result<()> {
    secrets::delete(&key)
}
