# Flashbots exact-state root reuse experiment

## Why this experiment

[The paired parent-BAL experiment](flashbots-parent-bal-followup.md) found no
reliable validation latency win, even with an already-decoded provider handle.
The larger measured cost was repeated state-root calculation: approximately
1.65 ms at 512 slots versus 0.47 ms EVM execution, after read caches were warm.

## Design

`--rpc.flashbots-state-root-cache` is independent of the parent-BAL flag and
**disabled by default**. Every request still recovers and executes the block,
checks headers and consensus outputs, verifies the rebuilt BAL and proposer
payment, and derives the complete hashed post-state using the exact parent
provider. Only the pure `state_root(hashed_post_state)` result is reusable.

A hit requires the exact parent hash and full `HashedPostState` equality. This
includes balance, nonce, code hash, account creation/deletion, every recorded
storage value, and explicit zeroes expanded by the provider for destroyed
storage. The claimed header root is excluded from the key and compared against
the computed result on every request. Different child changes or reorged parent
hashes miss; provider failures are not retained. Equal final changes can reuse a
root even if the payload's other fields differ, after those fields are checked.

The cache retains one entry, with at most 8,192 entries of aggregate map capacity (capacity,
not length), plus at most 256 KiB account-extension payload when enabled.
Oversized inputs bypass cloning/retention. Locking only snapshots or replaces an
Arc; full equality and root computation run outside the lock. Concurrent misses
can compute independently. Retention is bounded; transient state copies still
scale with concurrent eligible misses.

Memory telemetry estimates retained map key/value capacity and extension bytes,
excluding allocator/table overhead. The benchmark separately measures node RSS;
RSS includes the entire node and database, so small differences cannot be
attributed precisely to this cache.

## Verification scope

Unit regressions use actual Ethereum MPT roots and cover changed account fields,
code hash, account deletion/creation, zeroed/cleared storage, different/reorged
parents, map order, concurrency, provider errors and oversized sparse maps.
The full API regression primes the cache, proves the second root calculation was
skipped, then rejects an incorrect claimed root and an unpaid bid.

The actual-node experiment mirrors identical Amsterdam blocks through Engine API
between two isolated Reth processes. Timed V6 submissions use identical payloads
and claimed roots. Mining/imports, warmup, CPU-idle checks, metrics, RSS and bootstrap
analysis are outside timing. Paired order is randomized; confidence intervals
resample whole parents. A second run reverses producer/follower roles and seed.
The cache-only BAL flag remains off in the root-only comparison. Repeats vary
extraData to give distinct valid payload hashes with identical executed state.
Wrong-root and unpaid requests must produce identical rejections on both nodes.
An additional changed-fee workload signs valid sibling transactions, reconstructs
account roots from current-child Merkle proofs, and updates BAL commitments.
Their differing final balances must miss the root cache.

These nodes run in the workspace (2-core CPU quota, 8 GiB memory limit); they are
not a production performance node or representative builder replay corpus.
A gain on identical final state does not establish a gain on competing children
with different final states. Cache hit rate in the intended workload remains a
separate requirement before rollout. No first-submission improvement is assumed.

## Reproduce

```sh
BINDGEN_EXTRA_CLANG_ARGS=-I/usr/lib/gcc/x86_64-linux-gnu/14/include \
CARGO_BUILD_JOBS=2 CARGO_PROFILE_RELEASE_LTO=false \
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
cargo build --release -p reth --bin reth --no-default-features

# eth-account==0.14.0 and trie==3.1.0 must be installed in this Python environment.
python scripts/bench_flashbots_parent_bal_paired.py \
  --binary target/release/reth --output-dir /tmp/reth-root-paired \
  --optimization root --slots 64 512 --overlaps 0 25 100 \
  --samples 100 --repeats 5 --seed 1729 --vary-extra-data
python scripts/bench_flashbots_parent_bal_paired.py \
  --binary target/release/reth --output-dir /tmp/reth-root-reversed \
  --optimization root --slots 64 512 --overlaps 0 25 100 \
  --samples 100 --repeats 5 --seed 2026 --reverse-nodes --vary-extra-data
```


## Measured results

**A repeat-case improvement is proven in this synthetic workload, with the
same executed state and different valid payload hashes. It is not a general
speedup for different candidate states.** The parent-BAL-only variants remain
unproven for latency and should stay disabled by default.

All four full runs used the same optimized artifact, source commit
`ff96d05b024c8a18e4dae4aca48b6cff1b38e199`, Rust 1.99.0, no default features,
LTO disabled, 16 codegen units. SHA256:
`db21aa05bb4b2ee6e627edc4115154a551c3a652ea22164712dbc101095fa652`.
There were no concurrent builds/tests during timing. 300 broader RPC/core tests,
43 targeted regressions and warnings-denied affected-package clippy passed.
Full-workspace all-feature/JIT clippy is blocked by unavailable LLVM 22.

The full runs passed **19,200 timed valid V6 submissions** and **6,400 invalid
submissions rejected identically** by baseline and optimized nodes. The valid
submissions include noncanonical siblings with independently reconstructed roots
and rebuilt BALs. No mismatched roots, acceptance results, stale values or cache
hit/miss counter assertions occurred. Every submission executed and ran its
remaining checks.

Representative end-to-end latencies at 100% parent/child slot overlap:

| Slots | Node roles | First median off → on (ms) | Repeat median off → on (ms) | Repeat p95 off → on (ms) | Paired repeat change [95% CI] |
|---|---|---:|---:|---:|---:|
| 64 | Primary | 2.208 → 2.296 | 1.196 → 0.770 | 2.094 → 1.401 | -36.06% [-37.54%, -34.26%] |
| 512 | Primary | 4.991 → 5.114 | 3.033 → 1.687 | 4.363 → 2.527 | -44.72% [-45.89%, -43.81%] |
| 64 | Reversed | 2.236 → 2.224 | 1.223 → 0.785 | 2.156 → 1.429 | -35.41% [-37.26%, -33.05%] |
| 512 | Reversed | 5.109 → 4.987 | 3.056 → 1.618 | 4.221 → 2.384 | -47.62% [-48.17%, -46.39%] |

Paired changes use each parent's on/off latency ratio (median of its five
repeats), rather than the ratio of aggregate medians. All six cases in both
role arrangements improved: 29.5–37.1% at 64 slots, 43.6–47.6% at 512 slots.
Every repeat confidence interval excludes zero. Every first-submission interval
includes zero; first-submission improvement is unproven.

The root/hash stage accounts for the improvement; EVM execution remains:

| Slots | Primary repeated root/hash off → on (µs) | EVM off → on (µs) | Trie branch seeks off → on | Trie leaf seeks off → on |
|---|---:|---:|---:|---:|
| 64 | 471.5 → 32.4 | 115.0 → 113.6 | 33 → 0 | 247 → 0 |
| 512 | 1606.1 → 247.0 | 504.6 → 485.6 | 105 → 0 | 690 → 0 |

These are trie traversal counters, not physical disk I/O counts. Root hits leave
zero measured trie traversal. The root/hash stage still derives the hashed state
and compares it; those steps are not skipped. EVM provider slot calls are 64/512
on the first request and zero on repeats in both modes, thanks to CachedReads.
Account calls are nine on the first request in both modes. Parent BAL fetch,
decode and acquisition cost are zero in this root-only comparison.

## Changed-state controls and limitations

Changing each sibling's transaction gas price changes final sender/beneficiary
balances and its root. All such repeated lookups miss. Neither role arrangement
shows a reliable speedup across these changed-state cases:

| Slots | Node roles | Repeated median off → on (ms) | Paired change [95% CI] |
|---|---|---:|---:|
| 64 | Primary | 1.146 → 1.145 | -2.44% [-3.59%, 0.32%] |
| 512 | Primary | 2.845 → 2.885 | 1.64% [0.79%, 2.84%] |
| 64 | Reversed | 1.215 → 1.208 | -0.86% [-3.31%, 0.99%] |
| 512 | Reversed | 2.833 → 2.826 | -0.09% [-1.36%, 1.41%] |

The 512-slot primary miss workload was 1.64% slower; the reversed run showed no
clear change. Retaining a key and checking it has a cost. Do not assume the
repeat-case win applies to a builder's changing candidate blocks. Measure
`state_root_cache_hits / (state_root_cache_hits + state_root_cache_misses)` on
representative replay before enabling this option. Both experimental flags remain
disabled by default. Production workload acceptance is not claimed.

Estimated retained key/value capacity is **7,720 bytes** at 64 slots and
**57,896 bytes** at 512 slots, excluding allocator/table overhead. Primary
100%-overlap node RSS medians were approximately 2,586 → 2,562 MiB and
2,648 → 2,638 MiB; reversed runs 2,570 → 2,544 MiB and 2,639 → 2,648 MiB.
These RSS differences change with node roles and entire-node allocation patterns,
and cannot be attributed precisely to a tens-of-KiB cache. The one-entry retention
limit and transient concurrent-copy behavior are documented above.

Reproduce changed-state controls with the same command, replacing
`--vary-extra-data` with `--vary-gas-price`, using `--overlaps 100`, and repeating
with `--reverse-nodes` plus a different seed. The fixture requires the single
legacy transaction and bytecode without GASPRICE that the harness generates;
proof paths are primed on both nodes outside timing.

## Artifacts

For each full run, the summary records artifact identity, stage timings,
provider/trie counts, memory and confidence intervals. CSV files contain all
paired timings and relevant counts to independently resample parent pairs.
Full raw request-level measurements are also retained in the workspace scratch
run directories. No external node credentials are needed for reproduction.

- [Primary matching-state summary](flashbots-state-root-cache-primary-results.json), [pairs](flashbots-state-root-cache-primary-pairs.csv).
- [Reversed matching-state summary](flashbots-state-root-cache-reversed-results.json), [pairs](flashbots-state-root-cache-reversed-pairs.csv).
- [Primary changed-state summary](flashbots-state-root-cache-fee-primary-results.json), [pairs](flashbots-state-root-cache-fee-primary-pairs.csv).
- [Reversed changed-state summary](flashbots-state-root-cache-fee-reversed-results.json), [pairs](flashbots-state-root-cache-fee-reversed-pairs.csv).
- [Tested-commit ledger](flashbots-parent-bal-followup.md).
