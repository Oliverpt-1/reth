# Parent BAL follow-up: cache-only access and stage profiling

Previous experiment: [fetch/decode results](flashbots-parent-bal.md).
Work continues on `perf/flashbots-lazy-parent-bal`.

## Design under test

The node's opt-in flag now uses `BalProvider::cached_revm_bal`, a synchronous,
cache-only lookup. BlockchainProvider clones the decoded Arc retained with an
executed canonical parent at the exact hash. It never fetches or decodes on a
miss; missing, persisted, and untracked noncanonical parents use ordinary state
reads. Existing commitment checks and the inner read-only adapter remain.
Providers can omit this optional hook. The older explicit ETH-cache builder
remains available, but the node uses the cache-only builder with no extra worker.

Stage histograms measure payload recovery, pre-execution checks, provider creation,
read-cache cloning, executor setup/execution, child BAL hashing, cache merging,
post-execution checks, and state-root calculation. Both comparison modes have the
same instrumentation. These timings do not yet establish the dominant cost.

The next benchmark mirrors blocks through Engine API into a second node, validates
identical submissions on both, and randomizes which node is timed first. Reversing
producer/follower roles will check whether an apparent win depends on node roles.

## Test rounds

21. Targeted nextest: **34 passed**, 0 failed, 372 skipped (16.590 s).
    Scope: RPC validation plus provider decoded-handle and storage API regressions.
    Covers exact-hash Arc reuse, reorg eviction, missing decoded BAL, and all prior
    partial-field/code/storage/child-write/state-root cases. Release build underway.

22. Paired Engine API smoke passed on the previous binary: identical blocks were
    imported and made canonical on a second, non-dev node, then identical V6
    submissions validated on both. Counts matched. Timings are discarded because
    release compilation was active; the harness is ready for optimized runs.

23. Five affected-package clippy checks found one redundant clone in the new
    provider test. Removed it and rerunning with warnings denied. Runtime is
    unaffected; full-workspace JIT lint still requires unavailable LLVM 22.

24. Warning-free affected-package clippy passed (20.97 s). Optimized build
    succeeded (7m 23s). Cache-only actual-node smoke verified handle availability,
    exact root validation, and predicted hit/fallback counts at 64 and 512 slots.
    Smoke sample count is too small for performance conclusions.

25. Full paired direct-BAL run: **7,200 timed V6 validations passed**, 100 parents
    per 64/512-slot × 0/25/100% overlap case, five repeats each. Nodes imported
    identical blocks and validated identical submissions in randomized order.
    Warmup validations prime code/metric handles; two idle CPU intervals after
    imports exclude background import/trie work. Bootstrap intervals resample
    whole parent pairs, not correlated repeat requests. No concurrent builds/tests.
    Direct BAL lookup reduced acquisition to roughly 9–18 µs, but failed to show
    a reliable first-submission latency improvement. At 512 slots, first paired
    median changes were +3.7%, +4.1%, +3.2%; 95% intervals include regressions.

## Measured target

At 512 slots / 100% overlap, provider-only median stage times (µs):

| Stage | First | Repeated |
|---|---:|---:|
| EVM setup/execution | 1846 | 475 |
| State-root hashing/calculation | 1651 | 1649 |
| Read-cache copying | 4.9 | 6.9 |
| Cache merging | 2.6 | 3.0 |
| Rebuilt child BAL hashing | 64 | 67 |

At 64 slots, repeated root calculation is approximately 469 µs versus 112 µs
execution. Reusing root results for exact parent + exact hashed execution changes
is the next experiment. This will still execute every submission and check its
consensus outputs, proposer payment, rebuilt BAL, and expected root.

Artifacts: `flashbots-parent-bal-direct-results.json` and
`flashbots-parent-bal-direct-pairs.csv`. Cache-only BAL is not proven beneficial.


26. Bounded state-root reuse implementation and regressions added. Initial
    compilation caught test/helper import and error-construction mistakes; no
    runtime or performance result is claimed. Correcting these before execution.

27. Helper import/error-construction fixes compiled; the added API regression
    exposed one missing test-module ConsensusError import. Fixing and rerunning.

28. Targeted nextest: **43 passed**, 0 failed, 524 skipped (16.620 s).
    Includes full API validation on a root hit rejecting a false claimed root and
    an unpaid bid; complete-state MPT changes, parent/reorg isolation, concurrency,
    failed-provider handling, and capacity-based retention limits all pass.

29. Scoped warnings-denied clippy found one clone-on-copy in the map-order test.
    Correcting it in a way that also supports non-Copy account-extension builds.

30. Added retained map-capacity payload telemetry for the memory comparison.
    Clippy requested copied rather than cloned for standard accounts; using owned
    map removal in the test avoids conditional Copy/Clone assumptions entirely.

31. Affected-package clippy passed with warnings denied (31.02 s): RPC, node
    core, Ethereum node, storage API, and provider, including tests. Retained
    payload telemetry is an estimate of map key/value capacity; process RSS is
    measured separately and includes all node/database allocations.
