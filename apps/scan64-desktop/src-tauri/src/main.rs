#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod feedback;
mod release_links;
mod server;
mod updater;

use server::{Connection, Server};
use std::{
    env, fs,
    io::{self, Write},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};
use tauri::{
    ipc::{InvokeBody, Request},
    menu::{Menu, MenuItem, PredefinedMenuItem, Submenu},
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

#[tauri::command]
async fn save_coach_packet(
    window: tauri::WebviewWindow,
    request: Request<'_>,
) -> Result<(), String> {
    let InvokeBody::Raw(packet) = request.body() else {
        return Err("Expected a ZIP coach packet".to_owned());
    };
    if !packet.starts_with(b"PK\x03\x04") {
        return Err("Expected a ZIP coach packet".to_owned());
    }

    // Only the native dialog supplies the destination, never the webview payload.
    let (sender, mut receiver) = tauri::async_runtime::channel(1);
    window
        .dialog()
        .file()
        .set_parent(&window)
        .set_title("Save coach packet")
        .set_file_name("scan64-coach-packet.zip")
        .add_filter("ZIP archive", &["zip"])
        .save_file(move |choice| {
            let _ = sender.try_send(choice);
        });
    let Some(choice) = receiver
        .recv()
        .await
        .ok_or_else(|| "Save dialog closed unexpectedly".to_owned())?
    else {
        return Ok(());
    };
    let path = choice
        .into_path()
        .map_err(|_| "Choose a local ZIP file".to_owned())?;
    if !path
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("zip"))
    {
        return Err("Choose a .zip filename".to_owned());
    }

    let packet = packet.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path).map_err(|error| {
            if error.kind() == io::ErrorKind::AlreadyExists {
                "A file with that name exists; choose a new filename"
            } else {
                "Cannot create the coach packet at the selected location"
            }
        })?;
        file.write_all(&packet)
            .map_err(|_| "Cannot finish saving the coach packet; remove the incomplete ZIP")
    })
    .await
    .map_err(|_| "Coach packet save task failed".to_owned())?
    .map_err(str::to_owned)
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

#[cfg(feature = "updater-test")]
#[tauri::command]
fn updater_smoke_observed(
    app: tauri::AppHandle,
    stage: String,
    text: String,
) -> Result<(), String> {
    updater::observe_ui(&app, stage, text)
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
    #[cfg(feature = "updater-test")]
    if updater::prepare_smoke_launch().is_err() {
        eprintln!("Invalid updater smoke launch arguments");
        std::process::exit(1);
    }
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init());
    #[cfg(feature = "updater-test")]
    let builder = builder.on_page_load(updater::observe_page_load);
    let app = builder
        .on_menu_event(|app, event| {
            if event.id().as_ref() == "check-for-updates" {
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = updater::check_updates(app).await;
                });
            }
        })
        .manage(AtomicBool::new(false))
        .invoke_handler(tauri::generate_handler![
            desktop_connection,
            restart_server,
            choose_puzzle_file,
            save_coach_packet,
            release_links::open_release_url,
            feedback::open_feedback_issue,
            smoke_complete,
            smoke_failed,
            #[cfg(feature = "updater-test")]
            updater_smoke_observed,
            updater::update_status,
            updater::check_updates,
            updater::download_update,
            updater::restart_to_update,
            updater::set_automatic_updates
        ])
        .setup(|app| {
            // Release smoke may isolate CI data; ordinary releases always use the OS app directory.
            // This must not admit debug-only executable overrides into release launches.
            let data_dir = if cfg!(debug_assertions)
                || server::smoke_file().is_some()
                || (cfg!(feature = "updater-test")
                    && env::var_os("SCAN64_UPDATE_SMOKE_FILE").is_some())
            {
                env::var_os("SCAN64_DATA_DIR").map(Into::into)
            } else {
                None
            }
            .unwrap_or(app.path().app_data_dir()?);
            server::private_directory(&data_dir)?;
            let updates = updater::Controller::new(app.handle(), &data_dir);
            app.manage(Arc::new(Mutex::new(
                Server::new(data_dir, app.path().resource_dir()?)
                    .with_app_version(app.package_info().version.to_string()),
            )));
            app.manage(Arc::new(Mutex::new(updates)));
            let quit = PredefinedMenuItem::quit(app, Some("Quit Scan64"))?;
            let check = MenuItem::with_id(
                app,
                "check-for-updates",
                "Check for Updates…",
                true,
                None::<&str>,
            )?;
            #[cfg(target_os = "macos")]
            let menu = {
                let separator = PredefinedMenuItem::separator(app)?;
                let application =
                    Submenu::with_items(app, "Scan64", true, &[&check, &separator, &quit])?;
                Menu::with_items(app, &[&application])?
            };
            #[cfg(not(target_os = "macos"))]
            let menu = {
                let application = Submenu::with_items(app, "Scan64", true, &[&quit])?;
                let help = Submenu::with_items(app, "Help", true, &[&check])?;
                Menu::with_items(app, &[&application, &help])?
            };
            app.set_menu(menu)?;
            // The window-state plugin restores physical sizes before setup.
            // Clamp old saved sizes against the current logical-pixel minimum.
            let window = app
                .get_webview_window("main")
                .ok_or("Main window is unavailable")?;
            let config = app
                .config()
                .app
                .windows
                .iter()
                .find(|config| config.label == "main")
                .ok_or("Main window configuration is unavailable")?;
            let (Some(min_width), Some(min_height)) = (config.min_width, config.min_height) else {
                return Err("Main window minimum size is required".into());
            };
            let size = window
                .inner_size()?
                .to_logical::<f64>(window.scale_factor()?);
            if size.width < min_width || size.height < min_height {
                window.set_size(tauri::LogicalSize::new(
                    size.width.max(min_width),
                    size.height.max(min_height),
                ))?;
            }
            updater::start(app.handle().clone());
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
