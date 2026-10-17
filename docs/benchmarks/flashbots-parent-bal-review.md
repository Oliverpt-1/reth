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

## Acceptance criteria: evidence and verdict

| Criterion | What verifies it | Verdict |
|---|---|---|
| 1. Matching validation results and roots, including the listed edge cases | Enabled/off EVM execution compares results, receipts, rebuilt BALs, state changes and trie roots for controlled missing-BAL, partial/code-field and lifecycle/clear fixtures. Actual-node RPC runs accept the same valid roots and reject wrong roots/payments identically. Noncanonical-parent fixtures check exact-hash values. | Passed for the execution fixtures and node workloads. Reorg coverage is partial: exact-hash reads were checked, but full live-reorg validation/root comparisons remain open. |
| 2. BAL hits, provider fallback and no stale values after child writes | Counter assertions check hits/fallback. Child commits change balance, nonce, code and storage; subsequent reads see child values, child creation clears storage, and the next submission's CachedReads still contains parent values. | Passed in the regression tests. |
| 3. Before/after reads, BAL cost, memory and first/repeated latency | Metrics include provider-call counts, acquisition, separate raw fetch/decode timings, node RSS, shared BAL-cache bytes and validation stages. First and repeated requests are measured at 0/100% overlap, with normal and disabled decoded BAL caches. | Measurements collected for synthetic local workloads. Memory measures whole-node RSS/shared cache; the additional allocation pinned solely by validation was not isolated. No replicated latency improvement was established. |

A measured slowdown is a valid outcome for criterion 3. The remaining coverage
limits above should accompany any claim that all acceptance criteria are met.

## Tests completed

Regression tests use controlled in-memory/counting database fixtures, with real
EVM execution for the execution/root comparisons. The performance runs use actual
RETH node processes, real MDBX state providers and validation RPC calls against a
synthetic Amsterdam chain. Only the separate future-corpus replay utility has
mock-server checks; it did not produce these benchmark results.

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

Named regression entry points in [the test source](../../crates/rpc/rpc/src/validation/parent_bal.rs):

- `ethereum_execution_receipts_bal_and_state_roots_match`: enabled/off execution
  and calculated-root equality across five fixture variants.
- `every_partial_account_field_combination_preserves_provider_metadata` and
  `partial_code_change_preserves_nonce_balance_and_existence`: incomplete
  account/code entries preserve missing metadata through provider fallback.
- `final_writes_partial_accounts_and_provider_fallback`,
  `missing_or_disabled_bal_preserves_provider_reads` and
  `deletion_and_storage_wide_clears_use_parent_post_state`: final values, hits,
  missing/read-only values, deletion and clear fallback.
- `child_writes_and_child_storage_clears_win_over_parent_bal`: no stale parent
  values after child commits; no child modifications leaking into the next
  submission's parent read cache. An eager-prefetch regression checks precedence too.
- `noncanonical_parent_uses_its_exact_hash_and_post_state`: different parent
  hashes return their own values. This is the limited reorg-related coverage.

## Findings

Normal decoded BAL cache, 512 slots, full parent-write/child-read overlap.
Positive percentages mean slower. First-request numbers are paired median
changes from two runs with node roles reversed.

| Strategy | First provider slot calls | First latency change | Repeated validation |
|---|---:|---:|---|
| Lazy | 512 → 0 | +2.19% / +3.72% | No replicated speedup |
| Upfront per parent | 512 → 0 | +3.42% / +0.88% | No replicated speedup |
| Upfront every submission | 512 → 0 | +3.20% / −0.32% | +3.77% / +1.17% slower |

### Explicit before/after example: lazy loading

512 slots, full overlap, primary normal-cache run. Values are separate medians;
their ratios differ from the paired percentage statistic above.

| Metric | First submission: off → lazy | Repeated submission: off → lazy |
|---|---:|---:|
| Provider storage calls | 512 → 0 | 0 → 0 |
| Provider account calls | 9 → 9 | 0 → 0 |
| BAL acquisition time | 0 → 117.2 µs | 0 → 0 µs |
| Actual raw BAL fetch / decode | 0 / 0 → 0 / 0 µs (decoded-cache hit) | 0 / 0 → 0 / 0 µs |
| RPC validation latency | 8.271 → 8.309 ms | 4.063 → 3.912 ms |

With the decoded BAL LRU disabled, the same 512/full-overlap lazy control has
first acquisition **0 → 452.4 µs**, raw fetch **0 → 12.0 µs**, and decode
**0 → 177.4 µs**. Repeats do not reacquire/fetch/decode. These are separate
component medians, not additive; state/raw bytes remain warm in this control.

Whole-node RSS after the timed sequence is **2655.8 → 2611.6 MiB** in the primary
lazy run, versus **2606.9 → 2638.9 MiB** after node-role reversal. These noisy
whole-node figures do not establish an incremental BAL memory saving. Median
shared BAL-cache bytes are **16,691,816 → 16,691,816** for this full-overlap matrix;
both nodes import the same parents. RSS/cache gauges are retained per pair in
[the CSV](archive/flashbots-parent-bal-lazy-primary-pairs.csv). Latency, provider
counts and timing summaries are in [the JSON](archive/flashbots-parent-bal-lazy-primary-results.json).

The repeated-request improvement in the primary lazy run did not reproduce with
statistical confidence after role reversal. Repeats largely reuse the same
storage read set despite changing fees/state roots.

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

## Public backtesting and test-relay follow-up

**Status: prerequisite checks completed on two machines; neither full pipeline
was benchmarked.** These tools can provide stronger workloads, but the public
versions checked are not an interchangeable, ready-to-run Amsterdam/V6 replay
setup.

| Route | Checks and observations | Outcome |
|---|---|---|
| Historical rbuilder backtesting | Rechecked official rbuilder commit `6037fa728b13bf1806e16fff2586414216f6b8fa` and wiki commit `6f377406c636eb31540e60e5549a26e0be171419`. This Mac can access Mempool Dumpster: index/sample HEAD returned 200 and ranged GET returned 206. The archive is mainnet-only. Mainnet has no Amsterdam activation in the checked chain spec, while Sepolia activated at timestamp `1791294816`. The historical builder explicitly installs `MockRootHasher`, so its normal output has no validation-grade state root. | Archive access passed, but replay would take the pre-Amsterdam fallback and would not test parent BAL. No candidate was built or timed. Producing submit-ready V6 payloads requires a real root hasher, auxiliary V6 fields and post-Amsterdam state/BALs. |
| Historical state | No RETH database was found on this Mac. The exact branch binary found no published modular Sepolia snapshot. Listed mainnet archive snapshots are about 1.694–1.700 TB; this machine had 528 GiB free before the build. | No download started: Sepolia state was unavailable from this registry, and mainnet state neither fits nor supplies BAL-era parents. |
| Public rbuilder/test-relay | The current source still dispatches only V2–V5. The earlier cloud check downloaded and checksum-verified rbuilder v1.3.15 and reached the missing consensus-client prerequisite; it did not run the relay. | A V6-capable builder/relay and payload context are still required. This remains a compatibility check, not a relay benchmark. |

**Actual-node compatibility probe:** generated one Amsterdam child on the existing
local RETH build. V6 accepted it and served 64 slots from BAL with zero provider
slot calls. Invoking V5 with that payload failed with a block-hash mismatch before
state reads. This confirms the protocol mismatch rather than measuring a relay
performance improvement. Neither a missing-BAL fallback run nor an early failure
is counted as successful testing of BAL latency.

[The current Mac readiness record](archive/flashbots-historical-readiness-macos.json),
[the earlier cloud/source probe](archive/flashbots-public-tooling-readiness.json)
and [the reproducible source/API probe](../../scripts/probe_flashbots_public_tooling.py)
make the completed checks and blocked work explicit. The requested no-default
feature RETH binary was built at commit `523afb5cd3` with Rust 1.98.0; its SHA-256
is `83f426c80d438e0654ec9c23e2db122dbfc42e5b24c773b151c7a27313f5ba54`.
All 16 focused parent-BAL tests passed. Earlier synthetic latency findings remain
unchanged; these attempts add **zero new latency samples**.

Next steps require a post-Amsterdam RETH database with retained parent BALs and
matching realistic orderflow (for example Sepolia orderflow from another source),
plus a historical builder path that computes real roots and emits complete V6
submissions. Then run the same enabled/off comparison with real candidate
variation. Existing default backtest builder timings neither produce a complete
validation payload nor measure this Flashbots validation adapter.

## Remaining gaps

Live-node reorg validation, isolated incremental memory, production submission
replay, cold-storage behavior and full default/JIT-feature verification remain
open. These were synthetic Amsterdam workloads on two local RETH nodes, with a
two-core/8 GiB container. Keep the optimization experimental.

[Detailed results, confidence intervals and raw paired measurements](archive/flashbots-parent-bal-eager.md)
· [Regression tests](../../crates/rpc/rpc/src/validation/parent_bal.rs)
· [Node benchmark harness](../../scripts/bench_flashbots_parent_bal_paired.py)
