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
}

pub struct Server {
    child: Option<Child>,
    connection: Option<Connection>,
    failure: Option<String>,
    data_dir: PathBuf,
    resource_dir: PathBuf,
    pub log_path: PathBuf,
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
        }
    }

    pub fn connection(&mut self) -> Result<Connection, String> {
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
        self.stop();
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
        fs::create_dir_all(self.log_path.parent().expect("log parent"))
            .map_err(|_| self.error("Cannot create the log directory"))?;
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
                .env("SCAN64_PORT", port.to_string())
                .env("SCAN64_SHELL_TOKEN", &token)
                .env("PYTHONUTF8", "1")
                .stdin(Stdio::piped());
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
        self.connection = None;
        if let Some(mut child) = self.child.take() {
            // EOF starts the server's bounded, process-tree shutdown watchdog.
            drop(child.stdin.take());
            let deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < deadline {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return;
                }
                thread::sleep(Duration::from_millis(25));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop();
    }
}

pub fn smoke_file() -> Option<PathBuf> {
    cfg!(debug_assertions)
        .then(|| env::var_os("SCAN64_SMOKE_FILE").map(PathBuf::from))
        .flatten()
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
