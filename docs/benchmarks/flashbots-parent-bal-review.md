# Parent BAL validation: local findings

I tested using the parent's BAL during Flashbots validation. It reduced reads
from the state provider, and results and state roots matched in the cases tested.
I haven't found a consistent improvement in validation time.

The tests use generated Amsterdam chains. Follow-up runs measured verified
Linux guest disk reads and checked validation across canonical-chain reorgs.
These are not historical Sepolia or production results.

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

### Earlier warm-node tests

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
copying them, but keep them alive after shared-cache eviction: 117,472 bytes
for the 512-slot fixture. Dropping the validation views released those bytes.
Disabled and sequential eager-submission retained no payload after eviction.
The 88-byte parent view exists in all four modes. This isolates payload lifetime
and caller allocations; it does not establish a whole-node memory saving.

### Verified disk-read tests

All **1,152 validations passed**, across eight parents, four modes, full/partial/
zero overlap and reversed treatment order. Candidates contained different signed
transactions accessing additional slots; exact repeats were measured separately.
Root-result caching was disabled and all computed roots matched the requests.

All cold first requests and new-access siblings recorded process reads, major
faults and guest disk reads. Warm requests and exact repeats recorded none.
With full overlap, disabled/lazy first-request medians were:

| Measurement | Disabled | Lazy |
|---|---:|---:|
| Provider storage calls / BAL hits | 64 / 0 | 0 / 64 |
| Execution | 6.101 ms | 0.654 ms |
| State-root calculation | 19.294 ms | 24.591 ms |
| Total validation | 26.429 ms | 26.355 ms |
| Process disk-read bytes | 1,785,856 | 1,785,856 |

Execution improved, but overall disk reads were unchanged and the latency
difference was uncertain. Eager-parent improved one partial-overlap comparison;
eager modes were slower at zero overlap. There was no consistent overall speedup.

Stage-level instrumentation explains the unchanged I/O. Across eight parents
and both treatment orders, each fully covered cold lazy request moved **92–96
identical 4 KiB MDBX pages** from transaction execution to state-root
calculation. The combined page-offset set, 1,785,856 read bytes and 436 major
faults were unchanged. Zero-overlap, additional-access and warm controls also
matched as expected. BAL made execution faster, but root calculation still
needed the same database pages. These are intrusive diagnostic timings, not a
replacement for the latency results above.

The allocation-lifetime and guest cold-read measurements are complete for these
fixtures. The Mac can cache the VM's disk, so this does not prove physical SSD
misses. The detailed report includes p95, parent-level uncertainty, all modes,
fetch/decode costs and RSS; RSS alone is not the memory-isolation result.

### Reorg validation

I also ran an actual-node reorg regression across all four modes, with the decoded
BAL cache enabled and disabled. Two forks wrote different values at the same
heights. Across 32 two-block chain switches, all **160 valid submissions** passed
their root checks, all **80 deliberately wrong roots** were rejected, and BAL
hits/provider fallback matched expectations. Responses matched the disabled path.

The existing provider does not expose these parents while they are noncanonical.
All modes returned the same `parent block not found` error for **160** such
requests. Those are rejection checks, not successful noncanonical root checks.
Switching each fork back to canonical restored successful validation with its
own values. This is generated-chain correctness evidence, not another timing run.

## Coverage against the acceptance criteria

| Criterion | Evidence and scope |
|---|---|
| Matching results and roots, including missing/partial BAL data, code changes, cleared slots and reorgs | Enabled/off execution fixtures cover the account/storage cases. Actual-node validation checks claimed roots. The new reorg test covers successful validation before/after canonical switches and identical rejection of unavailable noncanonical parents. |
| BAL hits, provider fallback and no stale values after child writes | Counter assertions and child-write/storage-clear regressions passed. Reorg runs also verified per-fork values, BAL commitments, CachedReads repeats and fallback for additional slots. |
| Before/after provider reads, BAL fetch/decode, memory and first/repeated latency | Warm-node comparisons, 1,152 guest disk-read validations and 36 allocation/lifetime cases provide these measurements. They cover generated workloads; there is no consistent overall speedup claim. |

[Detailed test mapping and reorg evidence](archive/flashbots-parent-bal-reorg-study.md).

## Remaining limits

Generated siblings establish behavior with additional storage accesses, not
representative competing bids. Historical replay, production performance and
physical host SSD misses remain untested. The reorg test covers a two-block
generated fork, not every reorg depth, finality or concurrent-request scenario.

## Supporting results

- [Reorg test and acceptance-criteria mapping](archive/flashbots-parent-bal-reorg-study.md),
  [summary](archive/flashbots-parent-bal-reorg-summary.json) and
  [raw checks](archive/flashbots-parent-bal-reorg-results.json.gz)
- [Isolated allocations, ownership and limits](archive/flashbots-parent-bal-allocation-study.md)
  and [raw allocation measurements](archive/flashbots-parent-bal-allocations.json)
- [Disk-read methodology and results](archive/flashbots-parent-bal-disk-study.md),
  [summary](archive/flashbots-parent-bal-disk-summary.json) and
  [compressed raw measurements](archive/flashbots-parent-bal-disk-results.json.gz)
- [Stage I/O and page-offset attribution](archive/flashbots-parent-bal-io-study.md),
  [parsed attribution](archive/flashbots-parent-bal-io-attribution.json.gz) and
  [raw diagnostic records](archive/flashbots-parent-bal-io-results.json.gz)
- [Full methodology, mode comparisons and confidence intervals](archive/flashbots-parent-bal-eager.md)
- [Saved benchmark results](archive/flashbots-parent-bal-lazy-primary-results.json)
  and [individual paired measurements](archive/flashbots-parent-bal-lazy-primary-pairs.csv)
- [Correctness tests](../../crates/rpc/rpc/src/validation/parent_bal.rs)
  and [node benchmark script](../../scripts/bench_flashbots_parent_bal_paired.py)
- [Public data checks](archive/flashbots-parent-bal-data-sources.md)
  and [storage requirements](archive/flashbots-parent-bal-storage-blocker.md)
