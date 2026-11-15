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
use tauri_plugin_dialog::DialogExt;

fn readable_puzzle_file(path: &std::path::Path) -> Result<String, String> {
    if !path.as_os_str().to_string_lossy().ends_with(".csv.zst") {
        return Err("Choose a .csv.zst puzzle file".to_owned());
    }
    if !fs::metadata(path)
        .map_err(|_| "Cannot read puzzle file metadata")?
        .is_file()
    {
        return Err("Choose a readable regular file".to_owned());
    }
    let file = fs::File::open(path).map_err(|_| "Puzzle file is not readable".to_owned())?;
    if !file
        .metadata()
        .map_err(|_| "Cannot read puzzle file metadata")?
        .is_file()
    {
        return Err("Choose a readable regular file".to_owned());
    }
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| "Puzzle file path is not valid UTF-8".to_owned())
}

#[tauri::command]
async fn choose_puzzle_file(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    app.dialog()
        .file()
        .set_title("Choose a puzzle file")
        .add_filter("Compressed puzzle CSV", &["csv.zst"])
        .pick_file(move |file| {
            let _ = sender.send(file);
        });
    tauri::async_runtime::spawn_blocking(move || {
        let file = receiver
            .recv()
            .map_err(|_| "File dialog closed unexpectedly".to_owned())?;
        file.map(|file| {
            let path = file
                .into_path()
                .map_err(|_| "Choose a local puzzle file".to_owned())?;
            readable_puzzle_file(&path)
        })
        .transpose()
    })
    .await
    .map_err(|_| "File selection task failed".to_owned())?
}

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

fn stop_on_exit(state: &Mutex<Server>) {
    // Startup/restart may hold this lock for the readiness deadline. Never block
    // the GUI's exit on that worker: process exit closes stdin, and the server's
    // independent EOF watchdog bounds shutdown even while startup is stuck.
    if let Ok(mut server) = state.try_lock() {
        server.stop();
    }
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
        .plugin(tauri_plugin_dialog::init())
        .manage(AtomicBool::new(false))
        .invoke_handler(tauri::generate_handler![
            desktop_connection,
            restart_server,
            choose_puzzle_file,
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
                stop_on_exit(state.inner());
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    #[test]
    fn quit_does_not_wait_for_an_in_progress_server_operation() {
        let state = Arc::new(Mutex::new(Server::new("data".into(), "resources".into())));
        let startup = state.lock().unwrap();
        let worker_state = state.clone();
        let (sent, received) = mpsc::channel();
        let worker = thread::spawn(move || {
            stop_on_exit(&worker_state);
            sent.send(()).unwrap();
        });
        let result = received.recv_timeout(Duration::from_millis(500));
        drop(startup);
        worker.join().unwrap();
        assert!(result.is_ok(), "Quit waited for the startup/restart lock");
    }

    #[test]
    fn puzzle_selection_requires_readable_regular_csv_zst() {
        let root = std::env::temp_dir().join(format!("scan64-picker-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let valid = root.join("puzzles.csv.zst");
        fs::write(&valid, b"fixture").unwrap();
        assert_eq!(
            readable_puzzle_file(&valid).unwrap(),
            valid.to_str().unwrap()
        );
        let wrong = root.join("puzzles.csv");
        fs::write(&wrong, b"fixture").unwrap();
        assert!(readable_puzzle_file(&wrong).is_err());
        assert!(readable_puzzle_file(&root.join("missing.csv.zst")).is_err());
        let directory = root.join("directory.csv.zst");
        fs::create_dir(&directory).unwrap();
        assert!(readable_puzzle_file(&directory).is_err());
        fs::remove_dir_all(root).unwrap();
    }

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
