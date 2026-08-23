use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;

use rusqlite::{Connection, OptionalExtension};
use tauri::Manager;

/// SQLite database under the app data directory holding the whole key-value map.
const STORE_FILE: &str = "store.sqlite3";

/// Key-value file written by builds before the SQLite store existed.
const LEGACY_STORE_FILE: &str = "store.json";
/// The legacy file is renamed here once imported, so the migration runs exactly
/// once and the original bytes survive for recovery by hand.
const MIGRATED_STORE_FILE: &str = "store.json.migrated";

/// A poisoned mutex means a panic mid-command; the React layer reads the `Err`
/// as "this till cannot persist anything" rather than as a silent success.
const STORE_UNUSABLE: &str = "Local store is unusable after an earlier failure";

/// Durable key-value store backing the desktop platform's local storage. Values
/// are plain strings; the React layer owns all serialization.
///
/// Two things the counter cannot afford to lose live in this store: the offline
/// sale queue, and the access token. Held only in memory, both died with the
/// process -- so closing the app after an offline shift discarded every sale
/// rung up during the outage the queue exists to survive, and a restart while
/// still offline locked staff out of the POS altogether, because signing back in
/// needs an OTP and an OTP needs connectivity.
struct Store {
    connection: Mutex<Connection>,
}

impl Store {
    /// Open (creating if necessary) the database and prepare its single table.
    ///
    /// WAL keeps readers and the writer from blocking each other, and
    /// `synchronous = FULL` keeps the old tmp-write+rename guarantee: a commit is
    /// on the device before the command reports success, so an acknowledged save
    /// survives a crash or a pulled power cable.
    fn open(path: PathBuf) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|cause| format!("Could not create the local store directory: {cause}"))?;
        }
        let connection = Connection::open(&path).map_err(|cause| format!("Could not open the local store database: {cause}"))?;
        connection
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 PRAGMA synchronous = FULL;
                 CREATE TABLE IF NOT EXISTS storage (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
            )
            .map_err(|cause| format!("Could not prepare the local store database: {cause}"))?;
        if let Some(directory) = path.parent() {
            migrate_legacy_store(&connection, directory);
        }
        Ok(Store {
            connection: Mutex::new(connection),
        })
    }
}

fn upsert(connection: &Connection, key: &str, value: &str) -> Result<(), String> {
    connection
        .execute(
            "INSERT INTO storage (key, value) VALUES (?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            rusqlite::params![key, value],
        )
        .map_err(|cause| cause.to_string())?;
    Ok(())
}

/// One-time import of the JSON key-value store this app wrote before SQLite.
///
/// Malformed legacy content never blocks startup: whatever parses as a map is
/// imported row by row (one bad row would only skip itself), and the original
/// file is renamed to `{MIGRATED_STORE_FILE}` either way so a half-readable blob
/// is preserved instead of being re-read -- or overwritten -- on every launch.
/// A failed rename leaves the file in place; the import upserts, so the next
/// start simply tries again without duplicating anything.
fn migrate_legacy_store(connection: &Connection, directory: &Path) {
    let legacy = directory.join(LEGACY_STORE_FILE);
    let raw = match fs::read_to_string(&legacy) {
        Ok(raw) => raw,
        Err(_) => return,
    };
    if let Ok(entries) = serde_json::from_str::<HashMap<String, String>>(&raw) {
        for (key, value) in entries {
            let _ = upsert(connection, &key, &value);
        }
    }
    let archived = directory.join(MIGRATED_STORE_FILE);
    let _ = fs::remove_file(&archived);
    let _ = fs::rename(&legacy, &archived);
}

/// Errors are returned rather than swallowed. A silent no-op here is
/// indistinguishable from a successful save to the React layer, which would
/// report a sale as queued when nothing had been stored.
#[tauri::command]
fn store_get(state: tauri::State<Store>, key: String) -> Result<Option<String>, String> {
    let connection = state.connection.lock().map_err(|_| STORE_UNUSABLE.to_string())?;
    connection
        .query_row("SELECT value FROM storage WHERE key = ?1", rusqlite::params![key], |row| row.get(0))
        .optional()
        .map_err(|cause| cause.to_string())
}

#[tauri::command]
fn store_set(state: tauri::State<Store>, key: String, value: String) -> Result<(), String> {
    let connection = state.connection.lock().map_err(|_| STORE_UNUSABLE.to_string())?;
    // SQLite commits durably before this returns; unlike the in-memory cache it
    // replaces there is no copy that outlives a failed write, and none is needed.
    upsert(&connection, &key, &value)
}

#[tauri::command]
fn store_remove(state: tauri::State<Store>, key: String) -> Result<(), String> {
    let connection = state.connection.lock().map_err(|_| STORE_UNUSABLE.to_string())?;
    connection
        .execute("DELETE FROM storage WHERE key = ?1", rusqlite::params![key])
        .map(|_| ())
        .map_err(|cause| cause.to_string())
}

/// Print receipt text through the OS print pipeline.
///
/// The text is staged in a temp file and handed to `lpr` (macOS/Linux) or
/// PowerShell's `Out-Printer` with the default printer (Windows), because those
/// are the pipelines a shop machine already has. `Ok` only when the pipeline
/// reported success; every other outcome comes back as an explicit `Err`, which
/// the UI surfaces without touching sale state.
#[tauri::command]
fn print_receipt(receipt: String) -> Result<(), String> {
    print_text(&receipt)
}

fn print_text(text: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        print_windows(text)
    }
    #[cfg(not(target_os = "windows"))]
    {
        print_unix(text)
    }
}

#[cfg(not(target_os = "windows"))]
fn print_unix(text: &str) -> Result<(), String> {
    let staged = temp_receipt_path();
    fs::write(&staged, text).map_err(|cause| format!("Could not stage the receipt for printing: {cause}"))?;
    let printed = Command::new("lpr").arg(&staged).output();
    let _ = fs::remove_file(&staged);
    match printed {
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Err(
            "No print pipeline found: the lpr command is not installed on this machine".to_string(),
        ),
        Err(cause) => Err(format!("Could not run lpr: {cause}")),
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(format!("lpr did not accept the receipt{}", status_suffix(&output.stderr))),
    }
}

#[cfg(target_os = "windows")]
fn print_windows(text: &str) -> Result<(), String> {
    use std::io::Write;
    use std::process::Stdio;

    let mut child = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", "$input | Out-Printer"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|cause| format!("Could not run PowerShell to print: {cause}"))?;
    // Piped in rather than quoted into the command line, so a quote character in
    // the receipt can never become PowerShell code.
    if let Some(stdin) = child.stdin.as_mut() {
        stdin
            .write_all(text.as_bytes())
            .map_err(|cause| format!("Could not hand the receipt to the printer: {cause}"))?;
    }
    let output = child
        .wait_with_output()
        .map_err(|cause| format!("Printing did not finish: {cause}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!("Out-Printer did not accept the receipt{}", status_suffix(&output.stderr)))
    }
}

fn temp_receipt_path() -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or(0);
    std::env::temp_dir().join(format!("pharmacy-receipt-{}-{nanos}.txt", std::process::id()))
}

fn status_suffix(stderr: &[u8]) -> String {
    let lossy = String::from_utf8_lossy(stderr);
    let trimmed = lossy.trim();
    if trimmed.is_empty() {
        ".".to_string()
    } else {
        format!(": {trimmed}")
    }
}

/// Hardware stubs kept deliberately honest (Stage 2 of the plan): a scanner's
/// keystrokes already reach the focused field through the keyboard path, and no
/// drawer is wired up. Neither pretends support; real drivers plug in behind
/// these same commands.
#[tauri::command]
fn scan() -> Option<String> {
    None
}

#[tauri::command]
fn open_cash_drawer() -> Result<(), String> {
    Err("Cash drawer not connected".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let directory = app.path().app_data_dir()?;
            app.manage(Store::open(directory.join(STORE_FILE))?);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            store_get,
            store_set,
            store_remove,
            print_receipt,
            scan,
            open_cash_drawer
        ])
        .run(tauri::generate_context!())
        .expect("error while running desktop application");
}
