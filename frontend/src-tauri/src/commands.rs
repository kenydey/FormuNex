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
        // P3-E16: a JoinError (blocking task died) is an error, not
        // "Docker is not installed" — the old unwrap_or_default lied.
        Ok(docker) => Ok(tauri::async_runtime::spawn_blocking(move || docker.status())
            .await
            .map_err(|e| DesktopError::Config(e.to_string()))?),
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
    /// P2-E13: why `services` is empty, when it is an error rather than
    /// "no compose file yet". The UI shows this instead of a silent
    /// empty table (a failed `docker compose ps` used to look identical
    /// to a fresh install).
    pub services_error: Option<String>,
}

#[tauri::command]
pub async fn stack_status(state: State<'_, AppState>) -> Result<StackStatus> {
    let cfg = state.config()?;
    let base = cfg.api_base().unwrap_or_else(|| format!("http://127.0.0.1:{}", cfg.api_port));
    let (services, services_error) = match state.docker(&cfg) {
        Ok(docker) if state.files().compose().exists() => {
            match tauri::async_runtime::spawn_blocking(move || docker.services()).await {
                Ok(Ok(s)) => (s, None),
                Ok(Err(e)) => (vec![], Some(e.to_string())),
                Err(e) => (vec![], Some(e.to_string())),
            }
        }
        Ok(_) => (vec![], None),
        Err(e) => (vec![], Some(e.to_string())),
    };
    Ok(StackStatus { services, health: health::probe(&base).await, services_error })
}

#[tauri::command]
pub async fn probe_health(base: String) -> Result<Health> {
    Ok(health::probe(&base).await)
}

/// Run a stack operation on a blocking thread, streaming its output to the UI.
/// `check_volume` is true only for operations that boot the API (up):
/// down / restart / pull do not authenticate to the database, and
/// blocking them would bar the recovery path itself.
async fn streamed<F>(app: AppHandle, check_volume: bool, op: F) -> Result<()>
where
    F: FnOnce(&AppState, &Docker, &mut dyn FnMut(&str)) -> Result<()> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        let _guard = state.busy.try_lock().map_err(|_| DesktopError::Config("另一个栈操作正在进行".into()))?;
        let cfg = state.config()?;
        let docker = state.docker(&cfg)?;
        if check_volume && state.files().prepare(&cfg)? && pg_volume_exists(&docker) {
            // P3-E14: .env was lost and secrets were regenerated, but the
            // old database volume is still there — starting now would
            // boot an API that cannot authenticate to its own database.
            // Refuse with a recovery path instead of a cryptic crash loop.
            return Err(DesktopError::Config(
                "检测到已有数据库卷，但 .env 已丢失并重新生成了密钥：新密钥无法打开旧卷。请从备份恢复 .env，或确认清空数据后删除卷 formunex_fx-postgres 再启动。".into(),
            ));
        }
        if !check_volume {
            state.files().prepare(&cfg)?;
        }
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
    streamed(app, true, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["up", "-d", "--remove-orphans"]), emit))
        .await
}

#[tauri::command]
pub async fn stack_down(app: AppHandle) -> Result<()> {
    streamed(app, false, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["down"]), emit)).await
}

#[tauri::command]
pub async fn stack_restart(app: AppHandle) -> Result<()> {
    streamed(app, false, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["restart"]), emit)).await
}

#[tauri::command]
pub async fn stack_pull(app: AppHandle) -> Result<()> {
    streamed(app, false, |_, docker, emit| docker.run_streaming(&docker.compose_args(&["pull", "--ignore-buildable"]), emit))
        .await
}

/// Offline install: `docker load -i formunex-images.tar` (方案 §2.2).
#[tauri::command]
pub async fn import_images(app: AppHandle, path: String) -> Result<()> {
    let tar = PathBuf::from(&path);
    if !tar.is_file() {
        return Err(DesktopError::Config(format!("镜像包不存在：{path}")));
    }
    streamed(app, false, move |_, docker, emit| {
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
    // P2-E12: an illegal name used to be silently dropped, returning ALL
    // services' logs for a request that asked for one service.
    let service = match service {
        Some(s) if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') => Some(s),
        Some(s) => return Err(DesktopError::Config(format!("非法服务名：{s}"))),
        None => None,
    };
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
        let cfg = state.config()?;
        let docker = state.docker(&cfg)?;
        let files = state.files();
        let emit = |line: &str| {
            let _ = app.emit(LOG_EVENT, line.to_string());
        };
        // P1-E15: a failed service query used to mean "database not
        // running", silently skipping the pre-upgrade backup.
        let db_running =
            docker.services()?.iter().any(|s| s.service == "postgres" && s.state == "running");
        if db_running {
            emit("备份数据库（pg_dump）…");
            let b = stack::backup(&files, &docker, &format!("pre-{version}"))?;
            emit(&format!("已备份：{}", b.path));
        }
        // P1-E11: the old code saved the new version BEFORE pull/up, so
        // a failed pull left desktop.json claiming a version whose
        // images were never fetched (next `up` then fails on the missing
        // tag). Build the target config, prepare its compose files, and
        // persist the version only after pull+up succeed.
        let mut next = cfg.clone();
        next.version = version.clone();
        let next = next.validated()?;
        if files.prepare(&next)? && pg_volume_exists(&docker) {
            return Err(DesktopError::Config(
                "检测到已有数据库卷，但 .env 已丢失并重新生成了密钥：新密钥无法打开旧卷。请从备份恢复 .env，或确认清空数据后删除卷 formunex_fx-postgres 再升级。".into(),
            ));
        }
        docker.run_streaming(&docker.compose_args(&["pull", "--ignore-buildable"]), emit)?;
        docker.run_streaming(&docker.compose_args(&["up", "-d", "--remove-orphans"]), emit)?;
        next.save(&state.config_dir)?;
        // A new image may bring new regulatory data; the importer skips an unchanged set in seconds. The
        // upgrade itself already succeeded, so a failed import is reported, not raised.
        emit("更新法规库…");
        if let Err(e) = docker.run_streaming(&stack::seed_regulatory_args(&docker, &next.version), emit) {
            emit(&format!("法规库更新未完成：{e}（可在“系统管理 → 桌面端与本机服务”中重新导入）"));
        }
        Ok(next)
    })
    .await
    .map_err(|e| DesktopError::Config(e.to_string()))?
}

/// Load (or refresh) the bundled regulatory data into the local stack, streaming the importer's output.
#[tauri::command]
pub async fn seed_regulatory(app: AppHandle) -> Result<()> {
    streamed(app, false, |state, docker, emit| {
        let cfg = state.config()?;
        if cfg.mode != Some(Mode::Local) {
            return Err(DesktopError::Config("只有本机栈需要导入法规库".into()));
        }
        emit("导入法规库（物质、危险化学品目录、职业接触限值、GB 12268、IECSC、H/P 语句）…");
        docker.run_streaming(&stack::seed_regulatory_args(docker, &cfg.version), emit)
    })
    .await
}

#[derive(Serialize)]
pub struct CreateAdminResult {
    pub password: String,
    /// P2-E14: the wizard used to claim the password "is saved to the
    /// keychain" even when that save failed — the one-time secret would
    /// then exist only on screen. The UI shows a copy-it-now warning
    /// when this is false.
    pub keychain_saved: bool,
}

/// Create the first administrator; the generated password goes to the keychain and back to the UI once.
#[tauri::command]
pub async fn create_admin(app: AppHandle, username: String, display_name: String) -> Result<CreateAdminResult> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        let cfg = state.config()?;
        if cfg.mode != Some(Mode::Local) {
            return Err(DesktopError::Config("只有本机栈需要创建管理员".into()));
        }
        let docker = state.docker(&cfg)?;
        let password = stack::random_secret(20);
        stack::create_admin(&docker, &username, &display_name, &password)?;
        let keychain_saved = match secrets::set(secrets::ADMIN_PASSWORD, &password) {
            Ok(()) => true,
            Err(e) => {
                let _ = app.emit(LOG_EVENT, format!("管理员密码未能写入钥匙串：{e}"));
                false
            }
        };
        Ok(CreateAdminResult { password, keychain_saved })
    })
    .await
    .map_err(|e| DesktopError::Config(e.to_string()))?
}

/// P1-E12: delete a corrupt desktop.json so the first-run wizard can run
/// again. Only the settings file is removed — never the stack data dir.
#[tauri::command]
pub fn reset_config(state: State<'_, AppState>) -> Result<()> {
    let path = state.config_dir.join(crate::config::CONFIG_FILE);
    match std::fs::remove_file(&path) {
        Ok(()) | Err(ref e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(DesktopError::Config(e.to_string())),
    }
}

/// P3-E15: whether this build configured a signed updater (pubkey at
/// build time). The settings page asks this instead of guessing from
/// the updater plugin's error text.
#[tauri::command]
pub fn updater_configured(app: AppHandle) -> bool {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("pubkey"))
        .and_then(|k| k.as_str())
        .is_some_and(|k| !k.trim().is_empty())
}

/// The compose project `formunex` owns the `fx-postgres` named volume.
fn pg_volume_exists(docker: &Docker) -> bool {
    docker
        .run(&["volume".into(), "inspect".into(), "formunex_fx-postgres".into()], &[])
        .map(|o| o.status.success())
        .unwrap_or(false)
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
