//! Bounded reuse of a pure state-root calculation after complete block execution.
//!
//! A root depends on the exact parent state and complete hashed post-state. Equality includes
//! every account field, account deletion, and every storage value (including explicit zeroes
//! supplied by the provider when destroyed storage is expanded). The submitted header's claimed
//! root is deliberately absent from the key: callers must compare the computed root on every
//! request. Execution, consensus, BAL verification, and payment checks remain outside this cache.

use alloy_primitives::B256;
use parking_lot::RwLock;
use reth_errors::ProviderResult;
use reth_trie_common::HashedPostState;
use std::sync::Arc;

/// At most one state with this many aggregate map buckets is retained. Capacity, rather than
/// length, also bounds sparse maps; oversized inputs bypass cloning and use ordinary calculation.
const MAX_RETAINED_BUCKETS: usize = 8192;

/// Single-entry cache; concurrent misses may compute independently without holding a lock.
#[derive(Debug, Default)]
pub(super) struct StateRootCache {
    entry: RwLock<Option<Arc<Entry>>>,
}

impl StateRootCache {
    /// Returns an actual computed root and whether its exact inputs were already cached.
    pub(super) fn root(
        &self,
        parent: B256,
        state: HashedPostState,
        compute: impl FnOnce(HashedPostState) -> ProviderResult<B256>,
    ) -> ProviderResult<(B256, bool)> {
        let snapshot = self.entry.read().clone();
        if let Some(entry) = snapshot &&
            entry.parent == parent &&
            entry.state == state
        {
            return Ok((entry.root, true))
        }
        let retained = Self::admissible(&state).then(|| state.clone());
        let root = compute(state)?;
        if let Some(state) = retained {
            *self.entry.write() = Some(Arc::new(Entry { parent, state, root }));
        }
        Ok((root, false))
    }

    fn admissible(state: &HashedPostState) -> bool {
        let buckets = state.accounts.capacity() + state.storages.capacity();
        let buckets = state.storages.values().try_fold(buckets, |total, storage| {
            let total = total.checked_add(storage.storage.capacity())?;
            (total <= MAX_RETAINED_BUCKETS).then_some(total)
        });
        if buckets.is_none_or(|count| count > MAX_RETAINED_BUCKETS) {
            return false
        }
        // Extensions share their allocation when cloned, but must still have a retention bound.
        #[cfg(feature = "account-ext")]
        if state.accounts.values().flatten().map(|account| account.extension.len()).sum::<usize>() >
            256 * 1024
        {
            return false
        }
        true
    }
}

#[derive(Debug)]
struct Entry {
    parent: B256,
    state: HashedPostState,
    root: B256,
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{keccak256, U256};
    use reth_errors::ProviderError;
    use reth_primitives_traits::Account;
    use reth_trie_common::{
        root::{state_root_unsorted, storage_root_unsorted},
        HashedStorage,
    };
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn fixture() -> HashedPostState {
        let mut state = HashedPostState::default();
        state.accounts.insert(
            B256::ZERO,
            Some(Account { nonce: 1, balance: U256::from(2), ..Default::default() }),
        );
        state.storages.insert(B256::ZERO, HashedStorage::from_iter([(B256::ZERO, U256::from(3))]));
        state
    }

    // Actual Ethereum MPT calculation for these complete, empty-base fixtures.
    fn ethereum_root(state: HashedPostState) -> ProviderResult<B256> {
        Ok(state_root_unsorted(state.accounts.into_iter().filter_map(|(address, account)| {
            account.map(|account| {
                let storage = state.storages.get(&address).into_iter().flat_map(|slots| {
                    slots
                        .storage
                        .iter()
                        .filter(|(_, value)| !value.is_zero())
                        .map(|(k, v)| (*k, *v))
                });
                (address, account.into_trie_account(storage_root_unsorted(storage)))
            })
        })))
    }

    #[test]
    fn identical_complete_state_reuses_actual_root() {
        let cache = StateRootCache::default();
        let state = fixture();
        let expected = ethereum_root(state.clone()).unwrap();
        assert_eq!(
            cache.root(B256::ZERO, state.clone(), ethereum_root).unwrap(),
            (expected, false)
        );
        assert_eq!(
            cache.root(B256::ZERO, state, |_| panic!("unexpected calculation")).unwrap(),
            (expected, true)
        );
    }

    #[test]
    fn changed_child_fields_never_reuse_stale_root() {
        for variant in 0..8 {
            let cache = StateRootCache::default();
            let original = fixture();
            let (before, _) = cache.root(B256::ZERO, original.clone(), ethereum_root).unwrap();
            let mut changed = original;
            match variant {
                0 => changed.accounts.get_mut(&B256::ZERO).unwrap().as_mut().unwrap().nonce += 1,
                1 => {
                    changed.accounts.get_mut(&B256::ZERO).unwrap().as_mut().unwrap().balance +=
                        U256::from(1)
                }
                2 => {
                    changed.accounts.get_mut(&B256::ZERO).unwrap().as_mut().unwrap().bytecode_hash =
                        Some(keccak256([0x60, 0x00]))
                }
                3 => {
                    changed.accounts.insert(B256::ZERO, None);
                }
                4 => {
                    changed.accounts.insert(
                        B256::repeat_byte(1),
                        Some(Account { nonce: 2, ..Default::default() }),
                    );
                }
                5 => {
                    changed
                        .storages
                        .get_mut(&B256::ZERO)
                        .unwrap()
                        .storage
                        .insert(B256::ZERO, U256::from(4));
                }
                6 => {
                    changed
                        .storages
                        .get_mut(&B256::ZERO)
                        .unwrap()
                        .storage
                        .insert(B256::ZERO, U256::ZERO);
                }
                7 => {
                    changed.storages.get_mut(&B256::ZERO).unwrap().storage.clear();
                }
                _ => unreachable!(),
            }
            let expected = ethereum_root(changed.clone()).unwrap();
            assert_ne!(before, expected, "fixture must change the MPT root: {variant}");
            assert_eq!(
                cache.root(B256::ZERO, changed.clone(), ethereum_root).unwrap(),
                (expected, false)
            );
            assert_eq!(
                cache.root(B256::ZERO, changed, |_| panic!("unexpected calculation")).unwrap(),
                (expected, true)
            );
        }
    }

    #[test]
    fn different_and_reorged_parents_recompute() {
        let cache = StateRootCache::default();
        let calls = AtomicUsize::new(0);
        for parent in [B256::ZERO, B256::repeat_byte(1), B256::ZERO] {
            let (root, hit) = cache
                .root(parent, fixture(), |_| {
                    calls.fetch_add(1, Ordering::Relaxed);
                    Ok(parent)
                })
                .unwrap();
            assert_eq!(root, parent);
            assert!(!hit);
        }
        assert_eq!(calls.load(Ordering::Relaxed), 3);
    }

    #[test]
    fn provider_failure_does_not_cache_a_root() {
        let cache = StateRootCache::default();
        assert!(cache
            .root(B256::ZERO, fixture(), |_| Err(ProviderError::other(std::io::Error::other(
                "root failure"
            ))))
            .is_err());
        assert!(!cache.root(B256::ZERO, fixture(), ethereum_root).unwrap().1);
    }

    #[test]
    fn oversized_and_sparse_maps_bypass_retention() {
        let cache = StateRootCache::default();
        for map in 0..3 {
            let mut state = fixture();
            match map {
                0 => state.accounts.reserve(MAX_RETAINED_BUCKETS),
                1 => state.storages.reserve(MAX_RETAINED_BUCKETS),
                2 => state
                    .storages
                    .get_mut(&B256::ZERO)
                    .unwrap()
                    .storage
                    .reserve(MAX_RETAINED_BUCKETS),
                _ => unreachable!(),
            }
            assert!(!StateRootCache::admissible(&state));
            for _ in 0..2 {
                assert!(!cache.root(B256::ZERO, state.clone(), ethereum_root).unwrap().1);
            }
            assert!(cache.entry.read().is_none());
        }
    }

    #[test]
    fn map_insertion_order_does_not_change_key() {
        let cache = StateRootCache::default();
        let mut state = fixture();
        state
            .accounts
            .insert(B256::repeat_byte(1), Some(Account { nonce: 3, ..Default::default() }));
        let expected = cache.root(B256::ZERO, state.clone(), ethereum_root).unwrap().0;
        let mut reordered = state.clone();
        reordered.accounts.clear();
        for key in [B256::repeat_byte(1), B256::ZERO] {
            reordered.accounts.insert(key, state.accounts[&key].clone());
        }
        assert_eq!(
            cache.root(B256::ZERO, reordered, |_| panic!("unexpected calculation")).unwrap(),
            (expected, true)
        );
    }

    #[test]
    fn concurrent_different_parents_keep_roots_with_their_keys() {
        let cache = StateRootCache::default();
        std::thread::scope(|scope| {
            for byte in 0..16 {
                let cache = &cache;
                scope.spawn(move || {
                    let parent = B256::repeat_byte(byte);
                    for _ in 0..10 {
                        let (root, _) = cache.root(parent, fixture(), |_| Ok(parent)).unwrap();
                        assert_eq!(root, parent);
                    }
                });
            }
        });
    }
}
