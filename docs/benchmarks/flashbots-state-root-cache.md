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

The cache retains one entry, with at most 8,192 aggregate map buckets (capacity,
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

Results pending optimized actual-node execution. See the follow-up test ledger
for each tested commit and limitations of the unavailable all-feature JIT lint.
