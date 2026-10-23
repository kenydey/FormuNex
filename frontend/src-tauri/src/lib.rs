//! FormuNex desktop shell (方案 §3.5): serves the same UI as the web build and either orchestrates the
//! local Docker stack or connects to a FormuNex server.

pub mod commands;
pub mod config;
pub mod docker;
pub mod error;
pub mod health;
pub mod secrets;
pub mod stack;

use tauri::{Emitter, Manager};

/// Event telling the UI to open a `formunex://<path>` deep link.
pub const DEEP_LINK_EVENT: &str = "deep-link://open";

/// `formunex://plm/packages/42?x=1` → `/plm/packages/42?x=1`; anything else → `None`.
pub fn deep_link_route(url: &str) -> Option<String> {
    let parsed = url::Url::parse(url).ok()?;
    if parsed.scheme() != "formunex" {
        return None;
    }
    let mut path = String::from("/");
    if let Some(host) = parsed.host_str() {
        path.push_str(host);
    }
    path.push_str(parsed.path().trim_end_matches('/'));
    let path = path.replace("//", "/");
    if path.contains("..") {
        return None;
    }
    Some(match parsed.query() {
        Some(q) => format!("{path}?{q}"),
        None => path,
    })
}

fn updater_configured(app: &tauri::App) -> bool {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("pubkey"))
        .and_then(|k| k.as_str())
        .is_some_and(|k| !k.trim().is_empty())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // A second launch (e.g. a formunex:// link) focuses the running window instead.
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
            for arg in argv.iter().skip(1) {
                if let Some(route) = deep_link_route(arg) {
                    let _ = app.emit(DEEP_LINK_EVENT, route);
                }
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let state = commands::AppState::new(app.handle())?;
            app.manage(state);
            // Signed shell updates only when a public key was configured at build time (tauri.conf / CI).
            if updater_configured(app) {
                app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            }
            #[cfg(any(windows, target_os = "linux"))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let _ = app.deep_link().register_all();
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::desktop_info,
            commands::save_config,
            commands::docker_status,
            commands::stack_status,
            commands::probe_health,
            commands::stack_up,
            commands::stack_down,
            commands::stack_restart,
            commands::stack_pull,
            commands::import_images,
            commands::stack_logs,
            commands::backup_now,
            commands::list_backups,
            commands::stack_upgrade,
            commands::seed_regulatory,
            commands::create_admin,
            commands::keychain_get,
            commands::keychain_set,
            commands::keychain_delete,
        ])
        .run(tauri::generate_context!())
        .expect("error while running FormuNex");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deep_links_map_to_routes() {
        assert_eq!(deep_link_route("formunex://plm/packages/42").as_deref(), Some("/plm/packages/42"));
        assert_eq!(deep_link_route("formunex://lab/tasks?status=open").as_deref(), Some("/lab/tasks?status=open"));
        assert_eq!(deep_link_route("formunex://dashboard/").as_deref(), Some("/dashboard"));
        assert_eq!(deep_link_route("https://evil.example/x"), None);
        // dot segments are resolved by the URL parser and cannot climb out of the app routes
        assert_eq!(deep_link_route("formunex://a/../../etc").as_deref(), Some("/a/etc"));
        assert_eq!(deep_link_route("--flag"), None);
    }
}
