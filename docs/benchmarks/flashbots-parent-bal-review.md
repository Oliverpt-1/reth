# Parent BAL experiment — brief review

**Result:** parent BAL replaced covered EVM provider reads while preserving the
tested results. A consistent validation latency improvement was not demonstrated.

## What we tried

Compared provider-only validation with three opt-in strategies, all using the
shared ETH BAL cache and a read-only adapter below CachedReads and child state:

- **Lazy:** acquire BAL on the first underlying read-cache miss, once per parent.
- **Upfront per parent:** acquire before execution, retaining the view for repeats.
- **Upfront every submission:** acquire before every request, including repeats.

The parent BAL is never attached directly to the child's mutable State.

## Tests completed

- **Execution/root comparison:** enabled versus disabled execution compared
  results, receipts, rebuilt BALs, state changes and independently calculated
  Ethereum trie roots. Fixtures cover missing BALs, partial/code fields, cleared
  slots and account lifecycle cases.
- **Hit/fallback counters:** covered writes avoid provider slot calls; absent
  fields, read-only slots, missing BALs and provider errors preserve fallback.
- **Child-state precedence:** child writes and storage clears override parent BAL
  values; eager-prefetched values also cannot override committed child state.
- **Parent/cache safety:** commitment mismatch, pre-Amsterdam fallback, concurrent
  acquisition deduplication, repeated CachedReads hits, and exact-hash reads from
  distinct/noncanonical parents. The latter is an adapter test, not live reorg replay.
- **Checks:** 366 broader library tests passed; 49 targeted tests passed
  (overlapping counts). Scoped no-default-feature clippy and formatting passed.
- **Actual local-node comparison:** nine timing runs, 32,400 valid validations
  and 10,800 matching invalid rejections. Repeats use distinct executed balances
  and roots. Root-result caching was disabled.

## Findings

Normal decoded BAL cache, 512 slots, full parent-write/child-read overlap.
Positive percentages mean slower. First-request numbers are paired median
changes from two runs with node roles reversed.

| Strategy | First provider slot calls | First latency change | Repeated validation |
|---|---:|---:|---|
| Lazy | 512 → 0 | +2.19% / +3.72% | No replicated speedup |
| Upfront per parent | 512 → 0 | +3.42% / +0.88% | No replicated speedup |
| Upfront every submission | 512 → 0 | +3.20% / −0.32% | +3.77% / +1.17% slower |

- At zero overlap, provider slot calls remain unchanged. Partial account metadata
  still requires provider fallback.
- Repeats already make zero EVM provider calls through CachedReads. Loading BAL
  again therefore adds work without saving those calls.
- Cached BAL acquisition costs roughly 0.1 ms. Disabling the decoded BAL LRU
  exposes separate fetch/decode costs and makes every-submission repeats about
  15% slower at 64 slots and 10% slower at 512. Raw bytes/state remain warm.
- At 512 slots, first execution takes about 2 ms and root calculation about 4 ms
  in the primary baseline runs. BAL does not remove trie/root work.
- Measured node RSS and shared BAL-cache bytes; incremental memory held by
  validation's per-parent reference was not isolated.

## Remaining gaps

Live-node reorg validation, isolated incremental memory, production submission
replay, cold-storage behavior and full default/JIT-feature verification remain
open. These were synthetic Amsterdam workloads on two local RETH nodes, with a
two-core/8 GiB container. Keep the optimization experimental.

[Detailed results, confidence intervals and raw paired measurements](archive/flashbots-parent-bal-eager.md)
· [Regression tests](../../crates/rpc/rpc/src/validation/parent_bal.rs)
· [Node benchmark harness](../../scripts/bench_flashbots_parent_bal_paired.py)
