use serde::Serialize;
use std::{
    env,
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Serialize)]
pub struct Connection {
    pub port: u16,
    pub token: String,
    pub smoke: bool,
    pub smoke_board: bool,
    pub smoke_engine: bool,
}

pub struct Server {
    child: Option<Child>,
    connection: Option<Connection>,
    failure: Option<String>,
    data_dir: PathBuf,
    resource_dir: PathBuf,
    pub log_path: PathBuf,
    app_version: String,
    updating: bool,
}

impl Server {
    pub fn new(data_dir: PathBuf, resource_dir: PathBuf) -> Self {
        let log_path = data_dir.join("logs").join("server.log");
        Self {
            child: None,
            connection: None,
            failure: None,
            data_dir,
            resource_dir,
            log_path,
            app_version: env!("CARGO_PKG_VERSION").to_owned(),
            updating: false,
        }
    }

    pub fn with_app_version(mut self, version: String) -> Self {
        self.app_version = version;
        self
    }

    pub fn connection(&mut self) -> Result<Connection, String> {
        if self.updating {
            return Err("The server is stopped while an update is installed".to_owned());
        }
        if let Some(child) = self.child.as_mut() {
            match child.try_wait() {
                Ok(None) => {
                    return self
                        .connection
                        .clone()
                        .ok_or_else(|| self.error("Server is starting"));
                }
                Ok(Some(_)) | Err(_) => {
                    self.failure = Some(self.error("The server stopped unexpectedly"));
                    self.connection = None;
                    self.child = None;
                }
            }
        }
        if let Some(failure) = &self.failure {
            return Err(failure.clone());
        }
        self.start()
    }

    pub fn restart(&mut self) -> Result<Connection, String> {
        if self.updating {
            return Err("The server cannot restart while an update is installed".to_owned());
        }
        self.stop_checked()?;
        self.failure = None;
        self.start()
    }

    fn error(&self, message: &str) -> String {
        format!("{message}. Server log: {}", self.log_path.display())
    }

    fn start(&mut self) -> Result<Connection, String> {
        let result = self.start_inner();
        if let Err(error) = &result {
            self.failure = Some(error.clone());
        }
        result
    }

    fn start_inner(&mut self) -> Result<Connection, String> {
        private_directory(&self.data_dir)
            .and_then(|()| private_directory(self.log_path.parent().expect("log parent")))
            .map_err(|_| self.error("Cannot create the private log directory"))?;
        // Retry early exits (including a bind race) with a fresh ephemeral port.
        let deadline = Instant::now() + Duration::from_secs(30);
        for _ in 0..=3 {
            if Instant::now() >= deadline {
                break;
            }
            let port = free_port().map_err(|_| self.error("Cannot reserve a loopback port"))?;
            let token = new_token().map_err(|_| self.error("Cannot generate a shell token"))?;
            let mut command = self.command()?;
            command.env_clear();
            for key in [
                "PATH",
                "SystemRoot",
                "WINDIR",
                "TEMP",
                "TMP",
                "TMPDIR",
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "XDG_CACHE_HOME",
                "LANG",
                "LC_ALL",
            ] {
                if let Some(value) = env::var_os(key) {
                    command.env(key, value);
                }
            }
            command
                .env("SCAN64_DATA_DIR", &self.data_dir)
                .env("SCAN64_APP_VERSION", &self.app_version)
                .env("SCAN64_PORT", port.to_string())
                .env("SCAN64_SHELL_TOKEN", &token)
                .env("PYTHONUTF8", "1")
                .stdin(Stdio::piped());
            if cfg!(debug_assertions) {
                if let Some(url) = env::var_os("SCAN64_DEBUG_PUZZLE_URL") {
                    command.env("SCAN64_DEBUG_PUZZLE_URL", url);
                }
            }
            configure_engine(&mut command, &self.resource_dir);
            let log = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.log_path)
                .map_err(|_| self.error("Cannot open the server log"))?;
            let stderr = log
                .try_clone()
                .map_err(|_| self.error("Cannot open the server log"))?;
            command.stdout(Stdio::from(log)).stderr(Stdio::from(stderr));
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
            }
            self.child = Some(
                command
                    .spawn()
                    .map_err(|_| self.error("Cannot launch the server"))?,
            );
            while Instant::now() < deadline {
                let exited = self
                    .child
                    .as_mut()
                    .expect("spawned child")
                    .try_wait()
                    .map_err(|_| self.error("Cannot monitor the server"))?
                    .is_some();
                if exited {
                    break;
                }
                if healthy(port) {
                    let connection = Connection {
                        port,
                        token,
                        smoke: smoke_file().is_some(),
                        smoke_board: smoke_file().is_some()
                            && env::var_os("SCAN64_SMOKE_BOARD").is_some(),
                        smoke_engine: smoke_file().is_some()
                            && env::var_os("SCAN64_SMOKE_ENGINE").is_some(),
                    };
                    self.connection = Some(connection.clone());
                    return Ok(connection);
                }
                thread::sleep(Duration::from_millis(100));
            }
            self.stop();
        }
        Err(self.error("The server did not become ready within 30 seconds (three retries)"))
    }

    fn command(&self) -> Result<Command, String> {
        // Debug-only source-server override. Release builds never execute an env-selected program.
        if cfg!(debug_assertions) {
            if let Some(value) = env::var_os("SCAN64_SERVER_COMMAND") {
                let args: Vec<String> =
                    serde_json::from_str(&value.to_string_lossy()).map_err(|_| {
                        self.error("SCAN64_SERVER_COMMAND must be a JSON argument array")
                    })?;
                if args.is_empty() || !Path::new(&args[0]).is_absolute() {
                    return Err(self.error("The debug server executable must be an absolute path"));
                }
                let mut command = Command::new(&args[0]);
                command.args(&args[1..]);
                return Ok(command);
            }
        }
        let executable = if cfg!(windows) {
            "scan64-server.exe"
        } else {
            "scan64-server"
        };
        Ok(Command::new(
            self.resource_dir.join("scan64-server").join(executable),
        ))
    }

    pub fn pid(&self) -> Option<u32> {
        self.child.as_ref().map(Child::id)
    }

    pub fn stop(&mut self) {
        let _ = self.stop_checked();
    }

    pub fn stop_for_update(&mut self) -> Result<(), String> {
        self.updating = true;
        self.stop_checked()
    }

    pub fn recover_after_update(&mut self) -> Result<Connection, String> {
        self.updating = false;
        self.restart()
    }

    fn stop_checked(&mut self) -> Result<(), String> {
        self.connection = None;
        if let Some(mut child) = self.child.take() {
            // EOF starts the server's bounded, process-tree shutdown watchdog.
            drop(child.stdin.take());
            let deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < deadline {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return Ok(());
                }
                thread::sleep(Duration::from_millis(25));
            }
            #[cfg(unix)]
            {
                // SAFETY: the live server owns its session/group (pgid == pid).
                // Kill that whole group if its EOF watchdog cannot run.
                unsafe {
                    libc::killpg(child.id() as libc::pid_t, libc::SIGKILL);
                }
            }
            // Do not install over an executable that might still own the database.
            if child.kill().is_err() && !matches!(child.try_wait(), Ok(Some(_))) {
                self.child = Some(child);
                return Err(self.error("Cannot stop the server for an update"));
            }
            if child.wait().is_err() {
                self.child = Some(child);
                return Err(self.error("Cannot confirm the server stopped"));
            }
        }
        Ok(())
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop();
    }
}

pub(crate) fn private_directory(path: &Path) -> std::io::Result<()> {
    let mut directory = fs::DirBuilder::new();
    directory.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        directory.mode(0o700);
    }
    directory.create(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

pub fn smoke_file() -> Option<PathBuf> {
    // Deliberately release-reachable so CI exercises the actual installed artifact.
    // This opt-in may write only credential-free receipts; it must never enable
    // env-selected server commands or weaken the shell/API token boundary.
    env::var_os("SCAN64_SMOKE_FILE").map(PathBuf::from)
}

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?
        .local_addr()?
        .port())
}

fn new_token() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)?;
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut token = String::with_capacity(64);
    for byte in bytes {
        token.push(char::from(HEX[usize::from(byte >> 4)]));
        token.push(char::from(HEX[usize::from(byte & 15)]));
    }
    Ok(token)
}

fn healthy(port: u16) -> bool {
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port).into();
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(200)) else {
        return false;
    };
    let timeout = Some(Duration::from_millis(200));
    let _ = stream.set_read_timeout(timeout);
    let _ = stream.set_write_timeout(timeout);
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = [0_u8; 256];
    stream
        .read(&mut response)
        .is_ok_and(|count| response[..count].starts_with(b"HTTP/1.1 200 "))
}

fn configure_engine(command: &mut Command, resource_dir: &Path) {
    let engine_dir = resource_dir.join("stockfish");
    if engine_dir.is_dir() {
        let binary = if cfg!(windows) {
            "stockfish.exe"
        } else {
            "stockfish"
        };
        command.env("SCAN64_STOCKFISH_PATH", engine_dir.join(binary));
        // Keep external NNUE files discoverable without changing the inherited PATH.
        command.current_dir(engine_dir);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn update_pause_prevents_frontend_from_starting_or_restarting_the_child() {
        let mut server =
            Server::new("unused".into(), "unused".into()).with_app_version("0.6.2".to_owned());
        assert_eq!(server.app_version, "0.6.2");
        server.stop_for_update().unwrap();
        assert!(server.connection().err().unwrap().contains("update"));
        assert!(server.restart().err().unwrap().contains("update"));
        assert!(server.pid().is_none());
    }

    #[test]
    fn failed_install_recovery_releases_the_update_gate() {
        let directory = env::temp_dir().join(new_token().unwrap());
        let mut server = Server::new(directory.clone(), directory.join("missing-resources"));
        server.stop_for_update().unwrap();
        // A missing bundle cannot launch, but recovery must remove the gate so
        // ordinary server recovery is possible instead of leaving Installing.
        assert!(server.recover_after_update().is_err());
        assert!(!server.updating);
        assert!(server.pid().is_none());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn bundled_engine_uses_an_absolute_path_without_mutating_path() {
        let root = env::temp_dir()
            .join(new_token().unwrap())
            .join(if cfg!(unix) {
                "Scan64: test"
            } else {
                "Scan64 test"
            });
        let engine_dir = root.join("stockfish");
        fs::create_dir_all(&engine_dir).unwrap();
        let mut command = Command::new("unused");
        command.env_clear().env("PATH", "original");
        configure_engine(&mut command, &root);
        let variables: std::collections::HashMap<_, _> = command.get_envs().collect();
        let expected = engine_dir.join(if cfg!(windows) {
            "stockfish.exe"
        } else {
            "stockfish"
        });
        assert_eq!(
            variables.get(std::ffi::OsStr::new("SCAN64_STOCKFISH_PATH")),
            Some(&Some(expected.as_os_str()))
        );
        assert_eq!(
            variables.get(std::ffi::OsStr::new("PATH")),
            Some(&Some(std::ffi::OsStr::new("original")))
        );
        assert_eq!(command.get_current_dir(), Some(engine_dir.as_path()));
        fs::remove_dir_all(root.parent().unwrap()).unwrap();
    }

    #[test]
    fn source_server_without_resources_keeps_its_engine_lookup() {
        let mut command = Command::new("unused");
        command.env_clear();
        configure_engine(&mut command, &env::temp_dir().join(new_token().unwrap()));
        assert_eq!(command.get_envs().count(), 0);
        assert!(command.get_current_dir().is_none());
    }

    #[test]
    fn tokens_are_unpredictable_and_header_safe() {
        let first = new_token().unwrap();
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_ne!(first, new_token().unwrap());
    }

    #[test]
    fn port_is_loopback_and_nonzero() {
        assert_ne!(free_port().unwrap(), 0);
    }

    #[test]
    fn failure_identifies_log_without_credentials() {
        let server = Server::new(PathBuf::from("data"), PathBuf::from("resources"));
        assert!(server.error("Stopped").contains("server.log"));
    }

    #[test]
    fn data_directory_failure_is_reported_and_can_be_retried() {
        let data_dir = env::temp_dir().join(new_token().unwrap());
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&data_dir)
            .unwrap();
        drop(file);
        let mut server = Server::new(data_dir.clone(), PathBuf::from("resources"));
        let initial = server.connection().err().expect("A file cannot hold logs");
        let retry = server
            .restart()
            .err()
            .expect("Restart must report the failure");
        fs::remove_file(data_dir).unwrap();
        assert!(initial.contains("Cannot create the private log directory"));
        assert!(initial.contains("server.log"));
        assert_eq!(initial, retry);
    }

    #[cfg(unix)]
    #[test]
    fn data_and_log_directories_are_owner_only_even_if_they_already_exist() {
        use std::os::unix::fs::PermissionsExt;
        let data_dir = env::temp_dir().join(new_token().unwrap());
        let logs = data_dir.join("logs");
        fs::create_dir_all(&logs).unwrap();
        fs::set_permissions(&data_dir, fs::Permissions::from_mode(0o755)).unwrap();
        fs::set_permissions(&logs, fs::Permissions::from_mode(0o755)).unwrap();
        private_directory(&data_dir).unwrap();
        private_directory(&logs).unwrap();
        let data_mode = fs::metadata(&data_dir).unwrap().permissions().mode() & 0o777;
        let logs_mode = fs::metadata(&logs).unwrap().permissions().mode() & 0o777;
        fs::remove_dir_all(data_dir).unwrap();
        assert_eq!(data_mode, 0o700);
        assert_eq!(logs_mode, 0o700);
    }

    #[cfg(unix)]
    #[test]
    fn hard_stop_kills_the_group_when_the_server_does_not_read_eof() {
        use std::io::{BufRead, BufReader};
        use std::os::unix::process::CommandExt;

        let mut server = Server::new("unused".into(), "unused".into());
        server.child = Some(
            Command::new("sh")
                .args(["-c", "sleep 60 & echo $!; wait"])
                .process_group(0)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .spawn()
                .unwrap(),
        );
        let group = server.pid().unwrap();
        let stdout = server.child.as_mut().unwrap().stdout.take().unwrap();
        let mut pid = String::new();
        BufReader::new(stdout).read_line(&mut pid).unwrap();
        let pid = pid.trim();
        assert!(pid.parse::<u32>().unwrap() > 0);
        server.stop();

        let deadline = Instant::now() + Duration::from_millis(500);
        let stopped = loop {
            let state = Command::new("ps")
                .args(["-o", "stat=", "-p", pid])
                .output()
                .unwrap();
            let state = String::from_utf8_lossy(&state.stdout);
            // An exited orphan may briefly remain a zombie until init reaps it.
            if state.trim().is_empty() || state.trim_start().starts_with('Z') {
                break true;
            }
            if Instant::now() >= deadline {
                break false;
            }
            thread::sleep(Duration::from_millis(10));
        };
        if !stopped {
            // SAFETY: this test created and still owns this isolated group.
            unsafe {
                libc::killpg(group as libc::pid_t, libc::SIGKILL);
            }
        }
        assert!(
            stopped,
            "The server's uncooperative descendant survived stop"
        );
    }
}
