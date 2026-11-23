//! Open a prefilled GitHub issue that the user reviews and submits; Scan64 sends nothing.

use crate::release_links::system_browser;

const NEW_ISSUE: &str = "https://github.com/Mathews-Tom/Scan64/issues/new";
const MAX_TITLE_CHARS: usize = 120;
const MAX_BODY_CHARS: usize = 2000;
// Browsers and GitHub reject much longer request lines; fail closed before opening.
const MAX_URL_BYTES: usize = 8000;

/// Percent-encode everything except RFC 3986 unreserved characters.
fn encode(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            encoded.push(char::from(byte));
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    encoded
}

fn plain_text(value: &str, multiline: bool) -> bool {
    value
        .chars()
        .all(|character| !character.is_control() || (multiline && character == '\n'))
}

/// Build the only URL this command may open: the repository's new-issue form.
fn issue_url(title: &str, body: &str) -> Result<String, String> {
    let title = title.trim();
    if title.is_empty() {
        return Err("Enter a short summary".to_owned());
    }
    if title.chars().count() > MAX_TITLE_CHARS || body.chars().count() > MAX_BODY_CHARS {
        return Err("Feedback is longer than the form allows".to_owned());
    }
    if !plain_text(title, false) || !plain_text(body, true) {
        return Err("Feedback can contain only plain text".to_owned());
    }
    let url = format!("{NEW_ISSUE}?title={}&body={}", encode(title), encode(body));
    if url.len() > MAX_URL_BYTES {
        return Err("Feedback is too long to open in a browser; shorten the details".to_owned());
    }
    Ok(url)
}

#[tauri::command]
pub(crate) async fn open_feedback_issue(title: String, body: String) -> Result<(), String> {
    let url = issue_url(&title, &body)?;
    tauri::async_runtime::spawn_blocking(move || system_browser(&url))
        .await
        .map_err(|_| "Cannot open the feedback form".to_owned())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opens_only_the_new_issue_form_with_every_field_encoded() {
        let url = issue_url(
            "  Bug: board & clock ?  ",
            "Steps #1\nhttps://evil.example/?x=1&y=2 é",
        )
        .unwrap();
        assert_eq!(
            url,
            "https://github.com/Mathews-Tom/Scan64/issues/new?title=Bug%3A%20board%20%26%20clock%20%3F\
             &body=Steps%20%231%0Ahttps%3A%2F%2Fevil.example%2F%3Fx%3D1%26y%3D2%20%C3%A9"
        );
        let (_, query) = url.split_once('?').unwrap();
        assert_eq!(query.matches('&').count(), 1);
        assert!(!query.contains('#'));
    }

    #[test]
    fn rejects_empty_oversized_and_control_character_feedback() {
        for (title, body) in [
            ("   ", "details"),
            (&"t".repeat(MAX_TITLE_CHARS + 1)[..], ""),
            ("title", &"b".repeat(MAX_BODY_CHARS + 1)[..]),
            ("line\nbreak", ""),
            ("title", "bell\u{7}"),
            ("title", "carriage\rreturn"),
        ] {
            assert!(issue_url(title, body).is_err(), "{title:?} {body:?}");
        }
    }

    #[test]
    fn rejects_a_body_whose_encoded_url_exceeds_the_browser_limit() {
        // 2000 four-byte characters encode to 24000 bytes, far past the URL limit.
        let body = "😀".repeat(MAX_BODY_CHARS);
        assert!(issue_url("title", &body).is_err());
        assert!(issue_url("title", &"a".repeat(MAX_BODY_CHARS)).is_ok());
    }
}
