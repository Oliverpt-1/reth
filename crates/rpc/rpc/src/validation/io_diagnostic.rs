//! Opt-in, intrusive Linux stage I/O diagnostics, not production latency metrics.
//!
//! Set RETH_FLASHBOTS_IO_DIAGNOSTIC=1 before starting the process. Snapshots are
//! buffered until root validation succeeds. Concurrent requests/background work
//! can contaminate process totals; use isolated, serial requests.

use alloy_primitives::B256;
use serde_json::{json, Value};
use std::{
    sync::{Arc, Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Default)]
pub(super) struct Diagnostic {
    snapshots: Mutex<Vec<Value>>,
}

impl Diagnostic {
    pub(super) fn new() -> Option<Arc<Self>> {
        static ENABLED: OnceLock<bool> = OnceLock::new();
        ENABLED
            .get_or_init(|| {
                cfg!(target_os = "linux")
                    && std::env::var("RETH_FLASHBOTS_IO_DIAGNOSTIC").as_deref() == Ok("1")
            })
            .then(|| Arc::new(Self::default()))
    }
}

pub(super) fn mark(diagnostic: &Option<Arc<Diagnostic>>, stage: &str) {
    if let Some(diagnostic) = diagnostic {
        let before = timestamp();
        let process_io = read("/proc/self/io");
        let process_stat = read("/proc/self/stat");
        let mut threads = Vec::new();
        if let Ok(entries) = reth_fs_util::read_dir("/proc/self/task") {
            for entry in entries.flatten() {
                let path = entry.path();
                threads.push(json!({
                    "tid": entry.file_name().to_string_lossy(),
                    "stat": read(path.join("stat")),
                    "io": read(path.join("io")),
                }));
            }
        }
        let disks = read("/proc/diskstats");
        let thread = reth_fs_util::read_link("/proc/thread-self")
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_else(|error| error.to_string());
        diagnostic.snapshots.lock().unwrap().push(json!({
            "stage": stage, "snapshot_start_unix_ns": before,
            "snapshot_end_unix_ns": timestamp(), "process_io": process_io,
            "process_stat": process_stat, "threads": threads, "disks": disks,
            "calling_thread": thread,
        }));
    }
}

pub(super) fn emit(diagnostic: &Option<Arc<Diagnostic>>, block: B256, root: B256) {
    if let Some(diagnostic) = diagnostic {
        // Serialization and output are outside both measured stage intervals.
        eprintln!(
            "BAL_IO_DIAGNOSTIC {}",
            json!({
                "block": block, "computed_root": root,
                "snapshots": *diagnostic.snapshots.lock().unwrap(),
            })
        );
    }
}

fn timestamp() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos() as u64
}

fn read(path: impl AsRef<std::path::Path>) -> String {
    reth_fs_util::read_to_string(path).unwrap_or_else(|error| format!("ERROR: {error}"))
}
