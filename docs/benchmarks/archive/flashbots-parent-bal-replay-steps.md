# Parent BAL: concrete next experiment

This is the setup/replay procedure, not a claim that a representative replay has
already run. The new replay utility is checked with a mock RPC server. The earlier
synthetic node results are in [the brief review](../flashbots-parent-bal-review.md).

## 1. Obtain the data before renting a server

Ask Matt/client operators for one compatible replay bundle:

- A **cleanly stopped RETH database snapshot**, including static files, RocksDB
  and configuration, with retained parent states and raw parent BALs for the
  sampled range. Supply exact client commit, chain/genesis/fork configuration,
  snapshot head hash and restore instructions. A live copy of database files is
  not a consistent snapshot.
- Full original `flashbots_validateBuilderSubmissionV6` JSON-RPC request envelopes
  as `submissions.ndjson`: one complete request per line, including auxiliary
  parameters, execution requests, blobs/proofs and the submitted child's BAL.
  Start with requests known to validate successfully. Preserve chronological
  order within each parent group; include different transaction/read sets.
- Prefer at least 500 distinct parents with 5–20 actual candidates per parent;
  this is an initial sampling target, not a power calculation or proof threshold.
  Also identify reorged-parent and lifecycle fixtures for separate correctness
  runs. Include known expected responses and roots.

The public relay endpoint `builder_blocks_received` explicitly returns bids
without execution payloads. It cannot supply this complete corpus:
[Flashbots relay specification](https://github.com/flashbots/relay-specs/blob/main/apis/relay/data/builder_blocks_received.yaml).

BAL availability must be demonstrated. The
[EF deployment announcement](https://blog.ethereum.org/en/2026/09/17/glamsterdam-testnet-announcement)
scheduled Sepolia's fork for October 6, 2026; mainnet activation was unannounced
when checked. A post-fork Sepolia/devnet bundle can exercise BAL; a mainnet
snapshot does not automatically supply committed parent BALs. Our optimization
deliberately bypasses parents without BAL commitments. Testnet results also do
not establish mainnet workload performance.

If there is no bundle, the independent route is syncing a supported Sepolia RETH
plus a compatible consensus client and recording/generating full valid candidate
submissions. See [RETH node operation](https://reth.rs/run/ethereum/) and the EF
client compatibility table. Syncing the node alone does not generate competing
builder submissions; that still requires builder/workload tooling. We have not
verified an available public competing-submission corpus.

## 2. Choose one dedicated benchmark server

Suggested starting specification: Ubuntu 24.04 x86-64, eight fast CPU cores,
32–64 GiB RAM and local TLC NVMe. These are proposed benchmark resources, not
measured minimums. Size disk from the bundle's actual expanded size, plus its
compressed download, a working copy if needed, build space and headroom. Do not
assume a fixed 2 TB disk is enough. The official
[hardware guide](https://reth.rs/run/system-requirements/) emphasizes disk quality;
its chain-size examples are dated and are not a current sizing guarantee.

Use one benchmark node process at a time on a copy of the frozen database. No
validator keys, staking deposits or continuously running consensus client are
needed during offline replay. Avoid parallel builds, sync and unrelated RPC load.

## 3. Build the branch once

On the benchmark server, with Rust installed using the official
[source-build instructions](https://reth.rs/installation/source/):

```sh
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libclang-dev clang cmake libssl-dev git curl jq python3
git clone --branch perf/flashbots-lazy-parent-bal https://github.com/Oliverpt-1/reth.git reth-bal
cd reth-bal
git rev-parse HEAD > benchmark-source-commit.txt
cargo build --locked --release -p reth --bin reth --no-default-features
./target/release/reth --version > benchmark-version.txt
sha256sum target/release/reth > benchmark-binary.sha256
```

Use the same binary and build configuration for both modes. This command matches
the earlier no-default-feature scope; repeat with the operator's deployment build
before making a production claim. If the database format/chain rules do not match
this branch, stop and align the code and snapshot; do not assume a snapshot from
the latest release can be opened safely by an older commit.

Current online [snapshot instructions](https://reth.rs/run/storage/snapshots/)
offer component-based downloads. A snapshot must retain the sampled states/BALs
and match this client's storage format. A minimal/latest snapshot is not
automatically suitable for historical replay.

## 4. Restore and start the baseline offline

Restore the bundle to `/srv/bal-bench/node`, place the matching chain specification
at `/srv/bal-bench/chain.json`, and the corpus at
`/srv/bal-bench/submissions.ndjson`. These are paths to supplied inputs, not files
already provisioned by this guide. For built-in Sepolia, use `--chain sepolia`
instead of a custom chain file. Keep the restored database configuration identical
between modes and verify pruning will not discard the sampled states.

Run the following from the clone in a dedicated terminal:

```sh
./target/release/reth node \
  --chain /srv/bal-bench/chain.json \
  --datadir /srv/bal-bench/node \
  --http --http.addr 127.0.0.1 --http.port 8545 \
  --http.api eth,debug,flashbots \
  --metrics 127.0.0.1:9001 \
  --rpc.eth-proof-window 100000 \
  --disable-discovery --disable-dns-discovery \
  --max-outbound-peers 0 --max-inbound-peers 0 \
  --ipcdisable
```

Do not connect a consensus client or another Engine API writer during the replay.
Do not use `--dev`; it would change the chain/workload. Do not enable the separate
state-root result cache. HTTP and metrics stay on loopback.

This branch uses `rpc.eth-proof-window` for validation age as well as proofs.
100,000 accommodates a bounded historical sample; choose at least
`snapshot_head_number - oldest_parent_number`, within the CLI maximum. Raising
the window does not restore pruned historical state. The node head must cover the
sampled parents. Inspect the corpus and verify every requested parent is present.

## 5. Preflight before timing

In another terminal, extract one original valid request and its parent hash:

```sh
head -n 1 /srv/bal-bench/submissions.ndjson > /srv/bal-bench/first-request.json
jq -r '.params[0].execution_payload.parent_hash' /srv/bal-bench/first-request.json
```

Use that actual hash as `PARENT_HASH` below; keep the quotes:

```sh
curl -sS http://127.0.0.1:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByHash","params":["PARENT_HASH",false]}'
curl -sS http://127.0.0.1:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":2,"method":"eth_getBlockAccessListRaw","params":["PARENT_HASH"]}'
curl -sS http://127.0.0.1:8545 -H 'Content-Type: application/json' \
  --data-binary @/srv/bal-bench/first-request.json
```

Require a matching parent header with a BAL commitment, non-null raw BAL bytes,
and validation response `"result": null`. A JSON-RPC `error` can arrive with HTTP
200. Missing state/BAL, incompatible payload schema, old-block rejection or a
root/payment error must be resolved before timing. The raw BAL RPC name above
is verified against our pinned branch; newer clients may use a different API.

These checks warm caches. Stop the node cleanly with Ctrl-C, wait for shutdown,
then restart before collecting the first run. Use the exact same start procedure
for both modes. A restart resets process caches, not the OS page cache.

## 6. Replay the actual corpus, off and on

With the baseline restarted:

```sh
python3 scripts/replay_flashbots_validation.py \
  --corpus /srv/bal-bench/submissions.ndjson \
  --label off-1 --output /srv/bal-bench/off-1.ndjson
```

Stop cleanly, restart with the identical node command plus:

```sh
--rpc.flashbots-parent-bal-mode lazy
```

Then replay:

```sh
python3 scripts/replay_flashbots_validation.py \
  --corpus /srv/bal-bench/submissions.ndjson \
  --label lazy-1 --output /srv/bal-bench/lazy-1.ndjson
```

Pass `--pid NODE_PID` to collect whole-node RSS from `/proc/NODE_PID/status`.
The script records each request's fingerprint, parent/block/root, response,
wall-clock latency and before/after validation/ETH-cache metrics. Scrapes and
serialization are outside timing. It stops on validation failure rather than
reporting fast rejection as a performance success. Output paths must be new.

This new runner has only been mock-server tested so far; it is separate from the
synthetic benchmark generator that produced the previous measurements. Its
`first_in_group` label identifies a parent change in the input, not a guarantee
of cold physical storage. Keep parent groups intact and compare identical corpus
hashes, frozen head hashes and per-request fingerprints across runs.

## 7. Run a balanced matrix

Repeat with fresh processes in order **off → lazy → lazy → off**, using new output
files each time. Repeat balanced cycles until estimates stabilize; analyze paired
parent groups and bootstrap parents, not individual correlated siblings. Use
first-versus-repeat p50/p95 plus uncertainty, not just one overall mean.

- **Warm-state tests:** establish comparable OS-cache warming on both modes using
  the same untimed workload, then restart before the measured corpus. Note that
  startup/RPC warmup can also warm BAL; record actual acquisition/decode counters.
- **Cold-state controls:** on this dedicated benchmark host only, stop the node,
  flush dirty data and evict the OS page cache before each mode's fresh process.
  This is host-wide and does not establish that every later read stays cold.
  First validate the procedure using faults/storage-I/O measurements. Do not
  perform this on a shared production server.
- **BAL-cache controls:** vary decoded BAL availability separately from state
  warmth, using the existing decoded-LRU-disabled control where useful. Disabling
  the BAL LRU does not make provider state cold.
- **Realistic candidate variation:** inspect actual changing transaction/account/
  slot sets. Group results by newly uncached child reads covered by parent writes,
  BAL size and candidate position. Do not substitute fee-only changes for this.
- **Concurrency:** after sequential comparisons, replay observed request arrival
  timing and production concurrency. The supplied runner is sequential and does
  not implement that workload yet.

For the cold-state control, after clean shutdown on the dedicated server:

```sh
sync
printf '3\n' | sudo tee /proc/sys/vm/drop_caches >/dev/null
```

Restart and replay only after this command completes. Repeat the reset for both
off/on modes; do not clear once and compare a cold baseline with a warmed lazy
run. We have not executed this host-wide control in the shared workspace.

Do correctness reorg tests separately: import alternate parents on an isolated
devnet using matching Engine API payloads/forkchoice data and verify enabled/off
acceptance and calculated roots. A two-parent read adapter test alone is not a
live reorg test. A frozen canonical snapshot needs extra side-branch data to
replay a reorg.

## 8. Close the remaining measurement gaps

Existing metrics cover provider-call counts, BAL acquisition/fetch/decode and
validation stages. They do **not** isolate duration inside each provider/BAL read
path or memory pinned solely by validation's BAL reference. Before asserting a
specific cause, add aggregated read-duration and pinned-allocation measurements,
measure instrumentation overhead, and use a separately profiled diagnostic run.
Do not put expensive per-read histogram recording into the primary timed run.

Success evidence is matching results/roots, fewer provider calls and a repeatable
total-latency improvement for identified workload/cache conditions, with memory
cost reported. Flat or slower results remain valid findings. We have not yet run
this representative-data experiment: the database/corpus and benchmark host are
still missing.

## Tool checks completed

The sequential runner passed local mock-server checks for exact RPC parameter
forwarding, stable request fingerprints across modes, parent-group labeling,
RETH-prefixed metric capture, RSS capture, stop-on-rejection behavior, missing
metric rejection, protection against overwriting results and rejection of
unrelated RPC methods. Python compilation and documentation whitespace checks
passed. These checks establish tool behavior only, not realistic performance.
