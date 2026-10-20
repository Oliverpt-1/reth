# Generated-chain reorg validation and acceptance mapping

## Result

The actual-node reorg regression passed. It uses authenticated Engine API
messages to change the canonical chain, then calls
`flashbots_validateBuilderSubmissionV6` on the same running node. The production
validation implementation was not changed.

| Check | Total across eight profiles |
|---|---:|
| Canonical head/state checks | 40 |
| Two-block reorgs | 32 |
| Valid submissions accepted with claimed/computed root agreement | 160 |
| Deliberately incorrect roots rejected | 80 |
| Noncanonical-parent requests rejected identically | 160 |
| Total validation RPC checks | 400 |

Each profile uses one of disabled, lazy, eager-parent and eager-submission modes,
with either a normal or disabled decoded BAL cache. All outcomes match the
provider-only profile for the same requests and chain stage. The initial smoke
round covered disabled/lazy with normal BAL caching and is recorded separately;
the totals above exclude it.

## What ran

```sh
PYTHONPATH=/workspace/scratch/pydeps python \
  scripts/test_flashbots_parent_bal_reorg.py \
  --binary target/release/reth \
  --output-dir /workspace/scratch/bal-reorg-full
```

Python dependencies are the same `eth-account`, `rlp` and `trie` packages as the
existing paired node harness. The output directory must be new. The script
starts/stops local nodes and never contacts a public chain. A public development
key signs transactions funded only in the generated genesis. V6 bid metadata is
synthetic, not an original relay submission.

The existing optimized Linux x86-64 binary has embedded revision
`353e99a26d8851e93694a693b0ce040dca5858db`, SHA-256
`5ca052f1c7ee779abce2833e5abef7d693a399eb1d66af7ffa00527fc17c5bd8`.
It is Reth 2.7.0, no default/JIT features, Rust 1.99 release, LTO disabled and
16 codegen units. Production Rust files have not changed since that revision;
subsequent Rust additions are the separate allocation-test executable. This
run used the two-CPU/8 GiB Linux workspace. The harness source revision is
`ed9084b0a7ae82ba20f527d93a223c886452ecae`; exact script/helper hashes are saved.

This is a correctness run. It produces zero new performance samples. Existing
warm/disk/allocation benchmark results remain unchanged.

## Fork and cache controls

A common block is followed by forks `A1 -> A2` and `B1 -> B2`. Both branches
have the same height and sender nonce but different storage values: the first
64 contract slots contain 7 at A2 and 20 at B2. Each branch has two independently
built child candidates. One updates all 64 slots covered by the parent BAL;
the second overlaps 32 covered slots and accesses 32 additional slots. Children
are built using `forkchoiceUpdatedV4` and `getPayloadV6`, not hand-invented roots.
The harness independently reconstructs the transaction trie and RLP header and
checks the resulting block hash before making any negative variant.

The treatment node imports five blocks using `newPayloadV5`. Its canonical head
then follows `A2 -> B2 -> A2 -> B2 -> A2`, leaving safe/finalized hashes zero so
those blocks remain reorgable. It checks the actual canonical head, state root,
storage value and raw parent BAL at each stage. BAL bytes must match the fixture
and the header commitment.

Each valid child is submitted twice. The full-overlap child makes 64 provider
slot calls with BAL disabled, versus 64 BAL hits and zero provider slot calls
when enabled. The partial-overlap child then makes 32 new provider calls in all
modes: its covered half is already in CachedReads. Exact repeats make zero
provider/BAL slot calls. These assertions also hold after switching back to a
previously canonical branch, when stale values from the other fork would fail
the claimed root or rebuilt child BAL check.

Negative variants retain the correct parent, signed transactions and BAL but
claim the other fork child's state root. Their headers/block hashes are rebuilt
consistently. All modes must return the same `mismatched block state root`
error. This verifies root calculation ran; a successful response is not accepted
as sufficient evidence by itself. Root-result caching is disabled throughout.

### Noncanonical parent semantics

The existing provider exposes canonical/pending headers/state, not these
arbitrary Engine-tree side branches. When a request names the other branch's
parent, baseline validation returns JSON-RPC error `-32603`,
`parent block not found`. All BAL modes must return that exact error without
reading storage or BAL slots. These requests are not counted as valid executions
or root comparisons. Canonicalizing that branch then permits successful
validation against its actual state.

The earlier adapter regression additionally checks different available parent
hashes return different values. Together the tests cover exact-hash lookup,
actual canonical reorgs, cache invalidation/reuse and existing unavailable-parent
error semantics. They do not add support for validating arbitrary unavailable
side-chain state.

## Mapping to the original acceptance criteria

| Requested item | Test or measurement | What is established |
|---|---|---|
| Identical validation results and state roots | `ethereum_execution_receipts_bal_and_state_roots_match`; actual-node warm/disk runs; this reorg run | Enabled/off execution results, receipts, rebuilt BALs, state changes and independent fixture roots match. Actual-node valid requests must match Reth's computed root; invalid roots are rejected. |
| Missing BALs | `missing_or_disabled_bal_preserves_provider_reads`; missing-BAL execution variant; `parent_commitment_and_pre_amsterdam_fallback` | Unavailable/disabled BAL and pre-Amsterdam parents retain the provider path. |
| Partial account fields | `every_partial_account_field_combination_preserves_provider_metadata`; `final_writes_partial_accounts_and_provider_fallback`; execution variant | Unrecorded fields remain complete through provider fallback rather than being cached as invented complete accounts. |
| Code bytes/changes | `complete_account_and_changed_code_avoid_provider_reads`; `partial_code_change_preserves_nonce_balance_and_existence`; execution variant | Recorded code is used while absent metadata/existence falls back correctly. |
| Creation, deletion and cleared storage | `deletion_and_storage_wide_clears_use_parent_post_state`; lifecycle/clear execution variants; child-clear test | Parent post-state semantics and child storage clears are preserved. |
| Reorged parents | `noncanonical_parent_uses_its_exact_hash_and_post_state`; this actual-node A/B/A/B/A test | Per-fork results/roots and BAL/provider counters match after canonical switches; unavailable noncanonical parents retain the exact baseline rejection. |
| BAL hits and provider fallback | Counting-database regressions, node metric assertions, 0/50/100% disk-workload overlap | Covered values use BAL; missing/read-only/uncovered values preserve fallback. |
| No stale values after child writes | `child_writes_and_child_storage_clears_win_over_parent_bal`; eager-prefetch counterpart; two-transaction execution fixture | Child balance, nonce, code, storage and clears take precedence; the next submission's CachedReads retains parent values. |
| Before/after provider reads | Paired warm benchmarks and 1,152-request disk matrix | Account/storage/code calls and BAL hits were counted for first, sibling and repeated submissions. These are not equated with physical disk reads. |
| BAL fetch/decode cost | Normal/disabled decoded-cache benchmarks and separate disk-run preload measurements | Acquisition, fetch and decode costs were measured separately. Preloaded timed validations do not claim additional fetch/decode work. |
| Memory | 36 allocation/lifetime cases plus node RSS/shared-cache gauges | Decoded payload sharing, temporary caller allocations, extended lifetimes after eviction and release are measured. Worker/allocator overhead exclusions remain explicit. |
| First/repeated validation latency | Nine earlier node timing runs plus the 1,152-request disk matrix | First requests, changed candidates/new reads and exact repeats were measured separately with uncertainty and reversed order/roles. |
| Dependence on parent/child overlap | 0/100% warm workloads and 0/50/100% disk workloads | Read reduction varies with coverage; no consistent overall speedup was established. |

Relevant files:

- [Production adapter and regression tests](../../../crates/rpc/rpc/src/validation/parent_bal.rs).
- [New reorg harness](../../../scripts/test_flashbots_parent_bal_reorg.py).
- [Summary](flashbots-parent-bal-reorg-summary.json),
  [compressed raw results](flashbots-parent-bal-reorg-results.json.gz),
  [earlier smoke results](flashbots-parent-bal-reorg-smoke.json.gz).
- [Warm benchmark methods and raw-result links](flashbots-parent-bal-eager.md).
- [Disk study](flashbots-parent-bal-disk-study.md) and
  [isolated allocation study](flashbots-parent-bal-allocation-study.md).

## Boundaries

All requested categories have now been exercised on generated workloads, with
the specific noncanonical-parent behavior above. This does not mean every
possible workload or reorg is covered. The node test uses one two-block fork
with recent state in the engine's canonical buffer; it does not test every
depth, finalized-block conflict, on-disk unwind or in-flight validation race.
The prior memory study is an isolated allocation experiment, and the disk study
proves Linux guest misses, not physical host SSD misses. Historical replay,
representative private competing submissions, production performance and a full
default/JIT-feature runtime comparison have not been established.

The initial smoke attempt caught a Python condition-order bug in the new head
poller before fixture generation. It was corrected; the disabled/lazy smoke and
the final full matrix then passed. No production-code fix was needed.
