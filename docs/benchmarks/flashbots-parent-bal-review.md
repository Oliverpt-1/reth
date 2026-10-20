# Parent BAL validation: local findings

I tested using the parent's BAL to supply starting values during Flashbots
validation. The child's
writes took precedence, and all existing validation checks stayed enabled.

## What I tested

- Correctness fixtures covering missing BALs, partial account fields, code
  changes, account creation/deletion, cleared storage and child writes.
- Actual Reth nodes on generated Amsterdam chains, comparing BAL disabled,
  lazy loading, loading once upfront per parent and loading on every submission.
  Requests covered first and repeated submissions, plus children accessing all,
  some or none of the slots their parent wrote. State-root result caching was off.
- Memory ownership, controlled disk reads and validation across two-block reorgs.

Note: These used generated transactions, local databases, and test scripts. Ideally would
like to test this using an actual Sepolia replay of some kind.

## Results

**Correctness:** Results and state roots matched with BAL enabled and disabled
in the tested cases. BAL hits, database fallback and child-write checks passed.
The disk-read benchmark completed 1,152 successful validations. Across 32 reorgs,
160 valid submissions passed and 80 incorrect roots were rejected. Another 160
requests for unavailable noncanonical parents returned the same error in every
mode; those were rejection checks, not successful executions.

**Provider calls and validation time:** Below are disabled/lazy medians with
full overlap between the child's reads and the parent's writes.

| Measurement | Warm database, 512 slots | Cold database, 64 slots |
|---|---:|---:|
| First submission: provider storage calls | 512 / 0 | 64 / 0 |
| First submission: validation time | 8.271 / 8.309 ms | 26.429 / 26.355 ms |
| Repeat: provider storage calls | 0 / 0 | 0 / 0 |
| First submission: disk-read bytes | Not measured in this run | 1,785,856 / 1,785,856 |

There was no consistent overall speedup. Loading BAL on every submission added
repeat-request overhead. When the BAL had no value for a slot the child accessed,
it still needed the provider. Repeats already benefited from CachedReads with
BAL disabled.

**Why disk reads stayed the same:** Without BAL, execution loaded database pages
into memory and state-root calculation reused them. With BAL, execution skipped
some reads, but root calculation loaded those pages later. A separate 384-request
diagnostic run confirmed that 92–96 identical database pages moved between these
stages in each fully overlapping cold pair. Both modes still calculated and
checked the child's state root.

**BAL loading cost:** In the warm 512-slot run, getting an already decoded BAL
took 117.2 µs. With the decoded cache disabled, acquisition took 452.4 µs;
separately measured fetch/decode medians were 12.0/177.4 µs. These component
medians do not add up to total acquisition time. Raw BAL bytes were still warm.

**Memory:** Across 36 allocation cases, lazy and once-per-parent loading shared
the decoded BAL rather than copying it. They kept 117,472 bytes alive for the
512-slot fixture after the shared cache evicted it. Dropping the validation
views released that memory.

## Acceptance criteria and scope

All three requested categories have local evidence: matching results and roots,
hit/fallback/child-write regressions, and before/after measurements of provider
calls, BAL loading, memory and first/repeated validation time.

Historical Sepolia state and competing submissions remain untested. Disk
measurements above are Linux VM reads, not verified physical host SSD misses;
reorg coverage is limited to the generated two-block forks.

## Detailed evidence

- [Correctness, reorgs and acceptance-criteria mapping](archive/flashbots-parent-bal-reorg-study.md)
- [Latency comparisons and BAL loading costs](archive/flashbots-parent-bal-eager.md)
- [Disk-read benchmark](archive/flashbots-parent-bal-disk-study.md)
- [Stage-by-stage database page tracing](archive/flashbots-parent-bal-io-study.md)
- [Memory measurements](archive/flashbots-parent-bal-allocation-study.md)
