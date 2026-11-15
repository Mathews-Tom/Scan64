//! CI-only driver. The updater-test Cargo feature is never enabled in shipped builds.
use super::*;
use serde_json::{json, Value};
use std::{
    env,
    io::Read,
    net::{Ipv4Addr, SocketAddrV4, TcpStream},
};

const FLAGS: [&str; 5] = [
    "--scan64-update-smoke-file",
    "--scan64-update-smoke-mode",
    "--scan64-update-smoke-from",
    "--scan64-update-smoke-to",
    "--scan64-update-data-dir",
];
const VARIABLES: [&str; 5] = [
    "SCAN64_UPDATE_SMOKE_FILE",
    "SCAN64_UPDATE_SMOKE_MODE",
    "SCAN64_UPDATE_SMOKE_FROM",
    "SCAN64_UPDATE_SMOKE_TO",
    "SCAN64_DATA_DIR",
];

fn launch_arguments(
    args: &[std::ffi::OsString],
) -> Result<Option<[std::ffi::OsString; 5]>, String> {
    let mut values: [Option<std::ffi::OsString>; 5] = Default::default();
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        if let Some(index) = FLAGS
            .iter()
            .position(|flag| arg.as_os_str() == std::ffi::OsStr::new(flag))
        {
            if values[index].is_some() {
                return Err("Duplicate updater smoke argument".to_owned());
            }
            values[index] = Some(
                args.next()
                    .ok_or("Missing updater smoke argument value")?
                    .clone(),
            );
        }
    }
    if values.iter().all(Option::is_none) {
        return Ok(None);
    }
    if values.iter().any(Option::is_none) {
        return Err("All updater smoke launch arguments are required".to_owned());
    }
    let values = values.map(|value| value.expect("all values supplied"));
    if !Path::new(&values[0]).is_absolute()
        || !Path::new(&values[4]).is_absolute()
        || !matches!(
            values[1].to_str(),
            Some("install" | "reject" | "unavailable")
        )
        || values[2].is_empty()
        || values[3].is_empty()
        || values[2] == values[3]
    {
        return Err("Invalid updater smoke launch argument".to_owned());
    }
    Ok(Some(values))
}

pub(crate) fn prepare_smoke_launch() -> Result<(), String> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    let Some(values) = launch_arguments(&args)? else {
        if env::var_os("SCAN64_UPDATE_SMOKE_FILE").is_some() {
            return Err("Updater smoke requires persistent relaunch arguments".to_owned());
        }
        return Ok(());
    };
    // Called before Tauri creates any threads. These non-secret values are
    // restored from args_os after NSIS or macOS LaunchServices drops the env.
    for (variable, value) in VARIABLES.into_iter().zip(values) {
        env::set_var(variable, value);
    }
    Ok(())
}

fn file() -> Option<PathBuf> {
    let path = PathBuf::from(env::var_os("SCAN64_UPDATE_SMOKE_FILE")?);
    path.is_absolute().then_some(path)
}

pub(super) fn active() -> bool {
    file().is_some()
        && matches!(
            env::var("SCAN64_UPDATE_SMOKE_MODE").as_deref(),
            Ok("install" | "reject" | "unavailable")
        )
}

fn append(value: &Value) -> Result<(), String> {
    let path = file().ok_or("Updater smoke requires an absolute JSONL receipt path")?;
    let mut record =
        serde_json::to_vec(value).map_err(|_| "Cannot encode updater smoke receipt")?;
    record.push(b'\n');
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .and_then(|mut file| file.write_all(&record))
        .map_err(|_| "Cannot write updater smoke receipt".to_owned())
}

pub(super) fn snapshot(status: &UpdateStatus) {
    if active() {
        // State contains only fixed URLs, versions, percentages and safe reasons.
        // No manifest, request headers, shell token, or player credential is logged.
        let result = serde_json::to_value(status)
            .map_err(|_| "Cannot encode updater state".to_owned())
            .and_then(|value| append(&value));
        if result.is_err() {
            eprintln!("Cannot write updater state receipt");
        }
    }
}

fn seed_player(connection: &crate::Connection) -> Result<(), String> {
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, connection.port).into();
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(5))
        .map_err(|_| "Cannot connect to the smoke server")?;
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .map_err(|_| "Cannot set smoke timeout")?;
    stream
        .set_write_timeout(Some(Duration::from_secs(10)))
        .map_err(|_| "Cannot set smoke timeout")?;
    let body = r#"{"id":"update-smoke-player","display_name":"Update smoke"}"#;
    let request = format!(
        "POST /v1/players HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Scan64-Shell-Token: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        connection.token, body.len(), body
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|_| "Cannot send smoke player request")?;
    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|_| "Cannot read smoke player response")?;
    if !(response.starts_with(b"HTTP/1.1 200 ") || response.starts_with(b"HTTP/1.1 201 ")) {
        return Err("Cannot seed the update smoke player through the API".to_owned());
    }
    Ok(())
}

fn complete(app: &AppHandle) -> Result<(), String> {
    // Receipt precedes the normal exit path; the runner waits for both processes.
    append(&json!({"event": "complete", "version": app.package_info().version.to_string()}))?;
    app.exit(0);
    Ok(())
}

async fn run(app: AppHandle) -> Result<(), String> {
    let from = env::var("SCAN64_UPDATE_SMOKE_FROM").map_err(|_| "Missing smoke source version")?;
    let to = env::var("SCAN64_UPDATE_SMOKE_TO").map_err(|_| "Missing smoke destination version")?;
    let mode = env::var("SCAN64_UPDATE_SMOKE_MODE").map_err(|_| "Missing updater smoke mode")?;
    let version = app.package_info().version.to_string();
    if version != from && version != to {
        return Err("Unexpected application version in updater smoke".to_owned());
    }
    let server = app.state::<crate::SharedServer>().inner().clone();
    let (connection, server_pid) = tauri::async_runtime::spawn_blocking(move || {
        let mut server = server
            .lock()
            .map_err(|_| "Smoke server state unavailable")?;
        let connection = server.connection()?;
        Ok::<_, String>((connection, server.pid()))
    })
    .await
    .map_err(|_| "Smoke startup task failed")??;
    if version == from {
        seed_player(&connection)?;
    }
    append(&json!({
        "event": "startup", "version": version, "server_pid": server_pid,
        "port": connection.port, "shell_pid": std::process::id()
    }))?;
    snapshot(&update_status(app.clone())?);
    if version == to {
        if mode != "install" {
            return Err("A rejected update unexpectedly installed".to_owned());
        }
        return complete(&app);
    }
    if mode == "unavailable" {
        if update_status(app.clone())?.state != Phase::Unavailable {
            return Err("The package installation did not report updater unavailable".to_owned());
        }
        // Exercise the real command as well: unavailable is terminal and cannot check.
        if check_updates(app.clone()).await?.state != Phase::Unavailable {
            return Err("Unavailable updater accepted an update check".to_owned());
        }
        return complete(&app);
    }
    set_automatic_updates(app.clone(), false).await?;
    let checked = check_updates(app.clone()).await?;
    if checked.state != Phase::Available {
        return Err("The smoke manifest did not produce an available update".to_owned());
    }
    let downloaded = download_update(app.clone()).await?;
    if mode == "reject" {
        if downloaded.state != Phase::Failed
            || !downloaded
                .reason
                .as_ref()
                .is_some_and(|reason| reason.to_lowercase().contains("signature"))
        {
            return Err("The tampered update was not rejected".to_owned());
        }
        return complete(&app);
    }
    if downloaded.state != Phase::Ready {
        return Err("The signed smoke update did not become ready".to_owned());
    }
    append(&json!({"event": "restart-click", "version": version}))?;
    let installed = restart_to_update(app.clone()).await?;
    if installed.state == Phase::Failed {
        return Err("The smoke update could not be installed".to_owned());
    }
    Ok(())
}

pub(super) fn start(app: AppHandle) -> bool {
    if env::var_os("SCAN64_UPDATE_SMOKE_FILE").is_none() {
        return false;
    }
    if !active() {
        eprintln!("Invalid updater smoke opt-in");
        app.exit(1);
        return true;
    }
    tauri::async_runtime::spawn(async move {
        if let Err(reason) = run(app.clone()).await {
            let _ = append(&json!({"event": "error", "reason": reason}));
            app.exit(1);
        }
    });
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    fn arguments() -> Vec<OsString> {
        let root = env::temp_dir().join("Scan64 update smoke");
        let values = [
            root.join("receipt.jsonl").into_os_string(),
            "install".into(),
            "0.6.1".into(),
            "0.6.2".into(),
            root.into_os_string(),
        ];
        FLAGS
            .into_iter()
            .zip(values)
            .flat_map(|(flag, value)| [flag.into(), value])
            .collect()
    }

    #[test]
    fn complete_absolute_arguments_survive_relaunch_without_environment() {
        let args = arguments();
        let values = launch_arguments(&args).unwrap().unwrap();
        assert!(Path::new(&values[0]).is_absolute());
        assert!(Path::new(&values[4]).is_absolute());
        assert_eq!(values[1], "install");
        assert_eq!(values[2], "0.6.1");
        assert_eq!(values[3], "0.6.2");
        assert!(launch_arguments(&[]).unwrap().is_none());
    }

    #[test]
    fn partial_duplicate_relative_and_unknown_mode_arguments_are_rejected() {
        let mut args = arguments();
        assert!(launch_arguments(&args[..2]).is_err());
        args.extend([FLAGS[0].into(), env::temp_dir().into_os_string()]);
        assert!(launch_arguments(&args).is_err());
        let mut args = arguments();
        args[1] = "relative.jsonl".into();
        assert!(launch_arguments(&args).is_err());
        let mut args = arguments();
        args[3] = "automatic-install".into();
        assert!(launch_arguments(&args).is_err());
    }
}
