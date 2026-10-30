fn main() {
    // Production must embed real frontend files, never navigate to a build-host
    // drive URL. Enforce this using Tauri's own deserializer before compilation.
    if std::env::var("PROFILE").as_deref() == Ok("release") {
        let root = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
        let base: serde_json::Value = serde_json::from_slice(&std::fs::read(root.join("tauri.conf.json")).unwrap()).unwrap();
        let overrides: serde_json::Value = serde_json::from_str(&std::env::var("TAURI_CONFIG").unwrap_or_else(|_| "{}".into())).unwrap();
        let value = overrides.pointer("/build/frontendDist").or_else(|| base.pointer("/build/frontendDist")).expect("Missing frontendDist").clone();
        let directory = match serde_json::from_value::<tauri_utils::config::FrontendDist>(value).expect("Invalid frontendDist") {
            tauri_utils::config::FrontendDist::Directory(directory) => root.join(directory),
            _ => panic!("Release frontendDist must be a file directory, not a URL. Windows drive paths must be relative or namespaced."),
        };
        assert!(directory.join("index.html").is_file(), "Release frontend index.html is missing");
    }
    tauri_build::build()
}
