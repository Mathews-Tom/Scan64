use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tauri_plugin_updater::{Update, UpdaterExt};

const DOWNLOAD_URL: &str = "https://github.com/Mathews-Tom/Scan64/releases/latest";
const CHECK_INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
const STATUS_EVENT: &str = "scan64-update-status";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Phase {
    Idle,
    Checking,
    UpToDate,
    Available,
    Downloading,
    Ready,
    Installing,
    Failed,
    Unavailable,
}

impl Phase {
    fn permits(self, next: Self) -> bool {
        use Phase::*;
        matches!(
            (self, next),
            (Idle | UpToDate | Available | Failed, Checking)
                | (Checking, UpToDate | Available | Failed)
                | (Available | Failed, Downloading)
                | (Downloading, Ready | Failed)
                | (Ready, Installing)
                | (Installing, Failed)
        )
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateStatus {
    revision: u64,
    state: Phase,
    installed_version: String,
    available_version: Option<String>,
    percent: Option<u8>,
    last_checked: Option<u64>,
    automatic: bool,
    reason: Option<String>,
    download_url: &'static str,
    release_notes_url: String,
}

impl UpdateStatus {
    fn new(version: String, automatic: bool, unavailable: Option<String>) -> Self {
        Self {
            revision: 0,
            state: if unavailable.is_some() {
                Phase::Unavailable
            } else {
                Phase::Idle
            },
            installed_version: version,
            available_version: None,
            percent: None,
            last_checked: None,
            automatic,
            reason: unavailable,
            download_url: DOWNLOAD_URL,
            release_notes_url: DOWNLOAD_URL.to_owned(),
        }
    }

    fn transition(&mut self, phase: Phase) -> Result<(), String> {
        if !self.state.permits(phase) {
            return Err("An update operation is already in progress or is not ready".to_owned());
        }
        self.state = phase;
        if phase != Phase::Failed {
            self.reason = None;
        }
        Ok(())
    }

    fn should_download(&self) -> bool {
        self.automatic && self.state == Phase::Available
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Preferences {
    automatic: bool,
}

fn load_preferences(path: &Path) -> Result<bool, String> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice::<Preferences>(&bytes)
            .map(|preferences| preferences.automatic)
            .map_err(|_| "Cannot read the saved update preference".to_owned()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(_) => Err("Cannot read the saved update preference".to_owned()),
    }
}

fn save_preferences(path: &Path, automatic: bool) -> Result<(), String> {
    let pending = path.with_extension("pending");
    let bytes = serde_json::to_vec(&Preferences { automatic })
        .map_err(|_| "Cannot encode the update preference".to_owned())?;
    let result = (|| -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&pending)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&pending, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(pending);
    }
    result.map_err(|_| "Cannot save the update preference".to_owned())
}

pub struct Controller<R: Runtime = tauri::Wry> {
    app: AppHandle<R>,
    status: UpdateStatus,
    preferences: PathBuf,
    update: Option<Update>,
    // Only bytes returned by the plugin's signature-verifying download reach Ready.
    bytes: Option<Vec<u8>>,
}

pub type SharedUpdater = Arc<Mutex<Controller>>;

impl Controller {
    pub fn new(app: &AppHandle, data_dir: &Path) -> Self {
        let preferences = data_dir.join("updater.json");
        let saved = load_preferences(&preferences);
        let mut status = UpdateStatus::new(
            app.package_info().version.to_string(),
            saved.as_ref().copied().unwrap_or(false),
            unavailable_reason(app),
        );
        if let Err(reason) = saved {
            if status.state != Phase::Unavailable {
                status.state = Phase::Failed;
                status.reason = Some(reason);
            }
        }
        Self {
            status,
            app: app.clone(),
            preferences,
            update: None,
            bytes: None,
        }
    }
}

impl<R: Runtime> Controller<R> {
    fn publish(&mut self) {
        self.status.revision = self
            .status
            .revision
            .checked_add(1)
            .expect("Updater revision overflow");
        if let Err(error) = self.app.emit(STATUS_EVENT, &self.status) {
            eprintln!("Cannot emit updater status: {error}");
        }
        #[cfg(feature = "updater-test")]
        smoke::snapshot(&self.status);
    }

    fn begin_check(&mut self) -> bool {
        if self.status.transition(Phase::Checking).is_err() {
            return false;
        }
        self.update = None;
        self.bytes = None;
        self.status.available_version = None;
        self.status.percent = None;
        self.publish();
        true
    }

    fn checked(&mut self, update: Option<Update>) {
        self.status.last_checked = Some(now_ms());
        if let Some(update) = update {
            self.status.available_version = Some(update.version.clone());
            // Construct our own trusted GitHub link, never a manifest-controlled URL.
            self.status.release_notes_url = format!(
                "https://github.com/Mathews-Tom/Scan64/releases/tag/v{}",
                update.version
            );
            self.update = Some(update);
            self.status
                .transition(Phase::Available)
                .expect("check owns state");
        } else {
            self.status.release_notes_url = DOWNLOAD_URL.to_owned();
            self.status
                .transition(Phase::UpToDate)
                .expect("check owns state");
        }
        self.publish();
    }

    fn failed(&mut self, reason: &str) {
        self.status
            .transition(Phase::Failed)
            .expect("operation owns state");
        self.status.reason = Some(reason.to_owned());
        self.status.percent = None;
        self.bytes = None;
        self.publish();
    }

    fn progress(&mut self, received: u64, total: Option<u64>) {
        let percent = total
            .filter(|total| *total > 0)
            .map(|total| ((u128::from(received) * 100 / u128::from(total)).min(99)) as u8);
        if self.status.percent != percent {
            self.status.percent = percent;
            self.publish();
        }
    }

    fn ready(&mut self, bytes: Vec<u8>) {
        self.bytes = Some(bytes);
        self.status.percent = Some(100);
        self.status
            .transition(Phase::Ready)
            .expect("download owns state");
        self.publish();
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn controller(app: &AppHandle) -> SharedUpdater {
    app.state::<SharedUpdater>().inner().clone()
}

#[tauri::command]
pub fn update_status(app: AppHandle) -> Result<UpdateStatus, String> {
    controller(&app)
        .lock()
        .map(|state| state.status.clone())
        .map_err(|_| "Update state unavailable".to_owned())
}

#[tauri::command]
pub async fn check_updates(app: AppHandle) -> Result<UpdateStatus, String> {
    let shared = controller(&app);
    {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        if !state.begin_check() {
            return Ok(state.status.clone());
        }
    }
    let builder = app.updater_builder().timeout(Duration::from_secs(120));
    #[cfg(windows)]
    let builder = {
        let server = app.state::<crate::SharedServer>().inner().clone();
        // The plugin's default callback irreversibly tears down/hides the UI
        // BEFORE ShellExecute, even when the installer cannot launch. Keep our
        // child-exit cleanup, but let successful process exit release UI resources
        // so a failed installer launch can recover the still-live application.
        builder.on_before_exit(move || crate::stop_on_exit(server.as_ref()))
    };
    let result = match builder.build() {
        Ok(updater) => updater.check().await,
        Err(error) => Err(error),
    };
    let automatic = {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        match result {
            Ok(update) => state.checked(update),
            Err(_) => {
                state.status.last_checked = Some(now_ms());
                state.failed("Cannot check for updates. Check your connection and retry.");
            }
        }
        state.status.should_download()
    };
    if automatic {
        download(app, true).await
    } else {
        update_status(app)
    }
}

fn permitted_download(update: &Update) -> bool {
    let url = &update.download_url;
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    #[cfg(feature = "updater-test")]
    {
        url.scheme() == "http"
            && matches!(url.host_str(), Some("127.0.0.1" | "[::1]" | "localhost"))
    }
    #[cfg(not(feature = "updater-test"))]
    {
        url.scheme() == "https"
    }
}

fn download_failure(error: &tauri_plugin_updater::Error) -> &'static str {
    use tauri_plugin_updater::Error;
    match error {
        Error::SignedVersionMismatch { .. } | Error::MissingSignedVersion => {
            "The update signature does not authenticate the announced version. Retry or download Scan64 from GitHub."
        }
        Error::Minisign(_) | Error::Base64(_) | Error::SignatureUtf8(_) => {
            "The update signature verification failed. Retry or download Scan64 from GitHub."
        }
        _ => "The update download failed. Check your connection and retry.",
    }
}

#[tauri::command]
pub async fn download_update(app: AppHandle) -> Result<UpdateStatus, String> {
    download(app, false).await
}

async fn download(app: AppHandle, automatic_only: bool) -> Result<UpdateStatus, String> {
    let shared = controller(&app);
    let update = {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        // Re-read the preference under the operation lock: switching off after
        // a check's network response must not start a queued automatic download.
        if automatic_only && !state.status.should_download() {
            return Ok(state.status.clone());
        }
        if state.update.is_none() || !matches!(state.status.state, Phase::Available | Phase::Failed)
        {
            return Ok(state.status.clone());
        }
        state.status.transition(Phase::Downloading)?;
        state.status.percent = Some(0);
        state.publish();
        state.update.take().expect("update checked")
    };
    if !permitted_download(&update) {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        state.update = Some(update);
        state.failed(
            "The update download address is not secure. Download Scan64 from GitHub instead.",
        );
        drop(state);
        return update_status(app);
    }
    let progress = shared.clone();
    let mut received = 0_u64;
    let result = update
        .download(
            move |count, total| {
                received = received.saturating_add(count as u64);
                if let Ok(mut state) = progress.lock() {
                    state.progress(received, total);
                }
            },
            || {},
        )
        .await;
    {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        state.update = Some(update);
        match result {
            Ok(bytes) => state.ready(bytes),
            Err(error) => state.failed(download_failure(&error)),
        }
    }
    update_status(app)
}

#[tauri::command]
pub async fn set_automatic_updates(app: AppHandle, enabled: bool) -> Result<UpdateStatus, String> {
    let shared = controller(&app);
    let automatic = {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        save_preferences(&state.preferences, enabled)?;
        state.status.automatic = enabled;
        state.publish();
        state.status.should_download()
    };
    if automatic {
        download(app, true).await
    } else {
        update_status(app)
    }
}

#[tauri::command]
pub async fn restart_to_update(app: AppHandle) -> Result<UpdateStatus, String> {
    let shared = controller(&app);
    let (update, bytes) = {
        let mut state = shared.lock().map_err(|_| "Update state unavailable")?;
        if state.status.state != Phase::Ready {
            return Ok(state.status.clone());
        }
        state.status.transition(Phase::Installing)?;
        state.publish();
        (
            state.update.take().expect("ready update"),
            state.bytes.take().expect("verified ready bytes"),
        )
    };
    let server = app.state::<crate::SharedServer>().inner().clone();
    let install_server = server.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        install_server
            .lock()
            .map_err(|_| "Server state unavailable".to_owned())?
            .stop_for_update()?;
        update
            .install(&bytes)
            .map_err(|_| "The update could not be installed".to_owned())
    })
    .await
    .unwrap_or_else(|_| Err("Update installation task failed".to_owned()));
    if result.is_err() {
        let recovery = tauri::async_runtime::spawn_blocking(move || {
            let mut server = server
                .lock()
                .map_err(|_| "Server state unavailable".to_owned())?;
            server.recover_after_update()
        })
        .await;
        let recovered = matches!(recovery, Ok(Ok(_)));
        let reason = if recovered {
            "Update installation failed. Your server has restarted; retry the update check."
        } else {
            "Update installation failed and the server could not restart. Restart Scan64 to recover."
        };
        shared
            .lock()
            .map_err(|_| "Update state unavailable")?
            .failed(reason);
        if recovered {
            // A recovered child has a fresh port/token. Reload only the local UI so
            // its connection bootstrap cannot keep using the stopped child's token.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.location.reload()");
            }
        }
        return update_status(app);
    }
    // Windows's verified installer exits and relaunches the app itself. Other
    // platforms reach here only after install succeeds, never on download alone.
    app.request_restart();
    update_status(app)
}

#[derive(Clone, Copy)]
enum Installation {
    LinuxPackage,
    AppImage,
    MacBundle,
    Windows,
}

fn installation_reason(
    installation: Installation,
    executable: &Path,
    writable: bool,
) -> Option<String> {
    if matches!(installation, Installation::LinuxPackage) {
        return Some("In-app updates are unavailable for Linux .deb installations. Download the latest package from GitHub.".to_owned());
    }
    if matches!(installation, Installation::MacBundle)
        && executable
            .components()
            .any(|part| part.as_os_str() == "AppTranslocation")
    {
        return Some("Move Scan64 to Applications and reopen it to enable updates (macOS App Translocation).".to_owned());
    }
    if !writable {
        return Some("The installation location is read-only. Move Scan64 to a writable location or download the latest release from GitHub.".to_owned());
    }
    None
}

fn writable_location(path: &Path) -> bool {
    if !fs::metadata(path).is_ok_and(|metadata| !metadata.permissions().readonly()) {
        return false;
    }
    let Some(parent) = path.parent() else {
        return false;
    };
    if !fs::metadata(parent).is_ok_and(|metadata| !metadata.permissions().readonly()) {
        return false;
    }
    let probe = parent.join(format!(".scan64-update-write-{}", std::process::id()));
    match OpenOptions::new().write(true).create_new(true).open(&probe) {
        Ok(file) => {
            drop(file);
            fs::remove_file(probe).is_ok()
        }
        Err(_) => false,
    }
}

fn unavailable_reason(app: &AppHandle) -> Option<String> {
    let Ok(executable) = tauri::process::current_binary(&app.env()) else {
        return Some(
            "Cannot locate the installed application. Download the latest release from GitHub."
                .to_owned(),
        );
    };
    #[cfg(target_os = "linux")]
    let appimage = app.env().appimage.is_some();
    #[cfg(not(target_os = "linux"))]
    let appimage = false;
    let installation = if cfg!(target_os = "linux") {
        if appimage {
            Installation::AppImage
        } else {
            Installation::LinuxPackage
        }
    } else if cfg!(target_os = "macos") {
        Installation::MacBundle
    } else {
        Installation::Windows
    };
    if let Some(reason) = installation_reason(installation, &executable, true) {
        return Some(reason);
    }
    let location = if matches!(installation, Installation::MacBundle) {
        executable
            .ancestors()
            .find(|path| path.extension().is_some_and(|extension| extension == "app"))
            .unwrap_or(&executable)
    } else {
        &executable
    };
    installation_reason(installation, &executable, writable_location(location))
}

pub fn start(app: AppHandle) {
    #[cfg(feature = "updater-test")]
    if smoke::start(app.clone()) {
        return;
    }
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(10));
        loop {
            let _ = tauri::async_runtime::block_on(check_updates(app.clone()));
            thread::sleep(CHECK_INTERVAL);
        }
    });
}

#[cfg(feature = "updater-test")]
mod smoke;

#[cfg(feature = "updater-test")]
pub(crate) use smoke::{observe_page_load, observe_ui, prepare_smoke_launch};

#[cfg(test)]
#[path = "../updater_config.rs"]
mod updater_config_tests;

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::{
        test::{mock_builder, mock_context, noop_assets, MockRuntime},
        Listener,
    };

    fn state(phase: Phase) -> UpdateStatus {
        let mut state = UpdateStatus::new("0.6.1".to_owned(), true, None);
        state.state = phase;
        state
    }

    fn controller(phase: Phase) -> Controller<MockRuntime> {
        let app = mock_builder().build(mock_context(noop_assets())).unwrap();
        Controller {
            app: app.handle().clone(),
            status: state(phase),
            preferences: PathBuf::new(),
            update: None,
            bytes: None,
        }
    }

    #[test]
    fn publishes_authoritative_snapshots_in_revision_order() {
        let app = mock_builder().build(mock_context(noop_assets())).unwrap();
        let events = Arc::new(Mutex::new(Vec::new()));
        let received = events.clone();
        let listener = app.listen(STATUS_EVENT, move |event| {
            received
                .lock()
                .unwrap()
                .push(serde_json::from_str::<serde_json::Value>(event.payload()).unwrap());
        });
        let mut state = controller(Phase::Idle);
        state.app = app.handle().clone();
        assert_eq!(state.status.revision, 0);
        assert!(state.begin_check());
        assert!(!state.begin_check());
        state.checked(None);
        assert!(state.begin_check());
        state.failed("Cannot check for updates.");
        app.unlisten(listener);
        let events = events.lock().unwrap();
        assert_eq!(events.len(), 4);
        for (index, event) in events.iter().enumerate() {
            assert_eq!(event["revision"], (index + 1) as u64);
            assert_eq!(event["installedVersion"], "0.6.1");
            assert!(event.get("installed_version").is_none());
        }
        assert_eq!(events[0]["state"], "checking");
        assert_eq!(events[1]["state"], "upToDate");
        assert_eq!(events[2]["state"], "checking");
        assert_eq!(events[3]["state"], "failed");
        assert_eq!(events[3], serde_json::to_value(&state.status).unwrap());
    }

    #[test]
    fn publishes_progress_only_when_percent_changes_and_preserves_verified_terminal_state() {
        let mut state = controller(Phase::Downloading);
        let events = Arc::new(Mutex::new(Vec::new()));
        let received = events.clone();
        let listener = state.app.listen(STATUS_EVENT, move |event| {
            received
                .lock()
                .unwrap()
                .push(serde_json::from_str::<serde_json::Value>(event.payload()).unwrap());
        });
        state.progress(1, Some(1000));
        state.progress(2, Some(1000));
        state.progress(500, Some(1000));
        state.progress(501, Some(1000));
        state.progress(1000, Some(1000));
        state.ready(vec![1, 2, 3]);
        state.app.unlisten(listener);
        let events = events.lock().unwrap();
        assert_eq!(
            events
                .iter()
                .map(|event| event["percent"].as_u64().unwrap())
                .collect::<Vec<_>>(),
            vec![0, 50, 99, 100]
        );
        assert_eq!(state.status.revision, 4);
        assert_eq!(events[3]["state"], "ready");
        assert_eq!(events[3], serde_json::to_value(&state.status).unwrap());
        assert_eq!(state.bytes.as_deref(), Some([1, 2, 3].as_slice()));
    }

    #[test]
    fn every_state_transition_is_explicit_and_invalid_transitions_do_not_mutate() {
        use Phase::*;
        let phases = [
            Idle,
            Checking,
            UpToDate,
            Available,
            Downloading,
            Ready,
            Installing,
            Failed,
            Unavailable,
        ];
        let allowed = [
            (Idle, Checking),
            (UpToDate, Checking),
            (Available, Checking),
            (Failed, Checking),
            (Checking, UpToDate),
            (Checking, Available),
            (Checking, Failed),
            (Available, Downloading),
            (Failed, Downloading),
            (Downloading, Ready),
            (Downloading, Failed),
            (Ready, Installing),
            (Installing, Failed),
        ];
        for from in phases {
            for to in phases {
                let mut status = state(from);
                assert_eq!(
                    status.transition(to).is_ok(),
                    allowed.contains(&(from, to)),
                    "{from:?} -> {to:?}"
                );
                assert_eq!(
                    status.state,
                    if allowed.contains(&(from, to)) {
                        to
                    } else {
                        from
                    }
                );
            }
        }
    }

    #[test]
    fn enabling_automatic_downloads_an_existing_offer_but_disabling_during_check_does_not() {
        let mut available = state(Phase::Available);
        available.automatic = false;
        assert!(!available.should_download());
        available.automatic = true;
        assert!(available.should_download());
        assert_eq!(available.state, Phase::Available);
        let mut checking = state(Phase::Checking);
        checking.automatic = false;
        checking.transition(Phase::Available).unwrap();
        assert!(!checking.should_download());
    }

    #[test]
    fn automatic_download_never_means_automatic_install() {
        for phase in [
            Phase::Idle,
            Phase::Checking,
            Phase::UpToDate,
            Phase::Available,
            Phase::Downloading,
            Phase::Ready,
            Phase::Installing,
            Phase::Failed,
            Phase::Unavailable,
        ] {
            let mut status = state(phase);
            assert_eq!(status.should_download(), phase == Phase::Available);
            status.automatic = false;
            assert!(!status.should_download());
        }
    }

    #[test]
    fn duplicate_check_and_check_during_download_or_ready_preserve_work() {
        for phase in [
            Phase::Checking,
            Phase::Downloading,
            Phase::Ready,
            Phase::Installing,
            Phase::Unavailable,
        ] {
            let mut state = controller(phase);
            state.bytes = Some(vec![1, 2, 3]);
            assert!(!state.begin_check());
            assert_eq!(state.status.state, phase);
            assert_eq!(state.bytes, Some(vec![1, 2, 3]));
        }
    }

    #[test]
    fn failed_operations_clear_payloads_and_check_can_retry() {
        for phase in [Phase::Checking, Phase::Downloading, Phase::Installing] {
            let mut state = controller(phase);
            state.bytes = Some(vec![1]);
            state.failed("failure");
            assert!(state.bytes.is_none());
            assert_eq!(state.status.reason.as_deref(), Some("failure"));
            assert!(state.begin_check());
            assert!(state.status.reason.is_none());
        }
    }

    #[test]
    fn progress_waits_for_verification_before_ready() {
        let mut state = controller(Phase::Downloading);
        state.progress(4, None);
        assert_eq!(state.status.percent, None);
        state.progress(5, Some(10));
        assert_eq!(state.status.percent, Some(50));
        state.progress(12, Some(10));
        assert_eq!(state.status.percent, Some(99));
        assert_eq!(state.status.state, Phase::Downloading);
        state.ready(vec![1, 2]);
        assert_eq!(state.status.state, Phase::Ready);
        assert_eq!(state.status.percent, Some(100));
        assert_eq!(state.bytes, Some(vec![1, 2]));
    }

    #[test]
    fn no_update_records_check_time_and_up_to_date() {
        let mut state = controller(Phase::Checking);
        state.checked(None);
        assert_eq!(state.status.state, Phase::UpToDate);
        assert!(state.status.last_checked.is_some());
    }

    #[test]
    fn automatic_preference_defaults_on_and_persists_both_values() {
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random).unwrap();
        let directory = std::env::temp_dir().join(format!("scan64-update-{random:x?}"));
        fs::create_dir(&directory).unwrap();
        let path = directory.join("updater.json");
        assert!(load_preferences(&path).unwrap());
        save_preferences(&path, false).unwrap();
        assert!(!load_preferences(&path).unwrap());
        save_preferences(&path, true).unwrap();
        assert!(load_preferences(&path).unwrap());
        fs::write(&path, b"broken").unwrap();
        assert!(load_preferences(&path).is_err());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn every_unavailable_reason_has_a_remediation() {
        assert!(installation_reason(
            Installation::LinuxPackage,
            Path::new("/usr/bin/scan64"),
            true
        )
        .unwrap()
        .contains(".deb"));
        assert!(installation_reason(
            Installation::MacBundle,
            Path::new("/private/var/folders/x/AppTranslocation/y/Scan64.app/Contents/MacOS/scan64"),
            true
        )
        .unwrap()
        .contains("Applications"));
        for kind in [
            Installation::AppImage,
            Installation::MacBundle,
            Installation::Windows,
        ] {
            assert!(installation_reason(kind, Path::new("/apps/Scan64"), false)
                .unwrap()
                .contains("read-only"));
            assert!(installation_reason(kind, Path::new("/apps/Scan64"), true).is_none());
        }
        let status = UpdateStatus::new("0.6.1".to_owned(), true, Some("unavailable".to_owned()));
        assert_eq!(status.state, Phase::Unavailable);
        assert_eq!(status.download_url, DOWNLOAD_URL);
    }

    #[test]
    fn network_failures_cannot_be_mistaken_for_signature_rejections() {
        assert!(
            !download_failure(&tauri_plugin_updater::Error::Network("offline".to_owned()))
                .contains("signature")
        );
        assert!(download_failure(&tauri_plugin_updater::Error::Minisign(
            minisign_verify::Error::InvalidSignature
        ))
        .contains("signature"));
        assert!(
            download_failure(&tauri_plugin_updater::Error::SignatureUtf8(
                "redacted".to_owned()
            ))
            .contains("signature")
        );
    }

    #[test]
    fn signed_version_failures_are_signature_rejections() {
        for error in [
            tauri_plugin_updater::Error::SignedVersionMismatch {
                signed: "0.6.1".to_owned(),
                announced: "0.6.2".to_owned(),
            },
            tauri_plugin_updater::Error::MissingSignedVersion,
        ] {
            assert!(download_failure(&error).contains("signature"));
            assert!(download_failure(&error).contains("version"));
        }
    }

    #[test]
    fn real_read_only_installation_is_detected_without_overwriting_anything() {
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random).unwrap();
        let directory = std::env::temp_dir().join(format!("scan64-readonly-{random:x?}"));
        fs::create_dir(&directory).unwrap();
        let executable = directory.join("Scan64");
        fs::write(&executable, b"fixture").unwrap();
        assert!(writable_location(&executable));
        let original = fs::metadata(&executable).unwrap().permissions();
        let mut read_only = original.clone();
        read_only.set_readonly(true);
        fs::set_permissions(&executable, read_only).unwrap();
        assert!(!writable_location(&executable));
        fs::set_permissions(&executable, original).unwrap();
        assert_eq!(fs::read(&executable).unwrap(), b"fixture");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn serialized_contract_is_camel_case() {
        let value = serde_json::to_value(state(Phase::UpToDate)).unwrap();
        assert_eq!(value["state"], "upToDate");
        for key in [
            "installedVersion",
            "availableVersion",
            "lastChecked",
            "downloadUrl",
            "releaseNotesUrl",
        ] {
            assert!(value.get(key).is_some());
        }
    }
}
