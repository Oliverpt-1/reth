# Parent BAL allocation experiment

## Result

All 36 allocation/lifetime cases passed: four modes, 64/512/4096 written
storage slots, and 1/8/32 simultaneously retained parent views. This closes
the isolated payload/reference-lifetime measurement for these fixtures. It
does not measure all allocator activity in a running node.

Lazy and eager-parent share the existing decoded BAL instead of copying it.
They keep that allocation alive after eviction from the ETH cache. Clearing
the validation views released every tracked payload and view allocation.
Disabled and sequential eager-submission kept no payload alive after eviction.

| Slots in one parent | Shared raw + decoded bytes | Decode peak bytes | Temporary bytes above retained | Bytes pinned after shared eviction, lazy / eager-parent |
|---|---:|---:|---:|---:|
| 64 | 15,430 | 19,510 | 4,080 | 15,430 / 15,430 |
| 512 | 117,376 | 146,544 | 29,168 | 117,376 / 117,376 |
| 4096 | 936,008 | 1,165,880 | 229,872 | 936,008 / 936,008 |

Each parent view allocated 88 requested bytes in **all** modes, including
disabled; this is not an 88-byte incremental cost of enabling BAL. Pinned
payload bytes scaled linearly with retained parents. Production has one cached
parent; multiple views model outstanding concurrent validations.

| Mode | First acquisition peak above starting live bytes | Repeat peak | Payload pinned after shared eviction |
|---|---:|---:|---|
| Disabled | 48 B | 48 B | None |
| Lazy | 176 B | 112 B | Existing allocation, lifetime extended |
| Eager-parent | 280 B | 112 B | Existing allocation, lifetime extended |
| Eager-submission | 368 B | 368 B | None in sequential same-parent requests |

These acquisition figures were size-independent for the single-parent cases.
They include caller-side commitment closures, adapter accounting and channel
requests. Larger runs sometimes retained reusable channel storage until the
cache service shut down; raw results report it separately. It also returned to
zero. An initial assertion incorrectly required this storage to disappear when
the validation view was dropped; the final test checks its actual owner lifetime.

## What ran

```sh
python3 scripts/record_parent_bal_allocations.py \
  --output docs/benchmarks/archive/flashbots-parent-bal-allocations.json
```

Source: `4a0488c3ba`, with the new harness recorded by SHA-256 in the
[raw results](flashbots-parent-bal-allocations.json). Apple M4 Max, 14 cores,
36 GB RAM, macOS 15.7.9; rustc 1.98.0 (full compiler identification in the JSON).
This is a debug integration-test executable with reth-rpc's default features,
not a release latency test. The exact Cargo command, stdout and stderr are saved.

The harness includes the actual production `parent_bal.rs` module and uses
`LazyParentBal`, `ParentBalDb`, `committed_loader` and a real one-entry
`EthStateCache`. Raw storage-only fixtures are decoded with the same conversion
as `CachedRevmBal::try_from_raw`. Every treatment starts with the same decoded
fixture inserted into the cache. Each parent gets a first acquisition and three
repeats, then a different hash evicts the shared cache entry. Weak references
verify ownership; allocation counters verify release after views are dropped.

The allocator records requested sizes and carries tags across thread boundaries
so worker-thread frees are counted. Fixture generation, output serialization,
the test's vectors and weak references are excluded. Raw bytes, decoded trees
and their inner Arc are counted once. The shared cache's outer Arc/LRU bookkeeping,
allocator metadata/padding and untagged service-worker allocations are excluded.
The reported decode peak is for the production-equivalent conversion, not an
instrumented end-to-end disk fetch. This establishes payload retention and
caller-side temporary costs, not a complete process allocation census.

## Implementation audit

`cached_state` owns `(parent_hash, CachedReads, Arc<LazyParentBal>)`. Same-parent
updates extend CachedReads without replacing that Arc; a new parent replaces
the tuple. Active requests can keep the old Arc alive. Lazy resolves on demand;
eager-parent holds two references to the same shared allocation through its
prefetch and resolved cells. Neither duplicates the BAL.

Eager-submission creates a fresh request-local view. In the sequential
same-parent path, updating CachedReads does not install that view in the parent
cache. Earlier descriptions implying that it retains the last submission's BAL
were inaccurate for this path. Concurrent parent changes are not covered here.

EVM mutable state and CachedReads can satisfy accesses before `ParentBalDb`.
The adapter falls back for uncovered fields, slots and code; MDBX and the OS
file cache sit below the provider. State-root calculation remains separate.
The root-result cache is opt-in and is not enabled by a parent-BAL mode.

The memory harness deliberately uses EmptyDB to isolate ownership; it is not
a validation correctness test. Whole-node RSS and validation/root comparisons
belong to the node experiment and must not be inferred from these allocations.
