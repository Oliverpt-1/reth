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

## Measurement scope

This workspace has approximately 30 GB available disk and 10 GB RAM. No synced
performance node or builder-submission corpus was supplied or configured. Local
controlled measurements cannot establish production validation performance.
Production-node acceptance remains open until a suitable node and workload exist.
