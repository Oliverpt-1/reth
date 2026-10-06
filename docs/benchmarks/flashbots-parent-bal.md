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

## Measurement scope

This workspace has approximately 30 GB available disk and 10 GB RAM. No synced
performance node or builder-submission corpus was supplied or configured. Local
controlled measurements cannot establish production validation performance.
Production-node acceptance remains open until a suitable node and workload exist.
