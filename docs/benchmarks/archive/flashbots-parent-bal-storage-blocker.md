# Historical Sepolia run: storage gate

Checked 2026-10-17 at branch revision
`ba6b9e1e9bbf435112edc1a1c76c7ed9f91241b8`. This is a blocked execution
attempt, not a benchmark or a new data-source investigation.

The clean checkout was fast-forwarded using:

```sh
git fetch fork perf/flashbots-lazy-parent-bal
git merge --ff-only fork/perf/flashbots-lazy-parent-bal
df -k
ls -la /Volumes
diskutil list
diskutil apfs list
```

The existing prototype stash was preserved. `diskutil` initially failed inside
the sandbox with `Unable to run because unable to use the DiskManagement
framework.` Repeating the read-only inspection outside the sandbox succeeded.

## Observed storage

`diskutil list` reports one physical disk, internal `/dev/disk0` (1.0 TB).
No external physical drive is connected. The OBS installer and Metal toolchain
volumes are disk images, not additional physical storage. The main APFS
container has capacity 994,662,584,320 bytes and 556,332,900,352 bytes unallocated.
Multiple APFS mounts share this capacity; their free space cannot be added.

The already verified snapshot at Sepolia block 11,930,000 is
699,471,472,110 bytes compressed. Staging this archive alone exceeds available
space by 143,138,571,758 bytes, before extraction or independent treatments.
No snapshot download or extraction was started.

## Unresolved extraction and compatibility requirements

This metadata lookup failed with curl exit 56 and HTTP 404:

```sh
curl --fail --silent --show-error --max-time 30 \
  https://snapshots.ethpandaops.io/sepolia/reth/11930000/
```

The directory lookup failure does not imply the known archive is unavailable.
Expanded size remains unverified. Streaming extraction would avoid retaining
the archive but is not a demonstrated fit. Let E be the measured expanded
datadir size and H be space reserved for imports, logs and database growth.
Keeping a frozen source plus three independent full treatment copies requires
4E + H, plus 699,471,472,110 bytes if the archive is retained. Sequential
treatments can reduce the full-copy requirement to 2E + H. Filesystem clones
may reduce initial allocation, but their write growth still needs budgeting.
Neither arrangement has been shown to fit this SSD.

The source investigation identifies snapshot client
`reth/v2.7.0-3d592ec/x86_64-unknown-linux-gnu`; this checkout expects MDBX
`database.version` 2 (`crates/storage/db/src/version.rs`). The snapshot's actual
version file, other storage formats, architecture portability, pruning settings,
retained historical state and local parent BALs have not been inspected.
Matching client release labels alone do not establish compatibility. Verify on
a disposable copy before opening or migrating the preserved source.

## Execution status and next requirement

No historical payload was exported or submitted in this attempt. No baseline
acceptance, root equality, optimization hit/fallback, latency, fetch/decode or
memory criterion has passed for historical data. No treatment or reversed-role
round ran, and no synthetic run was substituted. Exact-repeat and competing
candidate conclusions remain open.

The immediate missing requirement is a storage destination with verified room
for the expanded compatible Sepolia database and treatment copies, or access
to an existing compatible database on a sufficiently provisioned host. After
that, resume the documented canonical-envelope route, including complete blob
bundles and explicitly synthetic bid metadata. Public payload and BAL access
were already established by the source investigation and were not re-counted
as progress on benchmark acceptance.
