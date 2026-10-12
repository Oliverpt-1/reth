# Eager versus lazy parent BAL acquisition

This follow-up directly tests obtaining the parent BAL before execution, using
Matt's proposed shared `EthStateCache` and the same read-only database adapter.
The experimental root-result cache stays disabled in every measurement.

Modes:

- Provider-only: ordinary CachedReads and state provider; no parent BAL query.
- Lazy: request the shared ETH cache on the first underlying read-cache miss;
  memoize the BAL or absence once per parent.
- Eager per parent: asynchronously request the shared ETH cache before execution;
  memoize the committed BAL or absence, including concurrent requests per parent.
- Eager per submission: asynchronously request the shared ETH cache before every
  submission, even when all EVM reads are already cached. The ETH LRU can still
  satisfy each request without another provider fetch/decode.

The eager path awaits acquisition before synchronous execution, so it does not
artificially add the lazy synchronous loader's extra blocking-worker hop. Both
paths retain final post-execution values, complete metadata fallback, commitment
checks and child-write precedence. Pre-Amsterdam parents skip acquisition.

## Test ledger

40. Existing targeted RPC/core checks passed: **43 passed**, 0 failed (37.429 s).
    Eager mode wiring and CLI opt-in/exclusion parse tests compile. Added three
    asynchronous preload regressions afterward; these are next to execute.

Results pending optimized node testing. These are synthetic local-node workloads;
production replay and full default/JIT feature validation remain unverified.
