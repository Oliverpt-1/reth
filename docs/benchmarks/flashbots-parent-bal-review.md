# Parent BAL validation: local findings

I tested using the parent's BAL during Flashbots validation. It reduced reads
from the state provider, and results and state roots matched in the cases tested.
I haven't found a consistent improvement in validation time.

The tests so far used a small synthetic chain with
mostly cached data, so they don't tell us whether avoiding more expensive reads
would make the extra BAL work worthwhile.

## What I tested

- **Correctness:** controlled account and storage fixtures, with real EVM
  execution and calculated state roots. Cases included missing BALs, partial
  account fields, code changes, creation/deletion, storage clears, provider
  fallback and child writes overriding parent values.
- **Performance:** actual Reth nodes with MDBX databases and Flashbots validation
  RPC calls. A script created a private Amsterdam chain and signed transactions
  calling a contract that updates 64 or 512 storage slots. These were generated
  transactions, not historical submissions. Nodes ran in a two-core, 8 GiB
  container.

I compared the existing path with lazy BAL loading, loading upfront once per
parent, and loading upfront on every submission. Tests covered first and
repeated submissions, children accessing the parent's written slots or different
slots, and reversed node roles. State-root result caching was disabled.

## What I found

One example: 512 slots, with the child accessing the slots its parent wrote,
using the normal decoded BAL cache. These are separate medians from the primary
lazy-loading run.

| Measurement | First submission: disabled / lazy | Repeated submission: disabled / lazy |
|---|---:|---:|
| Provider storage calls | 512 / 0 | 0 / 0 |
| Provider account calls | 9 / 9 | 0 / 0 |
| BAL acquisition time | 0 / 117.2 µs | 0 / 0 µs |
| Raw BAL fetch and decode | Both zero: decoded BAL already cached | Both zero |
| Validation time | 8.271 / 8.309 ms | 4.063 / 3.912 ms |

The lower repeated-request time did not reproduce with statistical confidence
when node roles were reversed. Neither lazy loading nor loading upfront once
per parent showed a consistent speedup. Loading on every submission made repeats
slower in both normal-cache runs at 512 slots.

When children accessed different slots from the parent's writes, storage calls
were unchanged. On repeats, the existing CachedReads cache already removed EVM
provider calls in both modes. Repeats changed fees and roots but largely reused
the same storage accesses. These counters measure provider calls, not physical
disk reads, and BAL does not remove state-root calculation.

With the decoded BAL cache disabled, first lazy acquisition took 452.4 µs in the
512-slot case. Separate fetch/decode medians were 12.0/177.4 µs; component medians
are not additive. State and raw BAL bytes remained warm, so this was not a
cold-disk test.

An isolated allocation test now covers the production adapter and shared ETH
cache across 36 cases. Lazy and eager-parent share decoded BALs rather than
copying them, but keep them alive after shared-cache eviction: 117,376 bytes
for the 512-slot fixture. Dropping the validation views released those bytes.
Disabled and sequential eager-submission retained no payload after eviction.
The 88-byte parent view exists in all four modes. This isolates payload lifetime
and caller allocations; it does not establish a whole-node memory saving.

## What I'd test next

The next comparison should use a larger state database and realistic
transactions, including different candidates for the same parent. The question
is whether BAL avoids expensive reads often enough to offset its own overhead.

## Supporting results

- [Isolated allocations, ownership and limits](archive/flashbots-parent-bal-allocation-study.md)
  and [raw allocation measurements](archive/flashbots-parent-bal-allocations.json)
- [Full methodology, mode comparisons and confidence intervals](archive/flashbots-parent-bal-eager.md)
- [Saved benchmark results](archive/flashbots-parent-bal-lazy-primary-results.json)
  and [individual paired measurements](archive/flashbots-parent-bal-lazy-primary-pairs.csv)
- [Correctness tests](../../crates/rpc/rpc/src/validation/parent_bal.rs)
  and [node benchmark script](../../scripts/bench_flashbots_parent_bal_paired.py)
- [Public data checks](archive/flashbots-parent-bal-data-sources.md)
  and [storage requirements](archive/flashbots-parent-bal-storage-blocker.md)
