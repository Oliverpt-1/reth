# Validation stage I/O attribution

## Eight-parent result

The full round completed **384 successful diagnostic validations**: eight
parents, disabled/lazy, 100%/0% overlap, warm/cold state, both treatment orders,
and first/sibling/exact-repeat requests. Every computed root matched, BAL hits
and provider fallback matched expectations, decoded BALs were preloaded equally,
and the state-root result cache remained disabled.

For fully covered cold first requests, BAL moved database work between stages
instead of eliminating it:

| Stage | Disabled: read bytes / major faults / duration | Lazy: read bytes / major faults / duration |
|---|---:|---:|
| Execution | 393,216 / 96 / 8.257 ms | 8,192 / 2 / 0.796 ms |
| Intervening work | 0 / 0 / 0.104 ms | 0 / 0 / 0.111 ms |
| State-root calculation | 1,392,640 / 340 / 25.165 ms | 1,777,664 / 434 / 31.946 ms |
| Execution + intervening + root | 1,785,856 / 436 | 1,785,856 / 436 |

Values are medians of 16 observations (eight parents in each order). Disabled
made 64 provider storage calls; lazy made zero and recorded 64 BAL hits. Both
still made the same account/code calls. Median lazy BAL acquisition was 77.4 µs
cold and 73.8 µs warm; timed fetch/decode counts were zero.

The page evidence is exact for this generated workload. In every one of the 16
cold full-overlap pairs, the combined set of MDBX major-fault offsets across
execution and root calculation was identical between modes. **92–96 identical
4 KiB pages** faulted during disabled execution but during lazy root calculation.
There were no disabled-only or lazy-only major-fault offsets. Process read bytes,
the sum of per-thread read bytes, major-fault results, page-cache insertions and
guest-device read bytes agreed at each stage. No stage-attributed major faults
belonged to executables, shared libraries or another file.

The disabled execution worker read those pages first, warming them for the
subsequent root calculation. Lazy execution obtained the covered values from
the decoded parent BAL, so those pages stayed cold until the separate validation
thread calculated the state root. State-root calculation still needs the
affected trie/database pages. This explains why execution got faster while
whole-request disk bytes did not decrease.

The zero-overlap control supports that attribution. Disabled and lazy had the
same execution I/O median (399,360 bytes, 97.5 major faults) and root I/O median
(1,433,600 bytes, 350 faults); their page-offset sets and stage read-byte totals
matched in all 16 cold pairs. The additional-access sibling also matched between
modes: 268,288 bytes/65.5 faults in execution and 946,176 bytes/231 faults in
roots. Exact repeats had zero disk reads/faults. All warm stages had zero disk
reads/faults.

The order split did not change the conclusion. Full-overlap cold execution/root
duration medians were 8.457/22.824 ms then 8.242/25.630 ms for disabled, and
0.833/31.564 ms then 0.785/32.025 ms for lazy in forward/reversed order.
Instrumented durations are diagnostic: counter snapshots cost 0.300–1.396 ms
each and change scheduling. They are not replacements for the earlier latency
benchmark.

No request had unassigned read bytes or major faults outside preparation,
execution, intervening work and root calculation. Preparation and intervening
work recorded zero I/O. That rules out BAL acquisition, BAL hashing, cache
updates, payment checks and hashed-state construction as the source of the
shift in this run. It does not prove that every memory-resident page access was
observed: BPF records file faults and page-cache insertion, while `/proc` and
device counters record actual charged I/O. Page access, a page fault, cache
insertion/read-ahead and completed I/O remain distinct concepts.

The evidence supports **BAL defers actual guest database I/O from transaction
execution to state-root calculation** for covered cold reads. It does not support
the explanations that BAL eliminates total I/O or merely avoids calls that were
already memory hits under the verified cold condition. Warm controls do show
that those calls hit memory once the state is resident. The evidence cannot say
whether a different root algorithm, database layout, candidate distribution or
physical host SSD would behave the same.

Evidence: [raw node/BPF records](flashbots-parent-bal-io-results.json.gz) and
[parsed stage/offset attribution](flashbots-parent-bal-io-attribution.json.gz).
Recreate the parsed artifact with:

```sh
python3 scripts/summarize_parent_bal_io.py \
  docs/benchmarks/archive/flashbots-parent-bal-io-results.json.gz \
  docs/benchmarks/archive/flashbots-parent-bal-io-attribution.json.gz
```

## First diagnostic round

The one-parent round completed 48 successful validations: disabled/lazy,
100%/0% overlap, warm/cold, both orders, first/sibling/repeat. Computed roots,
BAL hits and provider fallback passed assertions. Diagnostics were verified
disabled by default. No validation used root-result caching or fetched/decoded
a parent BAL inside the diagnostic interval.

For the fully overlapping cold first request, both orders produced:

| Stage | Disabled read bytes / major faults | Lazy read bytes / major faults |
|---|---:|---:|
| Execution | 389,120 / 95 | 8,192 / 2 |
| Intervening work | 0 / 0 | 0 / 0 |
| Root calculation | 1,335,296 / 326 | 1,716,224 / 419 |

Kernel `filemap_fault` probes identified **93 identical MDBX offsets** faulted
in during disabled execution but during lazy root calculation. Their combined
major-fault offset sets were identical. This directly supports deferral for
this parent, not merely an inference from stage durations.

[Raw first-round evidence](flashbots-parent-bal-io-smoke.json.gz).
The eight-parent replication above is the final result.

## Instrumentation and reproducibility

Instrumented source: `46d3d31e690cf11bff8c9aeae43dd44f09059098`.
Binary SHA-256:
`fcaac881beef8f221f3d7c04c6daa3c44f6f729e0b80aec776113647e7a55552`.
The binary reports that revision, release profile and no default features.
Build command:

```sh
CARGO_BUILD_JOBS=4 CARGO_PROFILE_RELEASE_LTO=false \
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
cargo build --locked --release -p reth --bin reth --no-default-features
```

The existing Colima VM and `parent-bal-build` Docker volume were reused.
`parent-bal-io` has four CPUs, a 7 GiB container memory limit, privileged tracing
access and `--pid=host` inside the isolated VM so kernel/process thread IDs
agree. It is based on the earlier Debian diagnostic container with bpftrace
0.17.0 installed. Kernel: 6.8.0-117-generic, ARM64, 4 KiB pages. The host remains
an M4 Max Mac; guest I/O does not prove host SSD cache misses.

```sh
# Inside that isolated container; tracefs is mounted at /sys/kernel/tracing.
/work/venv/bin/python /src/scripts/diagnose_parent_bal_io.py \
  --binary /work/reth/target/release/reth --frozen /work/disk-full \
  --output /work/io-smoke --parents 1 --allow-isolated-vm-cache-drop
python3 scripts/summarize_parent_bal_io.py \
  docs/benchmarks/archive/flashbots-parent-bal-io-smoke.json.gz \
  /tmp/parent-bal-io-smoke-summary.json
```

The harness sets `RETH_FLASHBOTS_IO_DIAGNOSTIC=1` only for diagnostic processes.
The node samples process and per-thread `/proc` counters immediately around
`executor.execute_one` and `state_provider.state_root`. Execution includes
block system changes and on-demand BAL acquisition. Existing BAL-load metrics
measure acquisition separately; decoded BAL preload occurs before tracing and
validation and timed fetch/decode counters must remain zero.

The interval between execution and roots includes BAL reconstruction hashing,
state-bundle extraction, worker return, CachedReads updates, post-execution
consensus/payment checks and `hashed_post_state`. It is reported separately.
Preparation between validation entry and execution is also recorded. Root
measurement starts after hashed-state construction.

Each boundary stores wall-clock timestamps bracketing the counter collection.
Process counters include all worker threads. Per-thread snapshots and trace
TIDs distinguish the executing worker from other node threads. Snapshots are
not atomic across files; stage byte/fault deltas include the starting sampling
overhead. Reported diagnostic durations exclude the snapshot windows themselves.
Serialization/log output occurs only after root validation, outside both stages.
These intrusive timings do not replace the earlier latency benchmarks.

The tracer captures fault entry/return time, PID/TID, filesystem device, inode,
offset and Linux fault-result flags. `VM_FAULT_MAJOR` is flag `0x4`; it is not
equated with every page access. The separate page-cache insertion trace records
offset/order and can include read-ahead; insertion alone does not prove completed
I/O. Actual read bytes come from process accounting, supported by guest device
counters. The trace includes non-MDBX file faults so executable/library activity
can be distinguished by file identity. Executable and library files are warmed.
Only faults entirely inside a stage's timestamp window are attributed to it.

All treatments use sparse copies of the same frozen parent, identical saved
signed candidates and equally preloaded decoded BALs. No state is generated.
Logical offsets are compared across copies of that same MDBX file, not across
unrelated databases. Requests are serial and nodes are idle before validation;
background node threads are nevertheless included in process totals.

## Checks and setup issues

The committed code built successfully. `zepter run check`, `make lint-toml`
and Python compilation checks passed after installing the missing lint tools
under `/tmp/parent-bal-tools`. The first BPF probe failed on a nested BTF
`struct file` field lookup; an explicit `(struct file *)` cast resolved it.
Tracefs was mounted inside the dedicated VM container. Neither issue prevented
the completed measurements.
