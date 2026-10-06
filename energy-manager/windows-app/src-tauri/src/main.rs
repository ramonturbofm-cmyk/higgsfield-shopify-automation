// Energy Manager desktop app: a thin WebView2 shell. The bundled connect screen
// finds the EMS server (Raspberry Pi or local server); the dashboard itself is
// served by that server, so every screen uses the real REST/WebSocket API.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("Energy Manager kon niet starten");
}
