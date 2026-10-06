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
