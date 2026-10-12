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

41. New preload test compilation caught a TransactionId constructor mismatch in
    its child-write fixture. Corrected it. Added separate shared-cache fetch/decode
    timers and explicit builder-mode reset before the next regression run.

42. Targeted nextest: **49 passed**, 0 failed, 318 skipped (24.095 s), including
    the three eager preload/absence/commit regressions, mode parsing, existing
    lifecycle/root comparisons and shared-cache budget/concurrency checks.

43. Affected-package clippy passed with warnings denied (1m 28s), including
    Ethereum add-on wiring, RPC/core and ETH-cache libraries/tests. No eager-only
    worker hop is required. Beginning the broader library suite before timing.

44. Broader RPC/core/ETH-cache library nextest passed: **366 passed**, 0 failed,
    one intentionally skipped manual MDBX benchmark (213.125 s). Beginning the
    optimized build; no build/test work will run during timed node comparisons.

## Planned node comparison

Same optimized artifact in both modes. Each run compares one BAL strategy against
provider-only validation using identical submissions, randomized pair order,
independently verified sibling roots and changing transaction fees. Warmup, block
import, proof construction, metrics, RSS and analysis are outside timing. Root
result reuse stays disabled. Repeat-provider calls must be zero in all modes;
repeat BAL acquisitions must be zero for lazy/eager-parent and one per request
for eager-submission. Repeat each normal-cache strategy with reversed node roles
and a different seed, plus disabled-decoded-LRU controls to expose fetch/decode.

45. Optimized build succeeded (11m 24s); CLI reference regenerated. Eager-parent
    actual-node smoke passed **64 timed valid submissions** and **32 matching
    invalid rejections** across 64/512 slots and 0/100% overlap. Counter assertions
    verify one BAL acquisition per parent, zero repeat acquisitions and the
    expected hit/fallback counts. No smoke performance claim is made.

46. Eager-submission node smoke with the decoded BAL LRU disabled passed
    **64 timed valid submissions** and **32 matching invalid rejections**. Every
    repeat acquired BAL again while EVM provider reads stayed zero. Separate
    fetch/decode measurements are present; no smoke latency claim is made.

47. Lazy shared-ETH-cache node smoke passed **64 timed valid submissions** and
    **32 matching invalid rejections**, with the same workloads as eager modes.
    Expected covered-slot hits and zero repeat acquisitions/read calls all match.
    Beginning normal-cache paired timing at 64/512 slots, 0/100% overlap, 100
    parents per case and five changing-fee siblings; role reversal follows.

48. Eager-parent primary paired run passed **4,800 timed valid submissions**
    and **1,600 matching invalid rejections**. All four first-request latency
    intervals include zero, including +3.42% [-1.08%, +8.49%] at 512 slots/full
    overlap. Covered slot calls drop 512 → 0; repeat BAL acquisitions and EVM
    provider calls are zero. Repeated 512/full-overlap change is -2.77%, but
    requires reversed-role verification before attributing it to the strategy.

49. Reversed eager-parent run passed **4,800 timed valid submissions** and
    **1,600 matching invalid rejections**. All first-request intervals again
    include zero. At 512/full overlap: first +0.88% [-4.24%, +4.94%], repeats
    -0.55% [-4.86%, +2.67%]. The primary repeat improvement does not reproduce at
    full overlap; no reliable upfront-per-parent latency win is established.
