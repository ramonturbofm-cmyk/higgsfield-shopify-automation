// Energy Manager desktop app: a thin WebView2 shell. The bundled connect screen
// finds the EMS server (Raspberry Pi, or the server built into this installation)
// and then loads that server's own web interface, so every screen uses the real
// REST/WebSocket API. The built-in server runs as a separate background process
// (EnergyManagerService.exe) so the EMS keeps working when this window is closed.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::Command;

fn service_exe() -> Option<PathBuf> {
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let exe = dir.join("server").join(if cfg!(windows) { "EnergyManagerService.exe" } else { "EnergyManagerService" });
    exe.is_file().then_some(exe)
}

fn service_command(args: &[&str]) -> Result<Command, String> {
    let exe = service_exe().ok_or("de ingebouwde EMS-server is niet geïnstalleerd")?;
    let mut cmd = Command::new(exe);
    cmd.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
    Ok(cmd)
}

#[tauri::command]
fn local_server_available() -> bool {
    service_exe().is_some()
}

/// Start the built-in server in the background ("demo" or "production").
/// Returns immediately; the connect screen polls until the server answers.
#[tauri::command]
fn start_local_server(mode: String) -> Result<(), String> {
    let flag = if mode == "demo" { "--demo" } else { "--production" };
    service_command(&["--background", flag])?
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("starten mislukt: {e}"))
}

/// Stop the built-in server gracefully (devices are released first).
#[tauri::command]
async fn stop_local_server() -> Result<(), String> {
    let status = service_command(&["--stop"])?
        .status()
        .map_err(|e| format!("stoppen mislukt: {e}"))?;
    if status.success() { Ok(()) } else { Err("de server reageerde niet op stoppen".into()) }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![local_server_available, start_local_server, stop_local_server])
        .run(tauri::generate_context!())
        .expect("Energy Manager kon niet starten");
}
