# Public data routes for the parent BAL experiment

Checked October 17, 2026. This is source research and public data verification,
not a historical execution benchmark. The existing measured results remain in
[the main review](../flashbots-parent-bal-review.md).

## What the earlier readiness check missed

The tools remain useful. Mempool Dumpster's mainnet-only coverage and the
default RETH snapshot registry do not describe all public Ethereum data.
Confirmed Sepolia payloads provide real transactions with real canonical roots.
We can use those before implementing a historical builder. They do not reproduce
private bundles, unsuccessful bids or several different submissions per parent.

## 1. Sepolia snapshot and complete canonical payloads

[EthPandaOps snapshots](https://ethpandaops.io/data/snapshots/) documents external
datadir archives, including Sepolia. Direct checks returned:

| Item | Verified result |
|---|---|
| `https://snapshots.ethpandaops.io/sepolia/reth/latest` | HTTP 200, height 11,930,000 |
| `_snapshot_web3_clientVersion.json` at that height | `reth/v2.7.0-3d592ec/x86_64-unknown-linux-gnu` |
| `_snapshot_eth_getBlockByNumber.json` | HTTP 200; 265 transactions and a nonempty BAL commitment |
| `snapshot.tar.zst` HEAD | HTTP 200; 699,471,472,110 compressed bytes, approximately 651.4 GiB |

The archive itself was not downloaded. The compressed size already exceeds the
Mac's roughly 528 GiB of free disk. Expanded size and operating headroom must be
budgeted separately. Streaming extraction avoids holding a separate compressed
copy, but does not establish that the expanded database fits. PublicNode also
[lists Ethereum Sepolia RETH snapshots](https://publicnode.com/snapshots).

Two public APIs served matching data for this snapshot's head:

- Execution: `https://ethereum-sepolia-rpc.publicnode.com`
- Consensus: `https://ethereum-sepolia-beacon-api.publicnode.com`

For execution block
`0x963c8c0acd844a863e678f42084401f747e7d2d1a18697ab3e37ec248d1e458e`
at height 11,930,000, the checks fetched:

1. `eth_getBlockByHash(hash, false)`.
2. `eth_getBlockAccessListRaw(hash)`: 89,870 bytes, 637 account entries. Its Keccak
   hash equals the header's `blockAccessListHash`.
3. `GET /eth/v2/beacon/blocks/11371158` and
   `GET /eth/v1/beacon/headers/11371158`.
4. `GET /eth/v1/beacon/execution_payload_envelopes/11371158`: the complete Gloas
   envelope, containing 265 signed transaction byte strings, withdrawals,
   execution requests, slot number and BAL bytes.

The envelope's execution block hash, BAL bytes, beacon root and parent beacon
root match the other responses. All 265 signed transaction hashes match the
execution RPC response, and an independently constructed transaction trie
matches the header's transaction root. These checks do not execute transactions
or establish a matching post-state root. The singular `execution_payload_envelope` route
returned 404; the plural route above worked on the checked Lighthouse endpoint.
API availability can change. These are public API observations, not instructions
to expose a local Engine API or submit test requests to a public validation node.

### What remains before this becomes a benchmark

1. Acquire or sync a compatible Sepolia RETH database with sufficient storage.
   Verify actual state access at the chosen parent and nonempty local BALs whose
   hashes match header commitments. A downloaded header or snapshot listing
   does not prove those are retained. Keep a frozen baseline and independent
   writable copies or filesystem clones for each treatment.
2. Choose a recent sequence within the database's retained history, or import
   children after the snapshot head in a separate preparation phase. A recent
   full snapshot is not automatically an archive of old state. Fetch complete
   envelopes by canonical beacon root and verify them against execution headers.
3. Convert the payload into the request format used by
   `scripts/bench_flashbots_parent_bal.py::submission`, retaining real signed
   transactions, roots, slot number, child BAL, parent beacon root and execution
   requests. Use explicit test bid metadata, not purported original Flashbots
   bids. The current branch permits zero-value test bids. No exporter was built
   or validated against historical state in this investigation.
4. Handle blobs correctly. The downloaded sample and eight following execution
   blocks all have nonzero blob gas. An empty blob bundle cannot validate them.
   Either locate blob-free blocks for a scoped first experiment, or acquire and
   reconstruct the matching blobs and cell proofs. The consensus envelope alone
   does not include a complete V6 blob bundle. Do not alter the signed
   transactions or committed block to sidestep that requirement.
5. Invoke `flashbots_validateBuilderSubmissionV6` on the local branch binary,
   with optimization disabled, lazy and eager modes. Confirm accepted results,
   expected state roots and actual parent-BAL hit counters before timing. Engine
   `newPayload` replay alone bypasses this Flashbots-specific adapter.
6. Compare first requests and exact repeats, with root-result caching disabled.
   Report cache setup, provider calls, acquisition/fetch/decode cost, stage and
   total latency, and memory. Add distinct sibling candidates to test realistic
   same-parent read-cache growth. Canonical history supplies one winner per
   parent, not that competing-candidate distribution.

A patched rbuilder and V6 test relay are therefore optional for the initial
confirmed-block experiment. They remain relevant if we want to generate and
submit different builder candidates through that pipeline.

## 2. Xatu BAL data for screening useful workloads

[Xatu's access guide](https://ethpandaops.io/data/xatu/getting-started/) documents
public Parquet downloads as well as restricted hosted ClickHouse access.
[Its schema](https://github.com/ethpandaops/xatu-data/blob/c0935f0bf5f380575415ef905b4f679d9772961a/schema/canonical_beacon_.md#canonical_beacon_block_access_list)
lists Sepolia BAL coverage beginning October 6. Direct range requests returned
HTTP 206 and Parquet `PAR1` magic for:

```
https://data.ethpandaops.io/xatu/sepolia/databases/default/canonical_beacon_block_access_list/2026/10/14.parquet
https://data.ethpandaops.io/xatu/sepolia/databases/default/canonical_beacon_block_access_list_summary/2026/10/14.parquet
```

The responses reported full object sizes of 226,610,682 and 964,005 bytes. Only
the first four bytes were fetched; no rows were queried. An initial analysis
could join actual parent/child hashes and compare parent storage writes with
child accesses. That estimates possible coverage, not actual provider-call
savings: child write order and CachedReads affect the latter.

Xatu also advertises Sepolia mempool events, and an October 14 file passed the
same range check. Its inspected schema lacks signed transaction bytes, calldata
and signature fields, so it is not a drop-in rbuilder replay corpus. Likewise,
the inspected gossip-envelope SQL table contains propagation metadata rather
than a complete payload. Fetch full envelopes from the beacon API instead.

## 3. Official stateful Amsterdam benchmarks

[Benchmarkoor's configuration repository](https://github.com/ethpandaops/benchmarkoor-tests)
contains RETH configurations, deterministic state generation and Amsterdam EEST
fixtures. The checked
[stateful release](https://github.com/ethpandaops/benchmarkoor-tests/releases/tag/eest-payloads-state-actor-v2-amsterdam-stateful-c4a18e34-20261014-021844)
has a public manifest and a 462,463,301-byte fixture tarball. The manifest reports
692 filled fixtures, 64 failed fixtures, and Geth prestate root
`0x3de7c47e447077459c36be878767de7d135f1371924551ac295dd9ab8384c466`.

The configuration's `350GB` target is not a disk guarantee: this manifest reports
535,730,007,536 bytes for its Geth database, before working copies and other
overhead. No matching RETH prestate was downloaded or generated here. Its state
root, genesis, fork schedule and fill sequence must match before fixtures can be
reused. Reducing state size requires refilling the tests, not replaying the old
fixtures against a different root.

This route can test larger state and varied access patterns without relying on
live submissions. It remains synthetic, and the default runner uses the Engine
API. Adapt the workload to Flashbots validation, or benchmark the adapter's
execution layer directly with equivalent checks. Ensure the chosen parent/child
sequence actually has write/read overlap; a state-access stress test does not
automatically provide it.

## 4. Updated builder tooling

[Buildoor](https://github.com/ethpandaops/buildoor) is a public devnet/testnet
builder with Gloas ePBS support. Its payload converter carries BAL bytes and its
API can export payload artifacts. It needs an EL Engine API, consensus client
and test builder setup. It is an alternative for generating varied native-fork
payloads, not a ready-made Flashbots V6 historical replay harness. Conversion
and a matching state database are still necessary.

Inspected repository revisions:

- `benchmarkoor-tests`: `16fe73e1473e4ea03386c9f3ea5da5c52a2c750c`.
- `buildoor`: `9f07c677fabff351acce5814c44dcbccaee8130d`.
- `xatu-data`: `c0935f0bf5f380575415ef905b4f679d9772961a`.

## Recommendation and limits

Start with canonical Sepolia payload replay on a suitably provisioned local
RETH database. It uses real chain transactions and committed BALs, avoids the
mock-root historical-builder path, and directly answers whether this adapter
helps that workload. First resolve state retention and complete blob bundles.
Use Xatu to identify promising overlap, and stateful fixtures as a separate
controlled stress experiment. Neither replaces an eventual competing-submission
corpus, a live reorg test or production performance measurements.

This investigation verified public data access and consistency only. It ran no
historical EVM execution, produced no accepted historical V6 submissions and
adds zero validation latency, provider-read or memory samples. The public RPC
BAL fetch is not a hit in our optimization. Earlier 403 responses describe
those requests, not permanent unavailability; the successful requests are
recorded in [the evidence JSON](flashbots-parent-bal-data-sources.json).
