#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{io::{BufRead, BufReader, Write}, process::{Child, ChildStdin, Command, Stdio}, sync::{mpsc, Mutex}, time::{Duration, Instant}};
use serde_json::{json, Value};
use tauri::Manager;

const ACTIONS: &[&str] = &["dashboard", "save_agent", "probe_agent", "budget_create", "budget_status", "create_task", "proposal", "approve_start", "run_detail", "review", "resume", "cancel", "integrate"];
struct Engine { child: Child, stdin: Option<ChildStdin>, replies: mpsc::Receiver<Result<String, String>>, next: u64, uncertain: bool }
impl Engine {
    fn start(app: &tauri::AppHandle) -> Result<Self,String> {
        let root = app.path().app_local_data_dir().map_err(|e|e.to_string())?.join("state");
        std::fs::create_dir_all(&root).map_err(|e|e.to_string())?;
        let mut cmd;
        if cfg!(debug_assertions) {
            let python = std::env::var_os("AGENT_HELM_PYTHON").ok_or("Use npm run dev with AGENT_HELM_PYTHON set")?;
            let source = std::env::var_os("AGENT_HELM_SOURCE_ROOT").ok_or("Missing development source root")?;
            cmd = Command::new(python);
            cmd.env("PYTHONPATH", std::path::Path::new(&source).join("src")).env("PYTHONDONTWRITEBYTECODE", "1").args(["-m", "agent_orch"]);
        } else {
            let resource = app.path().resource_dir().map_err(|e|e.to_string())?;
            cmd = Command::new(resource.join("engine").join(if cfg!(windows) {"agent-helm.exe"} else {"agent-helm"}));
        }
        cmd.arg("--state-root").arg(&root).arg("desktop").stdin(Stdio::piped()).stdout(Stdio::piped());
        let log = std::fs::OpenOptions::new().create(true).append(true).open(root.join("desktop-engine.log")).map_err(|e|e.to_string())?;
        cmd.stderr(Stdio::from(log));
        #[cfg(windows)] { use std::os::windows::process::CommandExt; cmd.creation_flags(0x08000000); }
        let mut child = cmd.spawn().map_err(|_|"Unable to start desktop engine; check local Python or installed engine resources")?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take().ok_or("Missing engine stdout")?;
        let (tx,replies) = mpsc::channel();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut line = Vec::new();
                // Bound each reply without trusting the child to emit a newline.
                let result = std::io::Read::take(&mut reader, 4 * 1024 * 1024 + 1).read_until(b'\n', &mut line);
                match result {
                    Ok(0) => break,
                    Ok(_) if line.len() <= 4 * 1024 * 1024 => { if tx.send(String::from_utf8(line).map_err(|_|"Invalid engine UTF-8".into())).is_err() {break;} }
                    _ => { let _ = tx.send(Err("Engine reply exceeds transport limit".into())); break; }
                }
            }
        });
        Ok(Self {child,stdin,replies,next:0,uncertain:false})
    }
    fn request(&mut self, action:String, payload:Value) -> Result<Value,String> {
        if self.uncertain {return Err("Engine response uncertain; reconcile local tasks before restarting desktop. No automatic retry.".into());}
        self.next += 1;
        let line = serde_json::to_vec(&json!({"id":self.next,"action":action,"payload":payload})).map_err(|e|e.to_string())?;
        if line.len()>1024*1024 {return Err("Request too large".into());}
        // Any I/O failure after dispatch is uncertain and must never be retried automatically.
        self.uncertain = true;
        let stdin = self.stdin.as_mut().ok_or("Engine closed")?;
        stdin.write_all(&line).and_then(|_|stdin.write_all(b"\n")).and_then(|_|stdin.flush()).map_err(|_|"Engine transport failed; reconcile before retry")?;
        let response = self.replies.recv_timeout(Duration::from_secs(180)).map_err(|_|"Engine response lost or timed out; reconcile before retry")??;
        let response:Value = serde_json::from_str(&response).map_err(|_|"Invalid engine reply")?;
        if response["id"].as_u64()!=Some(self.next) {return Err("Engine correlation mismatch; reconcile before retry".into());}
        self.uncertain = false;
        if let Some(error)=response.get("error") {return Err(error.as_str().unwrap_or("Engine request failed").into());}
        response.get("result").cloned().ok_or("Missing engine result".into())
    }
}
impl Drop for Engine {
    fn drop(&mut self) {
        self.stdin.take();
        let until = Instant::now()+Duration::from_secs(8);
        while Instant::now()<until {
            if !matches!(self.child.try_wait(),Ok(None)) {return;}
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill(); let _ = self.child.wait();
    }
}
#[derive(Default)]
struct Backend(Mutex<Option<Engine>>);
#[tauri::command]
async fn desktop_request(app:tauri::AppHandle, action:String, payload:Value) -> Result<Value,String> {
    if !ACTIONS.contains(&action.as_str()) {return Err("Unknown desktop action".into());}
    tauri::async_runtime::spawn_blocking(move || {
        let state=app.state::<Backend>();
        let mut guard=state.0.lock().map_err(|_|"Desktop engine unavailable; reconcile before restarting")?;
        if guard.is_none() {*guard=Some(Engine::start(&app)?);}
        guard.as_mut().unwrap().request(action,payload)
    }).await.map_err(|_|"Desktop command failed".to_string())?
}
fn main() {
    let context = tauri::generate_context!();
    // Read-only package diagnostic: no window, backend process, state or model call.
    if std::env::args().nth(1).as_deref() == Some("--check-embedded-assets") {
        let index = context.assets().get(&"index.html".into());
        let html = index.as_ref().map(|bytes| String::from_utf8_lossy(bytes));
        let mut checked = vec!["index.html".to_string()];
        let valid = html.as_ref().is_some_and(|html| {
            let links: Vec<_> = html.split('"').filter(|part| part.starts_with("/assets/")).collect();
            !links.is_empty() && links.into_iter().all(|path| {
                checked.push(path.to_string());
                context.assets().get(&path.into()).is_some()
            })
        });
        println!("{}", json!({"embedded_assets_ok":valid,"checked":checked,"window_opened":false,"provider_calls":0}));
        std::process::exit(if valid {0} else {2});
    }
    tauri::Builder::default().manage(Backend::default()).invoke_handler(tauri::generate_handler![desktop_request])
        .build(context).expect("Unable to initialize Agent Helm desktop")
        .run(|app,event| if let tauri::RunEvent::Exit = event {if let Ok(mut state)=app.state::<Backend>().0.lock(){state.take();}});
}
