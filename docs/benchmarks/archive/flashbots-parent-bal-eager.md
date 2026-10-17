# Parent BAL loading: lazy versus upfront

All three shared-ETH-cache strategies reduce covered EVM provider slot calls and
pass the tested correctness cases. **None establishes a replicated end-to-end
latency improvement in these local warm-state workloads.** Fetching BAL on every
submission adds repeat-request overhead even when its decoded form is cached.
The original state-read reduction works; a production latency benefit is unproven.

## Implementation

The opt-in `--rpc.flashbots-parent-bal-mode` supports:

- `lazy`: acquire parent BAL on the first underlying CachedReads miss; retain
  the decoded view or its absence for that parent.
- `eager-parent`: await acquisition before execution once per parent; concurrent
  acquisitions are deduplicated, and repeats retain the resolved view.
- `eager-submission`: await acquisition before every submission; the shared ETH
  BAL LRU may satisfy it without another provider fetch or decode.

All modes use the same read-only final-parent-state database adapter below
CachedReads and child mutable state. Partial account metadata and read-only slots
fall back to the provider. Commitment checks, missing BALs, code changes,
account lifecycle, storage clears and noncanonical parent behavior are covered
by regressions. Child writes take precedence. Pre-Amsterdam parents skip BAL
acquisition. The parent BAL is never attached to the child executor's State.
The eager path awaits the ETH cache before synchronous execution, avoiding an
artificial extra blocking-worker hop. Provider-only remains the default.

## Normal decoded BAL cache

512 requested slots, 100% covered by parent writes. Each cell is the median
paired percentage change and a 95% bootstrap confidence interval. **Positive
means slower.** Primary and reversal runs use different seeds and exchange which
node produces blocks. Improvements seen only in the primary run do not replicate.

| Strategy | First: primary | First: reversed | Repeat: primary | Repeat: reversed |
|---|---|---|---|---|
| lazy | +2.19% [-3.33%, +5.77%] | +3.72% [+0.40%, +8.94%] | -4.41% [-7.43%, -1.99%] | -1.22% [-2.65%, +0.22%] |
| eager-parent | +3.42% [-1.08%, +8.49%] | +0.88% [-4.24%, +4.94%] | -2.77% [-4.95%, -0.55%] | -0.55% [-4.86%, +2.67%] |
| eager-submission | +3.20% [+0.47%, +8.88%] | -0.32% [-2.70%, +2.66%] | +3.77% [+2.22%, +6.35%] | +1.17% [+0.34%, +2.11%] |

| Strategy | First provider slot calls, off → on | First BAL acquisitions | BAL acquisitions over five repeats | Repeat EVM provider calls, both nodes |
|---|---:|---:|---:|---:|
| lazy | 512 → 0 | 1 | 0 | 0 |
| eager-parent | 512 → 0 | 1 | 0 | 0 |
| eager-submission | 512 → 0 | 1 | 5 | 0 |

At 0% overlap, first slot calls remain 64/512 on both nodes. All first requests
still make nine account metadata calls in both modes; these BAL accounts are
partial. Repeated CachedReads already remove EVM provider calls, so acquiring
BAL again cannot save those calls. Root calculation still performs trie reads;
provider slot counters here are EVM API calls, not physical disk reads.

Decoded-cache-hit acquisition medians span about 94–126 µs on first requests and
78–94 µs on eager-submission repeats in the full-overlap cases. Actual raw BAL
provider fetch and decode counters are zero: these are ETH-service/cache requests,
not repeated decoding. At 512 slots, eager-submission repeat regression replicates
at +3.77% and +1.17%; lazy and eager-parent show no replicated speedup.

## Decoded BAL LRU disabled

`--rpc-cache.max-bals 0` on both nodes exposes actual fetch/decode cost. State and
raw BAL bytes remain warm; this is **not disk-cold** testing. These controls have
50 parents per case and primary node roles only, so do not claim role-replicated
cold-cache results.

| Strategy | Slots | First change [95% CI] | Repeat change [95% CI] | First acquisition / decode, µs | Repeat acquisition / decode, µs |
|---|---:|---|---|---:|---:|
| lazy | 64 | +10.86% [-0.38%, +36.81%] | -5.78% [-10.48%, -0.09%] | 268.0 / 39.5 | 0.0 / 0.0 |
| lazy | 512 | +7.08% [-2.62%, +19.14%] | +8.99% [+1.05%, +20.61%] | 452.4 / 177.4 | 0.0 / 0.0 |
| eager-parent | 64 | +16.95% [+1.37%, +25.36%] | +2.96% [+0.19%, +5.08%] | 256.2 / 36.7 | 0.0 / 0.0 |
| eager-parent | 512 | +7.22% [+2.30%, +11.18%] | -0.49% [-1.59%, +0.78%] | 366.1 / 142.6 | 0.0 / 0.0 |
| eager-submission | 64 | +11.28% [-5.09%, +28.23%] | +15.05% [+11.39%, +17.88%] | 237.5 / 36.4 | 193.5 / 31.1 |
| eager-submission | 512 | -0.87% [-4.68%, +6.41%] | +9.54% [+4.93%, +12.78%] | 351.7 / 135.0 | 322.4 / 107.5 |

Raw BAL provider fetches are approximately 6–13 µs in these warm-byte controls;
decode is separately timed and included in total acquisition. Eager-submission
repeat overhead increases to approximately +15% at 64 slots and +10% at 512.
Lazy/eager-parent retain the local per-parent view and incur no repeat fetch/decode.

## Bottlenecks and memory

In normal-cache primary 512/full-overlap runs, provider-only first EVM execution
stage medians are approximately 2.0–2.1 ms and state-root calculation 4.0 ms.
On repeats, execution is about 0.52 ms and root calculation 2.0–2.2 ms. BAL removes
covered slot calls but does not remove trie/root work; the EVM-stage reduction
needed to pay for acquisition is not observed consistently with warm MDBX state.
Stage medians are measured independently and should not be added to reconstruct
end-to-end medians. Execution includes several operations, so these timings do
not isolate provider slot time.

Whole-node RSS is roughly 2.4–2.6 GiB; enabled-minus-baseline median RSS varies
from about -44 to +41 MiB across normal full-overlap runs and reverses with node
roles. This does not identify incremental BAL memory. The shared decoded BAL LRU
reports identical retained bytes on both nodes: median 2,485,544 bytes for the
64-slot full-overlap matrix and 16,691,816 for 512 slots. Both nodes import the
same parent stream, which already populates this shared cache. With the LRU
disabled its gauge is zero, but the validation per-parent Arc can still retain
one decoded BAL until that parent's read cache is replaced. The measurements do
not isolate that pinned allocation; no incremental-memory win is claimed. CSVs
retain each node's RSS and shared-cache gauge to make the limitation reviewable.

## Verification and scope

- Broader RPC/core/ETH-cache library suite: **366 passed**, zero failed; one
  manual MDBX benchmark intentionally skipped. Targeted eager/adapter/cache
  suite: **49 passed**, zero failed. These counts overlap.
- Scoped no-default-feature clippy passed with warnings denied, including
  Ethereum add-on wiring. CLI reference regenerated; nightly formatting passed.
- Nine timing runs: **32,400 valid validation calls** and **10,800 identical
  invalid rejections**, excluding smoke runs. Valid siblings have different
  executed balances/state roots, independently derived from Ethereum account
  proofs, signed transactions and rebuilt BALs. Wrong-root and unpaid-bid
  rejection pairs are checked outside timing.
- Every run uses provider-only versus one BAL mode with the exact same binary
  and payloads, randomized pair order, idle-process checks, and no simultaneous
  build/test workload. Root-result caching is disabled throughout. Normal runs:
  64/512 slots × 0/100% overlap × 100 parents × five changed-fee repeats, seeds
  1729/2026. Decoded-LRU-disabled runs: 64/512 × 100% × 50 parents × five repeats,
  seed 314159. Bootstrap resamples whole parents (10,000 samples), preserving
  correlated repeats rather than treating them as independent observations.
- Actual local Amsterdam RETH nodes on a two-core, 8 GiB shared container;
  optimized Rust 1.99 release build, no default/JIT features, LTO disabled,
  16 codegen units. Same measured source commit
  `353e99a26d8851e93694a693b0ce040dca5858db`; binary SHA-256
  `5ca052f1c7ee779abce2833e5abef7d693a399eb1d66af7ffa00527fc17c5bd8`.

This establishes local correctness and read reduction in the tested cases. It
covers the requested lifecycle/fallback cases through regression tests, not
production replay. Full default/JIT-feature validation, representative production
submission traffic, cold storage behavior and isolated pinned-memory allocation
remain unverified. The next evidence needed for a production benefit is a real
replay corpus with measured parent-write/child-read overlap and slower provider
reads; none of the local results justifies enabling BAL globally for latency.

## Reproduce and inspect

```sh
BINDGEN_EXTRA_CLANG_ARGS=-I/usr/lib/gcc/x86_64-linux-gnu/14/include \
CARGO_BUILD_JOBS=2 CARGO_PROFILE_RELEASE_LTO=false \
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
cargo build --release -p reth --bin reth --no-default-features

python3 scripts/bench_flashbots_parent_bal_paired.py \
  --binary target/release/reth --output-dir /tmp/bal-eager-parent \
  --optimization bal --bal-mode eager-parent --bal-cache normal \
  --slots 64 512 --overlaps 0 100 --samples 100 --repeats 5 \
  --seed 1729 --vary-gas-price
```

Use `--bal-mode lazy` or `eager-submission` for the other strategies. Reverse
with `--reverse-nodes --seed 2026`. Disabled-LRU controls use `--bal-cache disabled
--overlaps 100 --samples 50 --seed 314159`. The harness requires eth-account,
rlp, trie and sortedcontainers Python packages. Each output directory must be new.

Committed summaries include latency, provider calls, fetch/decode, stages, trie
counters, RSS and build identity. Paired CSVs retain individual parent hashes,
roots, timings and counters; full workload JSON remains in the local scratch
output directories.

| Strategy | Primary normal | Reversed normal | Decoded LRU disabled |
|---|---|---|---|
| lazy | [JSON](flashbots-parent-bal-lazy-primary-results.json) / [CSV](flashbots-parent-bal-lazy-primary-pairs.csv) | [JSON](flashbots-parent-bal-lazy-reversed-results.json) / [CSV](flashbots-parent-bal-lazy-reversed-pairs.csv) | [JSON](flashbots-parent-bal-lazy-cold-results.json) / [CSV](flashbots-parent-bal-lazy-cold-pairs.csv) |
| eager-parent | [JSON](flashbots-parent-bal-eager-parent-primary-results.json) / [CSV](flashbots-parent-bal-eager-parent-primary-pairs.csv) | [JSON](flashbots-parent-bal-eager-parent-reversed-results.json) / [CSV](flashbots-parent-bal-eager-parent-reversed-pairs.csv) | [JSON](flashbots-parent-bal-eager-parent-cold-results.json) / [CSV](flashbots-parent-bal-eager-parent-cold-pairs.csv) |
| eager-submission | [JSON](flashbots-parent-bal-eager-submission-primary-results.json) / [CSV](flashbots-parent-bal-eager-submission-primary-pairs.csv) | [JSON](flashbots-parent-bal-eager-submission-reversed-results.json) / [CSV](flashbots-parent-bal-eager-submission-reversed-pairs.csv) | [JSON](flashbots-parent-bal-eager-submission-cold-results.json) / [CSV](flashbots-parent-bal-eager-submission-cold-pairs.csv) |

## Test ledger

40. Existing targeted RPC/core checks passed: **43 passed**, 0 failed (37.429 s).
    Eager mode wiring and CLI opt-in/exclusion parse tests compile. Added three
    asynchronous preload regressions afterward; these are next to execute.

41. New preload test compilation caught a TransactionId constructor mismatch in
    its child-write fixture. Corrected it. Added separate shared-cache fetch/decode
    timers and explicit builder-mode reset before the next regression run.

42. Targeted nextest: **49 passed**, 0 failed, 318 skipped (24.095 s), including
    the three eager preload/absence/commit regressions, mode parsing, existing
    lifecycle/root comparisons and shared-cache budget/concurrency checks.

43. Affected-package clippy passed with warnings denied (1m 28s), including
    Ethereum add-on wiring, RPC/core and ETH-cache libraries/tests. No eager-only
    worker hop is required. Beginning the broader library suite before timing.

44. Broader RPC/core/ETH-cache library nextest passed: **366 passed**, 0 failed,
    one intentionally skipped manual MDBX benchmark (213.125 s). Beginning the
    optimized build; no build/test work will run during timed node comparisons.

### Node comparison protocol

Same optimized artifact in both modes. Each run compares one BAL strategy against
provider-only validation using identical submissions, randomized pair order,
independently verified sibling roots and changing transaction fees. Warmup, block
import, proof construction, metrics, RSS and analysis are outside timing. Root
result reuse stays disabled. Repeat-provider calls must be zero in all modes;
repeat BAL acquisitions must be zero for lazy/eager-parent and one per request
for eager-submission. Repeat each normal-cache strategy with reversed node roles
and a different seed, plus disabled-decoded-LRU controls to expose fetch/decode.

45. Optimized build succeeded (11m 24s); CLI reference regenerated. Eager-parent
    actual-node smoke passed **64 timed valid submissions** and **32 matching
    invalid rejections** across 64/512 slots and 0/100% overlap. Counter assertions
    verify one BAL acquisition per parent, zero repeat acquisitions and the
    expected hit/fallback counts. No smoke performance claim is made.

46. Eager-submission node smoke with the decoded BAL LRU disabled passed
    **64 timed valid submissions** and **32 matching invalid rejections**. Every
    repeat acquired BAL again while EVM provider reads stayed zero. Separate
    fetch/decode measurements are present; no smoke latency claim is made.

47. Lazy shared-ETH-cache node smoke passed **64 timed valid submissions** and
    **32 matching invalid rejections**, with the same workloads as eager modes.
    Expected covered-slot hits and zero repeat acquisitions/read calls all match.
    Beginning normal-cache paired timing at 64/512 slots, 0/100% overlap, 100
    parents per case and five changing-fee siblings; role reversal follows.

48. Eager-parent primary paired run passed **4,800 timed valid submissions**
    and **1,600 matching invalid rejections**. All four first-request latency
    intervals include zero, including +3.42% [-1.08%, +8.49%] at 512 slots/full
    overlap. Covered slot calls drop 512 → 0; repeat BAL acquisitions and EVM
    provider calls are zero. Repeated 512/full-overlap change is -2.77%, but
    requires reversed-role verification before attributing it to the strategy.

49. Reversed eager-parent run passed **4,800 timed valid submissions** and
    **1,600 matching invalid rejections**. All first-request intervals again
    include zero. At 512/full overlap: first +0.88% [-4.24%, +4.94%], repeats
    -0.55% [-4.86%, +2.67%]. The primary repeat improvement does not reproduce at
    full overlap; no reliable upfront-per-parent latency win is established.

50. Eager-submission primary run passed **4,800 timed valid submissions** and
    **1,600 matching invalid rejections**. Five repeat BAL requests per parent
    occur despite zero repeat EVM provider calls. At 512/full overlap: first
    +3.20% [+0.47%, +8.88%], repeats +3.77% [+2.22%, +6.35%]. Cached acquisition
    costs approximately 94 µs per repeat. Reversed-role replication follows.

51. Reversed eager-submission run passed **4,800 timed valid submissions** and
    **1,600 matching invalid rejections**. Repeat medians regress in all four
    cases, with intervals above zero. At 512/full overlap: repeats +1.17%
    [+0.34%, +2.11%], versus +3.77% in the primary run. First-request intervals
    include zero in the reversal. No reliable eager-submission speedup is found.

52. Lazy primary comparison passed **4,800 timed valid submissions** and
    **1,600 matching invalid rejections**. All first-request intervals include
    zero; at 512/full overlap, first +2.19% [-3.33%, +5.77%]. Repeated calls make
    zero BAL queries/provider reads. The repeat -4.41% result requires node-role
    verification because no BAL read savings occur on these cached repeats.

53. Reversed lazy run passed **4,800 timed valid submissions** and **1,600 matching
    invalid rejections**. At 512/full overlap: first +3.72% [+0.40%, +8.94%],
    repeats -1.22% [-2.65%, +0.22%]; primary repeat improvement does not reproduce.
    All six normal-cache runs total **28,800 timed valid submissions** and
    **9,600 matching invalid rejections**. No BAL strategy demonstrates a
    replicated latency improvement. Disabled-decoded-LRU controls are next.

54. Eager-submission with decoded BAL LRU disabled passed **1,200 timed valid
    submissions** and **400 matching invalid rejections**. Repeats regress
    +15.05% [+11.39%, +17.88%] at 64 slots and +9.54% [+4.93%, +12.78%] at 512.
    Repeat decode medians are approximately 31 and 107 µs, respectively; warm
    state/raw bytes are retained, so this is decoded-cache-cold, not disk-cold.
    Checking the once-per-parent variants under the same cache setting next.

55. Eager-parent with decoded BAL LRU disabled passed **1,200 timed valid
    submissions** and **400 matching invalid rejections**. First requests
    regress +16.95% [+1.37%, +25.36%] at 64 slots and +7.22% [+2.30%, +11.18%]
    at 512. Repeats acquire/decode no BAL; 512 repeat change is -0.49%
    [-1.59%, +0.78%]. Covered first-slot calls still drop to zero. This control
    supports retaining the per-parent view to avoid repeat acquisition cost.

56. Lazy with decoded BAL LRU disabled passed **1,200 timed valid submissions**
    and **400 matching invalid rejections**. First paired medians are +10.86%
    [-0.38%, +36.81%] at 64 slots and +7.08% [-2.62%, +19.14%] at 512. Repeat
    changes differ by case (-5.78% and +8.99%) despite zero repeat BAL loads and
    provider calls. This single-role control does not establish a lazy speedup.
    Nine completed timing runs total **32,400 valid** and **10,800 matching
    invalid** validation calls; final artifact/report verification follows.

57. Final artifact audit passed for all **2,700 paired parents**: CSV-derived
    latency changes reproduce the summaries; provider/load/decode counters and
    five distinct sibling roots per parent match expectations. Raw rejection
    records contain the independently expected root/payment errors, and all
    root-result-cache counters are zero. Python compilation, nightly formatting
    and diff whitespace checks passed. All timed nodes have stopped. Final report
    distinguishes proven read reduction from unproven production latency benefit.
