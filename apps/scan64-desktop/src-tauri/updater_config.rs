use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::Value;

const ENDPOINT: &str = "https://github.com/Mathews-Tom/Scan64/releases/latest/download/latest.json";
const PLACEHOLDER: &str = "SCAN64_PRODUCTION_UPDATER_PUBLIC_KEY_REQUIRED";

// Match the JSON merge-patch semantics used by tauri-build for TAURI_CONFIG.
pub fn merge(target: &mut Value, patch: Value) {
    if let Value::Object(patch) = patch {
        if !target.is_object() {
            *target = serde_json::json!({});
        }
        let target = target.as_object_mut().expect("object initialized");
        for (key, value) in patch {
            if value.is_null() {
                target.remove(&key);
            } else {
                merge(target.entry(key).or_insert(Value::Null), value);
            }
        }
    } else {
        *target = patch;
    }
}

pub fn validate(
    config: &Value,
    release: bool,
    test_feature: bool,
    test_opt_in: bool,
    dry_run: bool,
) -> Result<(), &'static str> {
    let updater = &config["plugins"]["updater"];
    let test = test_feature && test_opt_in;
    if test_feature != test_opt_in {
        return Err("updater-test requires the explicit SCAN64_UPDATER_TEST=1 build opt-in");
    }
    if updater["dangerousInsecureTransportProtocol"] == true && !test {
        return Err("insecure updater transport is test-only, even in a dry run");
    }
    if !test
        && (updater["dangerousAcceptInvalidCerts"] == true
            || updater["dangerousAcceptInvalidHostnames"] == true)
    {
        return Err("disabling updater TLS validation is test-only, even in a dry run");
    }
    if !test && updater["requireSignedVersion"] != true {
        return Err("production updater must require signed versions, even in a dry run");
    }
    if updater["allowDowngrades"] == true {
        return Err("updater downgrades are forbidden");
    }
    let endpoints = updater["endpoints"]
        .as_array()
        .ok_or("missing updater endpoints")?;
    if endpoints.is_empty() {
        return Err("missing updater endpoints");
    }
    for endpoint in endpoints {
        let endpoint = endpoint.as_str().ok_or("invalid updater endpoint")?;
        if !test && endpoint != ENDPOINT {
            return Err("production updater endpoint cannot be overridden");
        }
        if test {
            let url = url::Url::parse(endpoint).map_err(|_| "invalid test updater endpoint")?;
            if url.scheme() != "http"
                || !matches!(url.host_str(), Some("127.0.0.1" | "[::1]" | "localhost"))
                || !url.username().is_empty()
                || url.password().is_some()
            {
                return Err("test updater endpoint must be credential-free loopback HTTP");
            }
        }
    }
    let key = updater["pubkey"].as_str().unwrap_or_default();
    // Debug and explicitly marked PR dry runs may compile the placeholder but cannot
    // install an update with it. Test builds must always have their per-run real key.
    if !test && (!release || dry_run) && key == PLACEHOLDER {
        return Ok(());
    }
    let decoded = STANDARD
        .decode(key)
        .map_err(|_| "missing or invalid updater public key")?;
    let decoded = std::str::from_utf8(&decoded).map_err(|_| "invalid updater public key")?;
    minisign_verify::PublicKey::decode(decoded).map_err(|_| "invalid updater public key")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn public_key() -> String {
        // A public-only fixture, with no private signing material.
        let mut bytes = [1_u8; 42];
        bytes[..2].copy_from_slice(b"Ed");
        STANDARD.encode(format!(
            "untrusted comment: public test fixture\n{}\n",
            STANDARD.encode(bytes)
        ))
    }

    #[test]
    fn valid_public_keys_are_accepted_only_in_the_correct_boundary() {
        let mut config = config();
        config["plugins"]["updater"]["pubkey"] = public_key().into();
        assert!(validate(&config, true, false, false, false).is_ok());
        config["plugins"]["updater"]["endpoints"] =
            serde_json::json!(["http://127.0.0.1:9876/latest.json"]);
        config["plugins"]["updater"]["dangerousInsecureTransportProtocol"] = true.into();
        assert!(validate(&config, true, true, true, false).is_ok());
        assert!(validate(&config, true, false, false, true).is_err());
        config["plugins"]["updater"]["endpoints"] =
            serde_json::json!(["http://example.com/latest.json"]);
        assert!(validate(&config, true, true, true, false).is_err());
        config["plugins"]["updater"]["endpoints"] =
            serde_json::json!(["http://127.0.0.1:9876/latest.json"]);
        config["plugins"]["updater"]["allowDowngrades"] = true.into();
        assert!(validate(&config, true, true, true, false).is_err());
    }

    fn config() -> Value {
        serde_json::json!({"plugins": {"updater": {
            "pubkey": PLACEHOLDER, "endpoints": [ENDPOINT],
            "dangerousInsecureTransportProtocol": false,
            "requireSignedVersion": true
        }}})
    }

    #[test]
    fn placeholder_fails_release_unless_explicit_dry_run() {
        assert!(validate(&config(), true, false, false, false).is_err());
        assert!(validate(&config(), true, false, false, true).is_ok());
        assert!(validate(&config(), false, false, false, false).is_ok());
    }

    #[test]
    fn dry_run_never_admits_insecure_transport_or_endpoint_changes() {
        let mut config = config();
        config["plugins"]["updater"]["dangerousInsecureTransportProtocol"] = true.into();
        assert!(validate(&config, true, false, false, true).is_err());
        config["plugins"]["updater"]["dangerousInsecureTransportProtocol"] = false.into();
        config["plugins"]["updater"]["endpoints"] =
            serde_json::json!(["http://127.0.0.1/latest.json"]);
        assert!(validate(&config, true, false, false, true).is_err());
    }

    #[test]
    fn tls_validation_bypasses_are_test_only_even_in_debug_and_dry_runs() {
        for field in [
            "dangerousAcceptInvalidCerts",
            "dangerousAcceptInvalidHostnames",
        ] {
            for (release, dry_run) in [(true, false), (true, true), (false, false)] {
                let mut config = config();
                config["plugins"]["updater"]["pubkey"] = public_key().into();
                config["plugins"]["updater"][field] = true.into();
                assert_eq!(
                    validate(&config, release, false, false, dry_run),
                    Err("disabling updater TLS validation is test-only, even in a dry run")
                );
                config["plugins"]["updater"][field] = false.into();
                assert!(validate(&config, release, false, false, dry_run).is_ok());
            }
            let mut config = config();
            config["plugins"]["updater"]["pubkey"] = public_key().into();
            config["plugins"]["updater"]["endpoints"] =
                serde_json::json!(["http://127.0.0.1:9876/latest.json"]);
            config["plugins"]["updater"][field] = true.into();
            assert!(validate(&config, true, true, true, false).is_ok());
        }
    }

    #[test]
    fn production_requires_explicit_signed_versions_even_in_debug_and_dry_runs() {
        for value in [Value::Null, false.into(), "true".into()] {
            for (release, dry_run) in [(true, false), (true, true), (false, false)] {
                let mut config = config();
                config["plugins"]["updater"]["requireSignedVersion"] = value.clone();
                assert_eq!(
                    validate(&config, release, false, false, dry_run),
                    Err("production updater must require signed versions, even in a dry run")
                );
            }
        }
    }

    #[test]
    fn test_boundary_requires_both_feature_and_opt_in_and_real_key() {
        let mut config = config();
        assert!(validate(&config, true, true, false, true).is_err());
        assert!(validate(&config, true, false, true, true).is_err());
        config["plugins"]["updater"]["endpoints"] =
            serde_json::json!(["http://127.0.0.1/latest.json"]);
        assert!(validate(&config, true, true, true, true).is_err());
    }

    #[test]
    fn test_boundary_rejects_remote_and_credentialed_endpoints() {
        for endpoint in [
            "http://example.com/latest.json",
            "http://user@localhost/latest.json",
            "https://localhost/latest.json",
        ] {
            let mut config = config();
            config["plugins"]["updater"]["endpoints"] = serde_json::json!([endpoint]);
            assert!(validate(&config, true, true, true, false).is_err());
        }
    }

    #[test]
    fn merge_preserves_the_base_security_fields() {
        let mut config = config();
        merge(&mut config, serde_json::json!({"version": "0.6.1"}));
        assert_eq!(config["plugins"]["updater"]["endpoints"][0], ENDPOINT);
        assert_eq!(config["plugins"]["updater"]["requireSignedVersion"], true);
        merge(
            &mut config,
            serde_json::json!({"plugins": {"updater": null}}),
        );
        assert!(validate(&config, true, false, false, true).is_err());
    }
}
