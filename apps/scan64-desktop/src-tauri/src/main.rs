#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod server;

use server::{Connection, Server};
use std::{
    env, fs,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};
use tauri::{
    menu::{Menu, PredefinedMenuItem, Submenu},
    Manager,
};

type SharedServer = Arc<Mutex<Server>>;

#[tauri::command]
async fn desktop_connection(state: tauri::State<'_, SharedServer>) -> Result<Connection, String> {
    let server = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = server
            .lock()
            .map_err(|_| "Server state unavailable".to_owned())?;
        guard.connection()
    })
    .await
    .map_err(|_| "Server startup task failed".to_owned())?
}

#[tauri::command]
async fn restart_server(state: tauri::State<'_, SharedServer>) -> Result<Connection, String> {
    let server = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = server
            .lock()
            .map_err(|_| "Server state unavailable".to_owned())?;
        guard.restart()
    })
    .await
    .map_err(|_| "Server restart task failed".to_owned())?
}

#[tauri::command]
fn smoke_complete(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedServer>,
    completed: tauri::State<'_, AtomicBool>,
    origin: String,
    path: String,
    status: u16,
) -> Result<(), String> {
    let Some(file) = server::smoke_file() else {
        return Err("Smoke mode is disabled".to_owned());
    };
    if !valid_smoke(&origin, &path, status) {
        return Err("Smoke requires a successful /v1 request from the shell origin".to_owned());
    }
    let mut server = state
        .lock()
        .map_err(|_| "Server state unavailable".to_owned())?;
    let connection = server.connection()?;
    let record = serde_json::json!({
        "origin": origin, "path": path, "status": status,
        "port": connection.port, "server_pid": server.pid(), "shell_pid": std::process::id(),
        "windows": app.webview_windows().len(),
    });
    let pending = file.with_extension("pending");
    fs::write(
        &pending,
        serde_json::to_vec_pretty(&record).map_err(|_| "Cannot encode smoke record")?,
    )
    .map_err(|_| "Cannot write smoke record".to_owned())?;
    fs::rename(pending, file).map_err(|_| "Cannot finalize smoke record".to_owned())?;
    completed.store(true, Ordering::Release);
    drop(server);
    if env::var_os("SCAN64_SMOKE_HOLD").is_none() {
        app.exit(0);
    }
    Ok(())
}

#[tauri::command]
fn smoke_failed(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedServer>,
    origin: String,
    reason: String,
) -> Result<(), String> {
    let file = server::smoke_file().ok_or("Smoke mode is disabled")?;
    let mut server = state.lock().map_err(|_| "Server state unavailable")?;
    let connection = server.connection()?;
    let reason = reason.replace(&connection.token, "[redacted]");
    let record = serde_json::json!({ "origin": origin, "reason": reason });
    fs::write(file.with_extension("failed.json"), record.to_string())
        .map_err(|_| "Cannot write smoke failure")?;
    drop(server);
    app.exit(1);
    Ok(())
}

fn valid_smoke(origin: &str, path: &str, status: u16) -> bool {
    let expected = if cfg!(windows) {
        "http://tauri.localhost"
    } else {
        "tauri://localhost"
    };
    origin == expected && path.starts_with("/v1/") && (200..300).contains(&status)
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(AtomicBool::new(false))
        .invoke_handler(tauri::generate_handler![
            desktop_connection,
            restart_server,
            smoke_complete,
            smoke_failed
        ])
        .setup(|app| {
            let data_dir = if cfg!(debug_assertions) {
                env::var_os("SCAN64_DATA_DIR").map(Into::into)
            } else {
                None
            }
            .unwrap_or(app.path().app_data_dir()?);
            fs::create_dir_all(&data_dir)?;
            app.manage(Arc::new(Mutex::new(Server::new(
                data_dir,
                app.path().resource_dir()?,
            ))));
            // Updates join this application menu in M84; no updater is advertised yet.
            let quit = PredefinedMenuItem::quit(app, Some("Quit Scan64"))?;
            let application = Submenu::with_items(app, "Scan64", true, &[&quit])?;
            app.set_menu(Menu::with_items(app, &[&application])?)?;
            if server::smoke_file().is_some() {
                let handle = app.handle().clone();
                thread::spawn(move || {
                    thread::sleep(Duration::from_secs(150));
                    if !handle.state::<AtomicBool>().load(Ordering::Acquire) {
                        eprintln!("Desktop smoke timed out before a tokened /v1 round trip");
                        handle.exit(1);
                    }
                });
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Cannot initialize Scan64 desktop");
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(state) = app.try_state::<SharedServer>() {
                if let Ok(mut server) = state.lock() {
                    server.stop();
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn smoke_requires_shell_origin_success_and_api_path() {
        let origin = if cfg!(windows) {
            "http://tauri.localhost"
        } else {
            "tauri://localhost"
        };
        assert!(valid_smoke(origin, "/v1/players", 201));
        assert!(!valid_smoke("https://example.com", "/v1/players", 201));
        assert!(!valid_smoke(origin, "/health", 200));
        assert!(!valid_smoke(origin, "/v1/players", 401));
    }
}
