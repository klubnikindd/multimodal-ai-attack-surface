//! Tauri 2 shell: sidecar lifecycle (ТЗ v3.1 §11.2, P1-07).
//!
//! Спавнит kadr-engine-<target-triple>[.exe], читает JSON-handshake из stdout
//! (port/token/pid/ready), отдаёт UI команду engine_status и на выходе завершает
//! дерево процессов через единый app_shutdown().

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{Manager, RunEvent, State};

#[cfg(windows)]
mod job_object;

/// Управляемое состояние: процесс sidecar + креды handshake.
#[derive(Default)]
pub struct EngineState {
    child: Mutex<Option<Child>>,
    base_url: Mutex<Option<String>>,
    token: Mutex<Option<String>>,
    csrf_token: Mutex<Option<String>>,
    #[cfg(windows)]
    job: Mutex<Option<job_object::JobObject>>,
}

#[derive(Deserialize)]
struct Handshake {
    port: u16,
    token: String,
    #[serde(default)]
    csrf_token: Option<String>,
    #[serde(default)]
    ready: bool,
}

#[derive(Serialize, Clone)]
struct EngineStatus {
    running: bool,
    base_url: Option<String>,
}

/// Rust target-triple (аналог kadr.lifecycle.target_triple на Python).
fn target_triple() -> String {
    let (arch, os) = (std::env::consts::ARCH, std::env::consts::OS);
    match os {
        "windows" => format!("{arch}-pc-windows-msvc"),
        "macos" => format!("{arch}-apple-darwin"),
        "linux" => format!("{arch}-unknown-linux-gnu"),
        other => format!("{arch}-unknown-{other}"),
    }
}

fn sidecar_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let exe = format!(
        "kadr-engine-{}{}",
        target_triple(),
        if cfg!(windows) { ".exe" } else { "" }
    );
    let mut candidates: Vec<PathBuf> = Vec::new();
    // release: ресурсный каталог бандла (binaries/ рядом с ресурсами).
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("binaries").join(&exe));
    }
    // dev: target/debug/kadr.exe → вверх 3 уровня → src-tauri/binaries/.
    if let Ok(cur) = std::env::current_exe() {
        if let Some(dev) = cur
            .parent()
            .and_then(|d| d.parent())
            .and_then(|d| d.parent())
        {
            candidates.push(dev.join("binaries").join(&exe));
        }
    }
    candidates
        .into_iter()
        .find(|c| c.exists())
        .ok_or_else(|| format!("sidecar binary not found: {exe}"))
}

/// Спавнит engine и блокируется до JSON-handshake в stdout.
fn spawn_engine(app: &tauri::AppHandle, state: &State<EngineState>) -> Result<(), String> {
    let exe = sidecar_path(app)?;
    let mut child = Command::new(&exe)
        .args(["serve", "--sidecar"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("spawn {}: {}", exe.display(), e))?;

    #[cfg(windows)]
    {
        let job = job_object::JobObject::new_kill_on_close()?;
        job.assign(child.id())?;
        *state.job.lock().unwrap() = Some(job);
    }

    let stdout = child.stdout.take().ok_or("sidecar stdout unavailable")?;
    let reader = BufReader::new(stdout);
    let mut creds: Option<(String, String, Option<String>)> = None;
    for line in reader.lines() {
        let line = line.map_err(|e| e.to_string())?;
        let t = line.trim();
        if t.is_empty() {
            continue;
        }
        if let Ok(h) = serde_json::from_str::<Handshake>(t) {
            if h.ready && h.port > 0 && !h.token.is_empty() {
                creds = Some((format!("http://127.0.0.1:{}", h.port), h.token, h.csrf_token));
                break;
            }
        }
    }
    *state.child.lock().unwrap() = Some(child);
    let (url, token, csrf) = creds.ok_or("sidecar handshake timeout")?;
    *state.base_url.lock().unwrap() = Some(url.clone());
    *state.token.lock().unwrap() = Some(token.clone());
    *state.csrf_token.lock().unwrap() = csrf;
    eprintln!("[kadr] engine ready at {url}");
    Ok(())
}

/// Единый путь выхода (§11.2.3): POST /internal/shutdown → grace ≤2 с → kill.
fn app_shutdown(state: &State<EngineState>) {
    let url = state.base_url.lock().unwrap().clone();
    let token = state.token.lock().unwrap().clone();
    let csrf = state.csrf_token.lock().unwrap().clone();
    if let (Some(url), Some(token)) = (url, token) {
        let csrf = csrf.unwrap_or_else(|| token.clone());
        let r = ureq::post(&format!("{url}/internal/shutdown"))
            .set("Authorization", &format!("Bearer {token}"))
            .set("X-Kadr-CSRF", &csrf)
            .send_string("{}");
        eprintln!("[kadr] shutdown POST -> {:?}", r.map(|resp| resp.status()));
    } else {
        eprintln!("[kadr] shutdown: no url/token");
    }
    if let Some(mut child) = state.child.lock().unwrap().take() {
        for _ in 0..20 {
            if child.try_wait().ok().flatten().is_some() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[tauri::command]
fn engine_status(state: State<EngineState>) -> EngineStatus {
    let running = state.child.lock().unwrap().is_some();
    let base_url = state.base_url.lock().unwrap().clone();
    EngineStatus { running, base_url }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![engine_status])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = handle.state::<EngineState>();
                if let Err(e) = spawn_engine(&handle, &state) {
                    eprintln!("engine spawn failed: {e}");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                app_shutdown(&app_handle.state::<EngineState>());
            }
            _ => {}
        });
}
