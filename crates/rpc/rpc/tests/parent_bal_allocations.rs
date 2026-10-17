//! Explicit allocation experiment using the production adapter and ETH cache.
//!
//! Run alone: cargo test -p reth-rpc --test parent_bal_allocations allocation_lifetimes
//! -- --ignored --exact --nocapture --test-threads=1.

#[path = "../src/validation/parent_bal.rs"]
mod parent_bal;

use alloy_eip7928::bal::DecodedBal;
use alloy_primitives::{Address, Bytes, B256, U256};
use parent_bal::{committed_loader, AsyncBalLoader, BalLoader, LazyParentBal, ParentBalDb};
use reth_ethereum_primitives::EthPrimitives;
use reth_provider::test_utils::create_test_provider_factory;
use reth_rpc_eth_types::{EthStateCache, EthStateCacheConfig};
use reth_tasks::Runtime;
use revm::{
    database::EmptyDB,
    state::bal::{AccountBal, Bal, BalWrites, BlockAccessIndex},
    DatabaseRef,
};
use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};

struct TaggedAllocator;

#[global_allocator]
static ALLOCATOR: TaggedAllocator = TaggedAllocator;

// Tags travel with allocations, so a free on an ETH service worker is counted too.
thread_local! {
    static TAG: Cell<usize> = const { Cell::new(0) };
}
static LIVE: [AtomicUsize; 5] = [const { AtomicUsize::new(0) }; 5];
static PEAK: [AtomicUsize; 5] = [const { AtomicUsize::new(0) }; 5];

fn tagged_layout(layout: Layout) -> (Layout, usize) {
    Layout::new::<usize>().extend(layout).unwrap()
}

// SAFETY: The original alignment is preserved by Layout::extend. Each pointer
// returned to the caller has a private tag prefix, recovered with the same layout
// during deallocation. System owns the full allocation, including the prefix.
unsafe impl GlobalAlloc for TaggedAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let (full, offset) = tagged_layout(layout);
        // SAFETY: full is a valid allocation layout.
        let ptr = unsafe { System.alloc(full) };
        if ptr.is_null() {
            return ptr;
        }
        let tag = TAG.try_with(Cell::get).unwrap_or(0);
        // SAFETY: The prefix has space and alignment for usize; offset is within full.
        unsafe {
            ptr.cast::<usize>().write(tag);
        }
        if tag != 0 {
            let live = LIVE[tag].fetch_add(layout.size(), Ordering::SeqCst) + layout.size();
            PEAK[tag].fetch_max(live, Ordering::SeqCst);
        }
        // SAFETY: offset designates the aligned caller allocation.
        unsafe { ptr.add(offset) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        let (full, offset) = tagged_layout(layout);
        // SAFETY: GlobalAlloc callers supply the original pointer and layout.
        let base = unsafe { ptr.sub(offset) };
        // SAFETY: This prefix was initialized by alloc.
        let tag = unsafe { base.cast::<usize>().read() };
        if tag != 0 {
            LIVE[tag].fetch_sub(layout.size(), Ordering::SeqCst);
        }
        // SAFETY: base and full match the original System allocation.
        unsafe { System.dealloc(base, full) };
    }
}

fn track<T>(tag: usize, work: impl FnOnce() -> T) -> T {
    assert_eq!(TAG.with(|value| value.replace(tag)), 0);
    let result = work();
    TAG.with(|value| value.set(0));
    result
}

fn bytes(tag: usize) -> usize {
    LIVE[tag].load(Ordering::SeqCst)
}

#[test]
#[ignore = "explicit allocation profiling; run alone"]
fn allocation_lifetimes() {
    let factory = create_test_provider_factory();
    let address = Address::repeat_byte(1);
    for slots in [64, 512, 4096] {
        for parents in [1, 8, 32] {
            for mode in ["disabled", "lazy", "eager-parent", "eager-submission"] {
                let runtime = Runtime::test();
                assert_eq!(bytes(1), 0);
                assert_eq!(bytes(2), 0);
                PEAK[1].store(0, Ordering::SeqCst);
                PEAK[2].store(0, Ordering::SeqCst);
                PEAK[3].store(0, Ordering::SeqCst);
                let cache = EthStateCache::<EthPrimitives>::spawn_with(
                    factory.clone(),
                    EthStateCacheConfig {
                        max_bals: 1,
                        max_bals_bytes: usize::MAX,
                        ..Default::default()
                    },
                    runtime.clone(),
                );
                let sync_cache = cache.clone();
                let loader: BalLoader = Arc::new(move |hash| {
                    futures::executor::block_on(sync_cache.get_bal(hash)).unwrap()
                });
                let async_cache = cache.clone();
                let asynchronous: AsyncBalLoader = Arc::new(move |hash| {
                    let cache = async_cache.clone();
                    Box::pin(async move { cache.get_bal(hash).await.unwrap() })
                });
                let mut retained = Vec::new();
                let mut weak = Vec::new();
                let mut raw_lengths = Vec::new();
                let mut first = Vec::new();
                let mut repeats = Vec::new();
                let mut decodes = Vec::new();
                for parent in 0..parents {
                    // Generation and encoding are outside allocation accounting.
                    let mut bal = Bal::default();
                    let mut account = AccountBal::default();
                    for slot in 0..slots {
                        account.storage.storage.insert(
                            U256::from(slot),
                            BalWrites::new(vec![(BlockAccessIndex::new(1), U256::from(slot + 1))]),
                        );
                    }
                    bal.accounts.insert(address, account);
                    let raw = alloy_rlp::encode(bal.into_alloy_bal());
                    raw_lengths.push(raw.len());
                    // Same decode/conversion as CachedRevmBal::try_from_raw, with raw
                    // ownership included. ETH insertion is tracked separately below.
                    let decode_start = bytes(1);
                    PEAK[1].store(decode_start, Ordering::SeqCst);
                    let decoded = track(1, || {
                        DecodedBal::from_rlp_bytes(Bytes::copy_from_slice(&raw))
                            .unwrap()
                            .try_map(|bal| Bal::try_from(Vec::from(bal)).map(Arc::new))
                            .unwrap()
                    });
                    decodes.push(serde_json::json!({
                        "retained_bytes": bytes(1) - decode_start,
                        "peak_bytes": PEAK[1].load(Ordering::SeqCst) - decode_start,
                        "temporary_above_retained": PEAK[1].load(Ordering::SeqCst) - bytes(1),
                    }));
                    let expected = decoded.hash();
                    let hash = B256::from(U256::from(parent + 1));
                    track(4, || cache.insert_bal(hash, decoded));
                    let shared = futures::executor::block_on(cache.get_bal(hash)).unwrap().unwrap();
                    weak.push(Arc::downgrade(shared.as_bal()));
                    drop(shared);
                    let before = bytes(2);
                    let view = track(2, || Arc::new(LazyParentBal::default()));
                    for submission in 0..4 {
                        let start = bytes(3);
                        PEAK[3].store(start, Ordering::SeqCst);
                        track(3, || {
                            let committed = committed_loader(
                                (mode != "disabled").then_some(&loader),
                                Some(expected),
                            );
                            let request_view = if mode == "eager-submission" {
                                Arc::new(LazyParentBal::default())
                            } else {
                                view.clone()
                            };
                            if mode.starts_with("eager") {
                                futures::executor::block_on(request_view.prefetch(
                                    hash,
                                    expected,
                                    &asynchronous,
                                ));
                            }
                            let db = ParentBalDb {
                                db: EmptyDB::default(),
                                hash,
                                bal: &request_view,
                                loader: committed.as_ref(),
                                reads: Default::default(),
                            };
                            assert_eq!(
                                db.storage_ref(address, U256::ZERO).unwrap(),
                                if mode == "disabled" { U256::ZERO } else { U256::ONE }
                            );
                        });
                        let row = serde_json::json!({
                            "retained_delta": bytes(3) as i64 - start as i64,
                            "peak_above_start": PEAK[3].load(Ordering::SeqCst) - start,
                        });
                        if submission == 0 {
                            first.push(row);
                        } else {
                            repeats.push(row);
                        }
                    }
                    // Holding several views models concurrent parents; sequential production
                    // has one cache slot. Collection allocations are untagged harness overhead.
                    retained.push(view);
                    assert!(bytes(2) >= before);
                }
                let decoded_before_eviction = bytes(1);
                let validation_retained = bytes(2);
                // An empty BAL under another hash evicts the one-entry real ETH LRU.
                let evict = B256::repeat_byte(0xfe);
                cache.insert_bal(evict, DecodedBal::new(Arc::new(Bal::default()), Bytes::new()));
                drop(futures::executor::block_on(cache.get_bal(evict)).unwrap());
                let alive = weak.iter().filter(|item| item.strong_count() > 0).count();
                assert_eq!(
                    alive,
                    if mode == "disabled" || mode == "eager-submission" { 0 } else { parents }
                );
                drop(weak);
                let pinned_after_eviction = bytes(1);
                let insertion_after_eviction = bytes(4);
                drop(retained);
                let outer_arcs_released = insertion_after_eviction - bytes(4);
                assert_eq!(bytes(1), 0);
                assert_eq!(bytes(2), 0);
                let channel_retained = bytes(3);
                let insertion_channel_retained = bytes(4);
                drop(loader);
                drop(asynchronous);
                drop(cache);
                runtime.shutdown_timeout(std::time::Duration::from_secs(5));
                assert_eq!(bytes(3), 0);
                assert_eq!(bytes(4), 0);
                println!(
                    "ALLOCATION_RESULT {}",
                    serde_json::json!({
                        "mode": mode, "slots": slots, "concurrent_parent_views": parents,
                        "raw_bytes_per_parent": raw_lengths[0],
                        "decoded_live_before_eviction": decoded_before_eviction,
                        "decoded_peak_bytes": PEAK[1].load(Ordering::SeqCst),
                        "validation_retained_bytes": validation_retained,
                        "acquisition_channel_bytes_until_service_shutdown": channel_retained,
                        "insertion_channel_bytes_until_service_shutdown": insertion_channel_retained,
                        "shared_outer_arc_bytes_released_with_views": outer_arcs_released,
                        "decoded_pinned_after_shared_eviction": pinned_after_eviction,
                        "decoded_alive_after_shared_eviction": alive,
                        "decoded_bytes_after_release": bytes(1), "validation_bytes_after_release": bytes(2),
                        "first_acquisitions": first, "repeated_acquisitions": repeats,
                        "decodes": decodes,
                    })
                );
            }
        }
    }
}
