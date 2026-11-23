use std::process::{Command, Stdio};

const RELEASES: &str = "https://github.com/Mathews-Tom/Scan64/releases/";

fn numeric_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| byte.is_ascii_digit())
        && (value.len() == 1 || !value.starts_with('0'))
}

fn identifiers(value: &str, prerelease: bool) -> bool {
    value.split('.').all(|identifier| {
        !identifier.is_empty()
            && identifier
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
            && (!prerelease
                || !identifier.bytes().all(|byte| byte.is_ascii_digit())
                || numeric_identifier(identifier))
    })
}

fn valid_version(version: &str) -> bool {
    let core_and_pre = if let Some((version, build)) = version.split_once('+') {
        if !identifiers(build, false) {
            return false;
        }
        version
    } else {
        version
    };
    let core = if let Some((core, prerelease)) = core_and_pre.split_once('-') {
        if !identifiers(prerelease, true) {
            return false;
        }
        core
    } else {
        core_and_pre
    };
    let mut components = core.split('.');
    (0..3).all(|_| components.next().is_some_and(numeric_identifier)) && components.next().is_none()
}

fn allowed_release_url(url: &str) -> bool {
    // Match the raw string, not a normalized URL: credentials, ports, escapes,
    // traversal, queries and fragments must never broaden this allowlist.
    let Some(destination) = url.strip_prefix(RELEASES) else {
        return false;
    };
    destination == "latest" || destination.strip_prefix("tag/v").is_some_and(valid_version)
}

fn open_with(url: &str, opener: impl FnOnce(&str) -> Result<(), String>) -> Result<(), String> {
    if !allowed_release_url(url) {
        return Err("Only Scan64 GitHub release links can be opened".to_owned());
    }
    opener(url)
}

pub(crate) fn system_browser(url: &str) -> Result<(), String> {
    // Fixed programs and separate arguments; never use a command shell.
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("/usr/bin/open");
        command.arg("--");
        command
    };
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut command = Command::new("rundll32.exe");
        command.arg("url.dll,FileProtocolHandler");
        command
    };
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let mut command = Command::new("xdg-open");
    let status = command
        .arg(url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|error| format!("Cannot open link: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err("Cannot open link in the system browser".to_owned())
    }
}

#[tauri::command]
pub(crate) async fn open_release_url(url: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || open_with(&url, system_browser))
        .await
        .map_err(|_| "Cannot open release link".to_owned())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opens_only_the_exact_constructed_release_destinations() {
        for destination in [
            "latest",
            "tag/v0.6.2",
            "tag/v1.0.0-rc.1",
            "tag/v0.0.0-alpha.0+build.01",
            "tag/v12.34.56+build-x",
        ] {
            let url = format!("{RELEASES}{destination}");
            let mut opened = false;
            open_with(&url, |actual| {
                assert_eq!(actual, url);
                opened = true;
                Ok(())
            })
            .unwrap();
            assert!(opened);
        }
    }

    #[test]
    fn rejects_url_and_path_bypasses_before_calling_the_opener() {
        for url in [
            "http://github.com/Mathews-Tom/Scan64/releases/latest",
            "HTTPS://github.com/Mathews-Tom/Scan64/releases/latest",
            "https://GITHUB.COM/Mathews-Tom/Scan64/releases/latest",
            "https://github.com.evil.test/Mathews-Tom/Scan64/releases/latest",
            "https://github.com@evil.test/Mathews-Tom/Scan64/releases/latest",
            "https://user@github.com/Mathews-Tom/Scan64/releases/latest",
            "https://user:password@github.com/Mathews-Tom/Scan64/releases/latest",
            "https://github.com:443/Mathews-Tom/Scan64/releases/latest",
            "https://github.com./Mathews-Tom/Scan64/releases/latest",
            "https://github.com/Other/Scan64/releases/latest",
            "https://github.com/Mathews-Tom/scan64/releases/latest",
            "https://github.com/Mathews-Tom/Scan64/releases/latest?download=1",
            "https://github.com/Mathews-Tom/Scan64/releases/latest#notes",
            "https://github.com/Mathews-Tom/Scan64/releases/../issues",
            "https://github.com/Mathews-Tom/Scan64/releases/tag/v1.2.3/../../issues",
            "https://github.com/Mathews-Tom/Scan64/releases/tag/v1.2.3%2f..",
            "https://github.com/Mathews-Tom/Scan64/releases/%6catest",
            "https://github.com/Mathews-Tom/Scan64/releases/%252e%252e/issues",
            "https://github.com/Mathews-Tom/Scan64/releases/./latest",
            "https://github.com/Mathews-Tom/Scan64/releases//latest",
            "https://github.com/Mathews-Tom/Scan64/releases/latest/",
            "https://github.com/Mathews-Tom/Scan64/releases/download/v1.2.3/app.exe",
            "https://github.com/Mathews-Tom/Scan64/releases/tag/v1.2.3?x",
            "https://github.com/Mathews-Tom/Scan64/releases/tag/v1.2.3#x",
            "https://github.com/Mathews-Tom/Scan64/releases/tag/v1.2.3\\evil",
            "https://github.com/Mathews-Tom/Scan64/releases/latest\n",
            " https://github.com/Mathews-Tom/Scan64/releases/latest",
            "javascript:alert(1)",
            "file:///tmp/Scan64",
            "--help",
        ] {
            assert!(open_with(url, |_| panic!("opener called for {url}")).is_err());
        }
    }

    #[test]
    fn rejects_non_semver_tags_without_opening_a_browser() {
        for version in [
            "",
            "1",
            "1.2",
            "1.2.3.4",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "v1.2.3",
            "1.2.3-",
            "1.2.3+",
            "1.2.3-01",
            "1.2.3-alpha..1",
            "1.2.3+build..1",
            "1.2.3+build+other",
            "1.2.3-α",
            "1.2.3-rc_1",
            "1.2.3%2bbuild",
        ] {
            let url = format!("{RELEASES}tag/v{version}");
            assert!(open_with(&url, |_| panic!("opener called for {url}")).is_err());
        }
    }

    #[test]
    fn preserves_opener_failure_for_the_ui() {
        assert_eq!(
            open_with(&format!("{RELEASES}latest"), |_| Err(
                "No browser".to_owned()
            )),
            Err("No browser".to_owned())
        );
    }
}
