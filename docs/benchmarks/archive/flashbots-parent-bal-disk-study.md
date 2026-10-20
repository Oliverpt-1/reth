# Generated-state disk-read validation

## Results

**Completed:** 1,152 timed V6 validations across eight parents, four modes,
three overlap levels, warm/cold conditions and both treatment orders. Every
request passed, including computed-versus-claimed state-root validation. All
expected storage hit/fallback assertions passed. No root-result cache was used.

The cold-read item is closed for **Linux guest file-cache misses during this
generated-state validation workload**. All 192 cold first requests and all 192
cold new-access siblings had positive process read bytes, major faults and
guest disk reads. All 576 warm requests and all 192 cold-condition exact repeats
had zero for those three measurements. This is stronger than restarting a node
or clearing application caches. It does not establish physical host SSD misses.

The eight frozen databases occupied 35,168,256–36,876,288 allocated bytes each,
despite MDBX's 4 GiB logical map. Controlled eviction, rather than a working set
larger than memory, creates the cold condition. Guest RAM was approximately
7.7 GiB; the container limit was 7 GiB. Storage v2 and archive mode were active.

### Fully covered first candidate

Each cell below is p50 / p95 client-observed validation latency in milliseconds.
There are 16 observations per cell: two orders for each of eight parents.
Nearest-rank p95 is therefore the maximum, not a stable tail estimate.

| Condition / request | Disabled | Lazy | Eager-parent | Eager-submission |
|---|---:|---:|---:|---:|
| Warm / first | 2.893 / 6.683 | 2.936 / 4.527 | 2.918 / 5.501 | 2.872 / 4.454 |
| Cold / first | 26.429 / 103.305 | 26.355 / 29.600 | 26.280 / 40.582 | 25.494 / 28.945 |
| Cold / new-access sibling | 14.360 / 56.758 | 14.722 / 18.151 | 15.140 / 24.152 | 14.504 / 16.535 |
| Cold / exact repeat | 1.187 / 3.540 | 1.198 / 1.591 | 1.170 / 2.478 | 1.207 / 1.419 |

For the cold first request, disabled made 64 storage-provider calls; each BAL
mode made zero and recorded 64 BAL hits. All modes still made nine provider
account calls and one code call. The new-access sibling made 64 new storage
calls, one account call and zero code calls in every mode; its exact repeat
made none. These distinct siblings exercise additional CachedReads entries.
Their new slots are deliberately uncovered, not a realistic candidate mixture.

Cold first-request execution medians were 6.101 / 0.654 / 0.515 / 0.502 ms
(disabled / lazy / eager-parent / eager-submission). State-root medians were
19.294 / 24.591 / 24.504 / 23.952 ms. Component medians are not additive.
Median process and guest-device reads were **1,785,856 bytes in every mode**,
with 436 major faults. The new-access sibling read another 1,214,464 bytes
(median) in every mode. Avoiding EVM calls did not remove the overall database
I/O; the timings are consistent with some work moving to root calculation.

Paired first-request mean deltas versus disabled, with parent-bootstrap 95%
intervals, were lazy −3.786 ms [−12.328, +1.359], eager-parent −3.068
[−13.389, +3.305], and eager-submission −5.146 [−14.136, +0.270]. None
establishes a full-overlap speedup. Lazy's forward-order delta was **+1.937 ms**
and reversed-order delta **−9.508 ms**. One 103.305 ms baseline observation
strongly affects means; it is retained, not discarded. Host scheduling and
other host activity were not instrumented, so its cause is unresolved.

### Partial and zero overlap

| Cold first-request overlap | Mode | p50 / p95 ms | Paired mean delta, 95% interval (ms) |
|---|---|---:|---:|
| 50% | Disabled | 27.370 / 36.645 | Reference |
| 50% | Lazy | 26.861 / 29.203 | −1.157 [−2.990, +0.641] |
| 50% | Eager-parent | 25.725 / 30.693 | −2.506 [−3.797, −1.042] |
| 50% | Eager-submission | 25.976 / 40.032 | −1.116 [−3.585, +1.280] |
| 0% | Disabled | 26.259 / 29.473 | Reference |
| 0% | Lazy | 24.989 / 35.293 | +0.534 [−1.950, +2.679] |
| 0% | Eager-parent | 27.060 / 30.198 | +1.862 [+0.585, +3.512] |
| 0% | Eager-submission | 26.539 / 31.998 | +1.435 [+0.048, +3.036] |

At 50% overlap, optimized first requests recorded 32 hits and 32 provider
storage calls; at zero overlap all modes made 64 provider calls. Eager-parent's
partial-overlap improvement was −0.295 ms in forward order and −4.717 ms in
reverse order. The zero-overlap eager modes were slower. These are exploratory
comparisons across many cells, without multiple-comparison adjustment; they
support neither a general speedup nor a general regression claim. All warm,
sibling and repeat cells, including uncertainty, are in the summary JSON.

### BAL costs and memory context

For fully covered cold first requests, median BAL acquisition was 71.7 µs lazy,
67.7 µs eager-parent and 68.3 µs eager-submission. It was zero on subsequent
lazy/eager-parent requests. Eager-submission reacquired on the sibling (48.0 µs)
and exact repeat (44.8 µs). Timed validations had **zero fetch/decode events**.
Separate preload medians were approximately 40–42 µs fetch and 11.5–12.5 µs
decode. Startup had already touched some metadata; these are observed preload
costs, not a worst-case cold BAL-fetch benchmark.

The shared cache's own size estimate was 16,355 bytes median in every mode for
these parents. Whole-node RSS ranged from 286.25 to 308.46 MiB across requests.
Full-overlap first-request medians were about 287 MiB cold and 299 MiB warm,
with less than 0.5 MiB between modes. RSS includes mapped state, executable code,
allocators and unrelated caches; it does not isolate incremental BAL memory.
That item is addressed by the [allocation study](flashbots-parent-bal-allocation-study.md).

Acceptance, hits/fallback, root agreement, distinct-candidate reads, guest disk
I/O, release of tracked BAL allocations, and reversed treatment order passed.
Historical replay, physical host SSD behavior, representative competing
candidates, production performance and full live-reorg coverage remain open.

Evidence: [raw requests and measurements](flashbots-parent-bal-disk-results.json.gz),
[all summary cells](flashbots-parent-bal-disk-summary.json). Recreate the summary:

```sh
python3 scripts/summarize_parent_bal_disk.py \
  docs/benchmarks/archive/flashbots-parent-bal-disk-results.json.gz \
  docs/benchmarks/archive/flashbots-parent-bal-disk-summary.json
```

## Scope and reproduction

This experiment uses generated Amsterdam blocks, not historical Sepolia data.
The database runs on the dedicated Colima Linux VM's ext4 Docker volume. The
VM has four CPUs and 8 GiB RAM; the container is limited to four CPUs and 7 GiB.
The host is an Apple M4 Max with 36 GB RAM. The VM's 100 GB data disk had more
than 80 GB free during setup. There is no swap. The host can cache the virtual
disk: guest disk-read measurements do not establish physical SSD cache misses.

The same ARM64 Linux release binary runs every mode. Its SHA-256 is
`85709ac78c5d8b7015af4bf9b943a6622af9305652fdae1f4ba3cc62fd0216a5`;
embedded source revision is `4a0488c3baba029eb11d997c19bb7dc8acd98f07`.
The compiler was rustc **1.98.1**, LLVM 22.1.8, aarch64-unknown-linux-gnu
(the mutable image tag says 1.98; the recorded digest and compiler identify it).
Production validation/cache code is unchanged from the earlier `523afb5cd3`
binary. The test harness is identified separately in the results.

```sh
colima start --cpu 4 --memory 8 --disk 100
# Build inside rust:1.98-bookworm, ARM64, with clang/cmake/pkg-config/libssl-dev.
CARGO_BUILD_JOBS=4 CARGO_PROFILE_RELEASE_LTO=false \
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
cargo build --locked --release -p reth --bin reth --no-default-features

# In the isolated VM container; /work is a Docker volume, /src the repository.
BENCH_SOURCE_REVISION=4a0488c3ba /work/venv/bin/python \
  /src/scripts/bench_parent_bal_disk.py \
  --binary /work/reth/target/release/reth \
  --output-dir /work/disk-full --parents 8 \
  --allow-isolated-vm-cache-drop
```

Python dependencies are `eth-account==0.14.0` and `trie==3.1.0` (the latter is
imported by the existing paired harness). Image digest:
`sha256:93ce27a88655056a51dbdd8f5f2d7ddc071c7b0070fb288a37b5a285fc83971e`.
The run container uses `--privileged --memory 7g --cpus 4` solely inside this
dedicated VM. The initial unprivileged container had read-only
`/proc/sys/vm/drop_caches`; the VM container provides the required access.
Do not run this cache-eviction procedure on a shared Linux host.

## Workload and controls

The genesis has 262,144 initialized storage slots in one contract. Each signed
transaction reads and updates 64 slots, spaced 1,024 keys apart to cross storage
pages. Each parent is persisted with engine persistence threshold and memory
buffer both zero. After shutdown, sparse copies preserve its exact starting
database. Each child is mined independently from that parent, yielding real
transactions, roots and BALs. V6 bid metadata is synthetic and unsigned, as in
the existing harness; these are local validation requests, not relay bids.

First candidates overlap 100%, 50% or 0% with parent writes. The next sibling
accesses 64 additional slots, disjoint from both parent and first candidate.
The third request exactly repeats that sibling. Parent groups use separate
storage keys. They belong to one generated chain, so confidence intervals are
descriptive for this workload, not independent samples of production traffic.

Every candidate must pass all modes before timing. Timed acceptance checks the
claimed state root against Reth's computed root; a mismatch returns an error.
The harness asserts expected provider/BAL storage counts and absence of
root-result-cache use. The flag enabling that cache is never supplied.
Assertions also require zero BAL fetch/decode events inside timed validation.

Each treatment uses a fresh process and copied parent database. The warm control
reads allocated database extents into the guest file cache. The cold condition
syncs files and drops the isolated guest's file cache before starting the node.
Executable/shared-library files are then warmed explicitly, and the parent's
decoded BAL is preloaded through `eth_getBlockAccessListRaw` outside timing.
That API calls the actual decoded ETH cache. Its fetch/decode measurements are
recorded separately. Cold state does not imply a cold BAL cache.

Node startup and BAL preload inevitably warm some database pages. The evidence
is therefore measured faults/I/O during each validation, not a claim that every
provider lookup starts cold. `/proc/<pid>/io`, major/minor faults, MDBX mapped
RSS and guest device read counters are sampled immediately around the RPC.
These measurements exclude preparation I/O. Process/device totals include both
execution and root calculation; they do not attribute every byte to an EVM
storage lookup. Existing stage timers separately report execution and roots.

All four modes run in alternating order by parent, then in reversed order;
warm/cold order is reversed too. There are no persistent producer/follower
roles during measurement: every treatment receives a fresh equivalent process.
This reverses treatment order rather than reusing asymmetric node roles.
State generation, builds, allocation profiling and candidate preflight finish
before latency timing begins. RSS is whole-node context, not isolated BAL memory.

The summarizer pairs treatments within parent/order and averages the two orders
before resampling parent groups (10,000 bootstrap draws, seed 7928). Exact
repeats and sibling requests are reported separately, never counted as
additional independent parents.

## Setup failures resolved

- The first Linux build failed because the source copy excluded `.git`, which
  the node-core build metadata generator needs. Copying repository metadata
  into the isolated build volume allowed the same build command to finish.
- Ordinary copies expanded MDBX's sparse 4 GiB logical map. The final harness
  uses `cp --sparse=always --reflink=never` and reports allocated bytes.
- An initial smoke run failed in `base.mine` with
  `TimeoutError: dev block mining timed out`. RPC had become available before
  the dev miner subscribed to pool events. Producer startup now waits before
  sending the transaction; this wait is outside measurements.

The preliminary one-parent smoke measurements are diagnostic checks. The final
matrix and its recorded harness hash are the performance evidence.
