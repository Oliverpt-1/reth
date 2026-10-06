# Flashbots lazy parent BAL experiment

Base: upstream main `8cd725d582628cfbe5c1903aa3f88eac6c3a7243` (2026-10-06),
330 commits ahead of the fork's main at task start.

## Test rounds

1. Baseline: `CARGO_BUILD_JOBS=4 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 cargo test -p reth-rpc --lib validation --no-default-features`.
   Build stopped before executing tests: libmdbx bindgen could not find `stdarg.h`.
   The workspace has libclang 19 but no clang resource headers. Resolving build prerequisites.

2. With GCC headers supplied via `BINDGEN_EXTRA_CLANG_ARGS`, native prerequisites built.
   Rust compilation found adapter `Debug` bounds and two test-only import/constant issues.
   No tests executed in this round. Implementation and regression suite checkpointed;
   resolving these errors before the next test round.

3. Targeted validation suite: **29 passed**, 0 failed (8.97 s test runtime).
   Includes 10 parent BAL regressions, an Amsterdam Ethereum block with two contract
   calls, identical receipts/rebuilt BAL/bundle state/MPT roots, and parent-hash
   cache isolation across a simulated reorg. Fixed adapter Debug and test imports.

4. Local node build: a custom EthApiBuilder exposed a primitives-type mismatch in
   the cache builder signature. Generalized it: the BAL cache representation is
   independent of the ETH API's block/receipt primitives. Added an ignored MDBX
   benchmark; node rebuild and benchmark compilation are next.

5. Node rebuild succeeded (`--no-default-features`, unoptimized debug profile).
   The first replay smoke run stopped on an optional-value CLI syntax issue; fixed
   `--rpc-cache.prewarm-bals=0`. The broader nextest run stopped compiling the new
   ignored benchmark on a StorageEntry import; corrected its crate path. Re-running.

6. Broader nextest run: **288 passed**, 1 skipped (the explicit MDBX benchmark),
   154.747 s. Scope: all reth-rpc and reth-node-core library tests. Generated CLI
   reference from the built binary, including the opt-in flag.
   Real V6 replay smoke: all 12 combinations (BAL on/off, cold/normal cache,
   0/25/100% storage overlap) validated successfully, including full state-root
   checks. Setup requires a 3-block validation window and fixed 30M builder gas
   target. Smoke timing is discarded because the broader suite ran concurrently.
   Corrected the metrics scraper for the `reth_` prefix before timed runs.
   Provider-only execution retains its existing worker; only BAL-enabled execution
   requires the additional blocking worker for the asynchronous shared cache.

7. Rechecked the provider-only worker-path refinement: **30 passed**, including
   the manual MDBX benchmark (10.39 s). Workspace nightly formatting check passed.
   Timing from this test-suite run is discarded because tests were parallel; rerun
   the benchmark in isolation, then build an optimized node for RPC measurements.

8. Real-node metrics smoke passed: provider slot reads per first submission were
   64/48/0 at 0/25/100% overlap, with 9 metadata reads in every case. All repeated
   submissions had zero EVM provider reads and zero BAL loads. No latency claims:
   release compilation ran concurrently. The added provider-error regression
   needed Reth's DBErrorMarker-compatible ProviderError; corrected it and expanded
   full EVM/root comparisons to missing BALs, cleared storage, complete accounts,
   and deleted accounts. The next regression run verifies these additions.

9. Expanded regression run identified two fixture errors: non-created code changes
   are served inline by revm's AccountInfo, not added to its hash cache; and the
   Amsterdam cleared-slot write requires more gas than the old test's 100K limit.
   Check inline changed code, check hash lookup after creation, and raise test gas
   to 500K. Compare full receipts/BAL/bundles/roots across five parent-state cases.
   Avoid cloning the configured disallow set for each worker; share its API Arc.

10. Extended parent BAL suite: **13 passed**, 0 failed (2.174 s, nextest).
    Five full Ethereum execution comparisons passed: partial/code writes,
    unavailable BAL, cleared storage, complete account fields, and deletion. Each
    compares receipts, rebuilt child BAL, full bundle state, and a real MPT root.
    All eight partial account field combinations and fallback errors passed too.

11. Optimized node build succeeded (23m 54s; Rust 1.99.0, no default features,
    LTO off, 16 codegen units, debug info off). Full nightly all-feature workspace
    clippy stopped in GMP configuration because `m4` is missing. No lint result
    claimed; resolving prerequisites separately from timed measurements.

12. Optimized real-node run: **3,960 V6 validations passed**, across four nodes
    and all 12 cache/overlap combinations. First-submission read-count assertions
    and zero-load/zero-provider-read assertions on every repeat passed. No other
    build/test job ran during these timings. Results and raw first-submission
    measurements are committed alongside this report.

13. Optimized isolated MDBX benchmark: **1 passed**. 16 accounts × 64 slots,
    30 samples per overlap/mode, real shared ETH BAL service and MDBX provider.
    Read savings verified; results below. Moved test imports into their module
    scope to follow repository style.

14. Refined scheduling path: optimized validation suite **32 passed**, 1 ignored
    (manual benchmark), 0 failed (6.43 s). Includes the resolved-view cache test
    and all prior root/state comparisons. Rebuilding the node for a second timed
    RPC matrix; retrying nightly workspace lint with `m4` available.

## Design

Opt in with `--rpc.flashbots-parent-bal`. Validation reads in this order:
child mutable revm State → per-parent CachedReads → read-only parent BAL adapter
→ state provider at the exact parent hash. The adapter uses the final write of
individual fields/slots (including post-execution), rather than indexed reads.
Read-only slots and omitted fields retain provider fallback. Account existence
comes from the provider unless every standard field is available and nonempty;
account-ext builds always load provider metadata. Changed code is supplied inline.
Code accessed only by hash falls back to the provider instead of scanning the BAL.

A shared OnceLock memoizes one BAL load or absence for the current parent and is
reused with its read cache. It invokes the existing ETH cache only on a read miss,
skips pre-Amsterdam parents, and verifies the cached BAL commitment against the
parent header. Parent switches/reorgs use fresh read/BAL views. A successful BAL
load pins one shared decoded Arc alongside the current-parent read cache; in-flight
requests can retain earlier parents until they finish. This pin can outlive ETH
LRU eviction, including when that LRU is disabled. No decoded BAL is copied.

The ETH cache is asynchronous, so BAL-enabled execution uses a blocking worker;
provider-only/pre-Amsterdam execution keeps its original worker path. Read metrics
are aggregated locally and published once per execution, avoiding metric-handle
lookup on every EVM read. `builder.validation.parent_bal.load_seconds` measures
shared-cache retrieval, including fetch/decode on a miss. Read counters cover EVM
provider calls; state-root/proof provider work is outside these counters.

## Reproduce

```sh
cargo build --release -p reth --bin reth --no-default-features
python -m venv /tmp/reth-bal-bench-env
/tmp/reth-bal-bench-env/bin/pip install eth-account==0.14.0
/tmp/reth-bal-bench-env/bin/python scripts/bench_flashbots_parent_bal.py \
  --binary target/release/reth --output-dir /tmp/reth-bal-bench-results \
  --samples 30 --repeats 10 --slots 64
```

The runner starts four sequential isolated Amsterdam dev nodes (BAL on/off,
ETH BAL LRU disabled/normal). Each sample mines a parent and a child with controlled
0/25/100% storage overlap, validates V6 once and then ten times against the same
parent, and records latency, EVM provider counts, BAL load cost, parent serialized
size, and process RSS. Every validation checks consensus outputs and the expected
state root. Assertions require the predicted slot-hit/fallback counts and zero
BAL loads/provider reads on repeats. Serialized parent BAL size is inspected after
the timed intervals. Full raw measurements and node logs are retained.

Run the additional 16-account × 64-slot MDBX layer benchmark separately:

```sh
cargo test --release -p reth-rpc --lib benchmark_mdbx_parent_bal -- \
  --ignored --nocapture --test-threads=1
```

Do not run builds, tests, or other workloads concurrently with timed benchmarks.
For a representative node, use a funded test infrastructure account or provision
hardware separately, then replay real builder submissions over BAL-enabled blocks.
The same toggle and metrics allow a paired comparison; never use the runner's
public development key on another network.

## Measurement scope

This workspace has a 2-core CPU quota, an 8 GiB memory limit, and a 32 GB filesystem. No synced
performance node or builder-submission corpus was supplied or configured. Local
controlled measurements cannot establish production validation performance.
Production-node acceptance remains open until a suitable node and workload exist.

## Optimized local-node results

30 first submissions and 300 repeats per row. Medians in milliseconds;
first p95 is in parentheses. Cold means the ETH BAL LRU is disabled; normal
uses its default capacity with prewarming disabled in both cases. The local
node creates the parent itself, so normal-cache results are mostly cache hits.
These are sequential runs, not randomized paired production trials.

| BAL cache | Slot overlap | First off → on (p95) | Repeat off → on | Provider slots off → on | BAL load on | RSS off → on (MiB) |
|---|---:|---|---|---|---:|---|
| cold | 0% | 2.269 → 2.250 (2.842 → 2.730) | 1.047 → 0.970 | 64 → 64 | 0.140 | 2451.9 → 2441.7 |
| cold | 25% | 2.222 → 2.520 (2.405 → 2.893) | 1.023 → 1.042 | 64 → 48 | 0.168 | 2480.5 → 2485.9 |
| cold | 100% | 2.263 → 2.536 (2.809 → 4.871) | 1.051 → 1.099 | 64 → 0 | 0.170 | 2489.5 → 2489.8 |
| normal | 0% | 2.033 → 2.361 (2.672 → 3.437) | 0.954 → 1.011 | 64 → 64 | 0.043 | 2437.5 → 2440.7 |
| normal | 25% | 2.298 → 2.659 (3.092 → 5.109) | 1.038 → 1.121 | 64 → 48 | 0.072 | 2480.4 → 2484.3 |
| normal | 100% | 2.236 → 2.727 (2.574 → 3.774) | 1.061 → 1.141 | 64 → 0 | 0.069 | 2486.7 → 2492.2 |

Every first validation made 9 provider account-metadata reads and 1 code read in
both modes; this BAL records partial accounts. Every repeated validation made
zero EVM provider reads and zero BAL loads. Serialized parent BALs were 660–677
bytes. Cold fetch/decode/cache service cost was 0.140–0.170 ms median; normal-cache
retrieval was 0.043–0.072 ms. These are combined costs, not isolated decode costs.
RSS is whole-node resident memory, strongly affected by allocator/database/cache
history; these differences do not isolate the incremental decoded BAL allocation.

For this small, warm workload, eliminating up to 64 provider slot calls did **not**
improve end-to-end latency. With the normal cache, first medians were 16–22% higher;
repeats were 6–8% higher despite no BAL reloads. The extra worker scheduling remains
on BAL-enabled repeats, and can outweigh cheap MDBX reads. Cold results vary from
approximately equal at 0% overlap to 12–14% slower at 25/100%. This supports an
experimental opt-in default, rather than enabling the optimization globally.

The binary embeds commit `de9caa3b8` from build metadata generated earlier in the
build; use the SHA-256 and build options in `flashbots-parent-bal-results.json` to
identify the measured artifact. Later commits include test/documentation changes
and sharing the existing validation Arc instead of copying its disallow set.
Full raw first latencies, loads, counts, sizes, and RSS are in
`flashbots-parent-bal-first.csv`; repeat distributions are in the results JSON.
Production performance acceptance remains open.

## Optimized MDBX layer results

Warm OS page cache, 16 accounts × 64 slots, 30 samples per mode. First views fetch
and decode from the provider through the ETH service. No concurrent build/test
workloads; release profile as above. Times are medians in microseconds.

| Overlap | First off → on | Repeated off → on | Provider slots off → on | Decode only | Serialized bytes |
|---:|---|---|---|---:|---:|
| 0% | 301.505 → 655.118 | 42.394 → 42.664 | 1024 → 1024 | 93.361 | 6707 |
| 25% | 316.617 → 614.367 | 42.504 → 42.634 | 1024 → 768 | 93.071 | 6707 |
| 100% | 306.312 → 398.120 | 42.354 → 42.715 | 1024 → 0 | 93.381 | 6691 |

Account reads remain 16 in both modes. The 100% overlap still regresses first-view
latency by 30%; fetching/decoding a cold BAL costs more than these warm MDBX reads.
Decode includes RLP decode and conversion into revm BAL data. The cold shared-cache
benchmark includes additional service/channel/provider cost. Repeated cached reads
are approximately equal at this layer. This is a database-layer experiment, not
full RPC validation. Full stdout is in `flashbots-parent-bal-mdbx.txt`.
