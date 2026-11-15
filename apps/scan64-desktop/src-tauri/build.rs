mod updater_config;

fn main() {
    println!("cargo:rerun-if-changed=tauri.conf.json");
    for name in [
        "TAURI_CONFIG",
        "SCAN64_UPDATER_DRY_RUN",
        "SCAN64_UPDATER_TEST",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    let target = tauri_utils::platform::Target::from_triple(
        &std::env::var("TARGET").expect("Missing build target"),
    );
    let (mut config, paths) = tauri_utils::config::parse::read_from(
        target,
        &std::env::current_dir().expect("Missing build directory"),
    )
    .expect("Cannot read Tauri configuration");
    for path in paths {
        println!("cargo:rerun-if-changed={}", path.display());
    }
    if let Ok(overlay) = std::env::var("TAURI_CONFIG") {
        let overlay = serde_json::from_str(&overlay).expect("Invalid Tauri config overlay");
        updater_config::merge(&mut config, overlay);
    }
    updater_config::validate(
        &config,
        std::env::var("PROFILE").as_deref() == Ok("release"),
        std::env::var_os("CARGO_FEATURE_UPDATER_TEST").is_some(),
        std::env::var("SCAN64_UPDATER_TEST").as_deref() == Ok("1"),
        std::env::var("SCAN64_UPDATER_DRY_RUN").as_deref() == Ok("1"),
    )
    .unwrap_or_else(|reason| panic!("Unsafe updater configuration: {reason}"));
    tauri_build::build();
}
