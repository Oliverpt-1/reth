# Parent BAL validation results

Parent BAL eliminated covered EVM provider reads and preserved correctness in
the tested workloads. **No consistent validation latency improvement was
demonstrated.** All performance results below use synthetic transactions on real
RETH nodes; historical Sepolia validation has not run.

## What we tested

Compared optimization disabled, lazy acquisition, upfront acquisition once per
parent, and upfront acquisition on every submission. The BAL adapter sits below
CachedReads and child state. State-root result caching was disabled.

- **Correctness:** EVM results, receipts, rebuilt BALs, state changes and calculated
  trie roots matched with optimization on and off. Fixtures covered partial
  account/code fields, missing BALs, deletion, storage clears and child writes.
- **Cache and fallback:** tested commitment mismatch, pre-Amsterdam fallback,
  provider errors, concurrent acquisition, repeated CachedReads hits and reads
  from distinct/noncanonical parents.
- **Node validation:** nine timing runs on a synthetic Amsterdam chain, using
  real MDBX providers and Flashbots RPC: 32,400 valid validations and 10,800
  matching invalid rejections for wrong roots/payments.
- **Workloads:** 0% and 100% parent-write/child-read overlap, first and repeated
  submissions, normal and disabled decoded BAL caches, and reversed node roles.
  Nodes ran in a two-core/8 GiB container.
- **Checks:** 366 broader library tests and 49 targeted tests passed (counts
  overlap); scoped no-default-feature clippy and formatting passed. The later
  Mac build passed all 16 focused parent-BAL tests.

## Performance results

512 storage slots, full overlap, normal decoded BAL cache. First-request changes
are paired medians from the primary and reversed-role runs; positive means slower.

| Strategy | First provider storage calls | First latency change, primary / reversed | Repeated validation |
|---|---:|---:|---|
| Lazy | 512 → 0 | +2.19% / +3.72% | No replicated speedup |
| Upfront per parent | 512 → 0 | +3.42% / +0.88% | No replicated speedup |
| Upfront every submission | 512 → 0 | +3.20% / −0.32% | +3.77% / +1.17% slower |

Lazy mode, primary run, separate medians:

| Metric | First request: off → lazy | Repeated request: off → lazy |
|---|---:|---:|
| Provider storage calls | 512 → 0 | 0 → 0 |
| Provider account calls | 9 → 9 | 0 → 0 |
| BAL acquisition | 0 → 117.2 µs | 0 → 0 µs |
| Raw BAL fetch / decode | 0 / 0 → 0 / 0 µs (decoded-cache hit) | 0 / 0 → 0 / 0 µs |
| Validation latency | 8.271 → 8.309 ms | 4.063 → 3.912 ms |

The apparent repeated-request improvement did not reproduce with statistical
confidence after role reversal. Repeats changed fees, balances and roots but
largely reused the same storage accesses; CachedReads already eliminated their
provider calls. At zero overlap, provider storage calls were unchanged.

With the decoded BAL cache disabled, first lazy acquisition took 452.4 µs,
including separately measured fetch/decode medians of 12.0/177.4 µs. Raw bytes
and state remained warm; this was not a cold-storage test. BAL does not remove
state-root computation.

Whole-node RSS was 2655.8 → 2611.6 MiB in the primary lazy run and
2606.9 → 2638.9 MiB with roles reversed. Shared BAL-cache bytes were unchanged
at 16,691,816. Incremental memory retained solely by the optimization was not
isolated.

[Full methodology, confidence intervals and results](archive/flashbots-parent-bal-eager.md)
· [Lazy results JSON](archive/flashbots-parent-bal-lazy-primary-results.json)
· [Per-pair measurements](archive/flashbots-parent-bal-lazy-primary-pairs.csv)
· [Regression tests](../../crates/rpc/rpc/src/validation/parent_bal.rs)
· [Benchmark harness](../../scripts/bench_flashbots_parent_bal_paired.py)

## Historical and protocol checks

- **Synthetic protocol probe:** V6 accepted an Amsterdam payload with 64 BAL
  storage hits and zero provider storage calls. V5 rejected it before execution.
  No public builder/test-relay benchmark ran.
  [Evidence](archive/flashbots-public-tooling-readiness.json).
- **Historical data verification:** fetched Sepolia block 11,930,000 and its
  complete execution payload envelope. Signed transaction hashes, transaction
  trie root, BAL commitment and beacon references matched. Transactions were
  not executed locally.
  [Sources and evidence](archive/flashbots-parent-bal-data-sources.md).
- **Historical validation:** not run. The verified snapshot is 699.5 GB
  compressed; the Mac has 556.3 GB free and no external drive. Expanded size,
  treatment-copy space and database compatibility remain unverified.
  [Storage record](archive/flashbots-parent-bal-storage-blocker.md).

## What remains open

Correctness and hit/fallback criteria passed for the fixtures and synthetic node
workloads. Latency and memory were measured, but no replicated speedup or isolated
incremental memory result was established.

Historical validation, different competing candidates per parent, live reorgs,
cold-storage behavior and full default/JIT-feature verification remain untested.
