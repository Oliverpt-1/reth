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

No latency improvement is claimed until optimized paired measurements pass.
