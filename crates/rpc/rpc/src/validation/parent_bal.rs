//! Read-only parent post-state beneath CachedReads and the child executor's mutable State.

use alloy_eip7928::bal::DecodedBal;
use alloy_primitives::{Address, B256, U256};
use revm::{
    bytecode::Bytecode,
    state::{bal::Bal, AccountInfo},
    DatabaseRef,
};
use std::{
    cell::Cell,
    sync::{Arc, OnceLock},
    time::Instant,
};

pub(super) type SharedBal = Arc<DecodedBal<Arc<Bal>>>;
pub(super) type BalLoader = Arc<dyn Fn(B256) -> Option<SharedBal> + Send + Sync>;

/// Shared with concurrent/repeated submissions for exactly one parent hash. A missing BAL is
/// memoized too; cache hits never reach this layer. No child writes are committed here.
#[derive(Default)]
pub(super) struct LazyParentBal(OnceLock<Option<SharedBal>>);

impl LazyParentBal {
    fn get(&self, hash: B256, loader: Option<&BalLoader>) -> Option<&Bal> {
        let loader = loader?;
        self.0
            .get_or_init(|| {
                let start = Instant::now();
                let bal = loader(hash);
                reth_metrics::metrics::histogram!("builder.validation.parent_bal.load_seconds")
                    .record(start.elapsed().as_secs_f64());
                reth_metrics::metrics::counter!("builder.validation.parent_bal.loads").increment(1);
                bal
            })
            .as_ref()
            .map(|bal| bal.as_bal().as_ref())
    }
}

pub(super) struct ParentBalDb<'a, DB> {
    pub(super) db: DB,
    pub(super) hash: B256,
    pub(super) bal: &'a LazyParentBal,
    pub(super) loader: Option<&'a BalLoader>,
    pub(super) reads: ParentBalReads,
}

impl<DB> std::fmt::Debug for ParentBalDb<'_, DB> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ParentBalDb").field("hash", &self.hash).finish_non_exhaustive()
    }
}

impl<DB: DatabaseRef> DatabaseRef for ParentBalDb<'_, DB> {
    type Error = DB::Error;

    fn basic_ref(&self, address: Address) -> Result<Option<AccountInfo>, Self::Error> {
        let Some(account) =
            self.bal.get(self.hash, self.loader).and_then(|bal| bal.accounts.get(&address))
        else {
            self.reads.provider_accounts.set(self.reads.provider_accounts.get() + 1);
            return self.db.basic_ref(address)
        };
        // Select the last write directly, including post-execution writes. Indexed BAL reads
        // are exclusive and positioning at the post-execution index would miss its writes.
        let balance = account.balance.writes.last().map(|(_, value)| *value);
        let nonce = account.nonce.writes.last().map(|(_, value)| *value);
        let code = account.code.writes.last().map(|(_, value)| value);

        // BALs do not encode account existence or storage-wide clears. Only a complete,
        // nonempty account can be reconstructed without consulting the parent state provider.
        // Empty/deleted accounts and partial fields must retain provider existence semantics.
        #[cfg(not(feature = "account-ext"))]
        if let (Some(balance), Some(nonce), Some((code_hash, code))) = (balance, nonce, code) {
            let info = AccountInfo {
                balance,
                nonce,
                code_hash: *code_hash,
                code: Some(code.clone()),
                ..Default::default()
            };
            if !info.is_empty() {
                self.reads.bal_accounts.set(self.reads.bal_accounts.get() + 1);
                return Ok(Some(info))
            }
        }
        self.reads.provider_accounts.set(self.reads.provider_accounts.get() + 1);
        let Some(mut info) = self.db.basic_ref(address)? else { return Ok(None) };
        if let Some(balance) = balance {
            info.balance = balance;
        }
        if let Some(nonce) = nonce {
            info.nonce = nonce;
        }
        if let Some((hash, code)) = code {
            info.code_hash = *hash;
            info.code = Some(code.clone());
        }
        Ok(Some(info))
    }

    fn storage_ref(&self, address: Address, index: U256) -> Result<U256, Self::Error> {
        if let Some(value) = self
            .bal
            .get(self.hash, self.loader)
            .and_then(|bal| bal.accounts.get(&address))
            .and_then(|account| account.storage.storage.get(&index))
            .and_then(|writes| writes.writes.last())
        {
            self.reads.bal_slots.set(self.reads.bal_slots.get() + 1);
            return Ok(value.1)
        }
        // A read-only slot has no value, and an omitted slot may have been cleared by a
        // deletion. The underlying DB is already at the parent's post-state, not pre-state.
        self.reads.provider_slots.set(self.reads.provider_slots.get() + 1);
        self.db.storage_ref(address, index)
    }

    fn code_by_hash_ref(&self, hash: B256) -> Result<Bytecode, Self::Error> {
        // Changed code is supplied inline by basic_ref. Avoid an O(BAL size) hash scan here;
        // code referenced only by hash (including unchanged code) uses the parent's provider.
        self.reads.provider_code.set(self.reads.provider_code.get() + 1);
        self.db.code_by_hash_ref(hash)
    }

    fn block_hash_ref(&self, number: u64) -> Result<B256, Self::Error> {
        self.reads.provider_hashes.set(self.reads.provider_hashes.get() + 1);
        self.db.block_hash_ref(number)
    }
}

/// Aggregate on the execution worker; publish once instead of resolving metric handles per read.
#[derive(Default)]
pub(super) struct ParentBalReads {
    bal_accounts: Cell<u64>,
    bal_slots: Cell<u64>,
    provider_accounts: Cell<u64>,
    provider_slots: Cell<u64>,
    provider_code: Cell<u64>,
    provider_hashes: Cell<u64>,
}

impl Drop for ParentBalReads {
    fn drop(&mut self) {
        for (kind, value) in [
            ("bal_accounts", self.bal_accounts.get()),
            ("bal_slots", self.bal_slots.get()),
            ("provider_accounts", self.provider_accounts.get()),
            ("provider_slots", self.provider_slots.get()),
            ("provider_code", self.provider_code.get()),
            ("provider_hashes", self.provider_hashes.get()),
        ] {
            if value != 0 {
                reth_metrics::metrics::counter!("builder.validation.parent_bal.reads", "kind" => kind).increment(value);
            }
        }
    }
}

/// Bind cached bytes to the exact parent's commitment; no lookup for pre-Amsterdam parents.
pub(super) fn committed_loader(
    loader: Option<&BalLoader>,
    expected: Option<B256>,
) -> Option<BalLoader> {
    let expected = expected?;
    let loader = loader?.clone();
    Some(Arc::new(move |hash| {
        loader(hash).filter(|bal| {
        let matches = bal.hash() == expected;
        if !matches {
            tracing::warn!(target: "rpc::validation", %hash, "Parent BAL commitment mismatch; using state provider");
        }
        matches
    })
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{map::HashMap, Bytes};
    use reth_revm::cached::CachedReads;
    use revm::{
        database::{CacheDB, EmptyDB, State},
        state::{
            bal::{AccountBal, BalWrites, BlockAccessIndex},
            Account, EvmStorageSlot,
        },
        Database, DatabaseCommit,
    };
    use std::{
        cell::Cell,
        sync::atomic::{AtomicUsize, Ordering},
    };

    const ADDRESS: Address = Address::repeat_byte(1);
    const OTHER: Address = Address::repeat_byte(2);

    #[derive(Default)]
    struct Reads {
        basic: Cell<usize>,
        storage: Cell<usize>,
        code: Cell<usize>,
        hash: Cell<usize>,
    }

    struct CountingDb<DB = CacheDB<EmptyDB>> {
        db: DB,
        reads: Reads,
    }

    impl<DB: DatabaseRef> DatabaseRef for CountingDb<DB> {
        type Error = DB::Error;
        fn basic_ref(&self, a: Address) -> Result<Option<AccountInfo>, Self::Error> {
            self.reads.basic.set(self.reads.basic.get() + 1);
            self.db.basic_ref(a)
        }
        fn storage_ref(&self, a: Address, k: U256) -> Result<U256, Self::Error> {
            self.reads.storage.set(self.reads.storage.get() + 1);
            self.db.storage_ref(a, k)
        }
        fn code_by_hash_ref(&self, h: B256) -> Result<Bytecode, Self::Error> {
            self.reads.code.set(self.reads.code.get() + 1);
            self.db.code_by_hash_ref(h)
        }
        fn block_hash_ref(&self, n: u64) -> Result<B256, Self::Error> {
            self.reads.hash.set(self.reads.hash.get() + 1);
            self.db.block_hash_ref(n)
        }
    }

    fn fixture() -> (CountingDb, Bal) {
        let mut db = CacheDB::new(EmptyDB::default());
        db.insert_account_info(
            ADDRESS,
            AccountInfo { balance: U256::from(100), nonce: 7, ..Default::default() },
        );
        for (slot, value) in [(1, 42), (2, 99), (3, 0)] {
            db.insert_account_storage(ADDRESS, U256::from(slot), U256::from(value)).unwrap();
        }
        let mut account = AccountBal::default();
        account.balance = BalWrites::new(vec![
            (BlockAccessIndex::new(0), U256::from(80)),
            (BlockAccessIndex::new(4), U256::from(100)),
        ]);
        account.storage.storage.insert(
            U256::from(1),
            BalWrites::new(vec![
                (BlockAccessIndex::new(1), U256::from(20)),
                (BlockAccessIndex::new(4), U256::from(42)),
            ]),
        );
        account.storage.storage.insert(U256::from(2), BalWrites::default());
        account
            .storage
            .storage
            .insert(U256::from(3), BalWrites::new(vec![(BlockAccessIndex::new(4), U256::ZERO)]));
        let mut bal = Bal::default();
        bal.accounts.insert(ADDRESS, account);
        (CountingDb { db, reads: Reads::default() }, bal)
    }

    fn loader(bal: Option<Bal>) -> (BalLoader, Arc<AtomicUsize>) {
        let calls = Arc::new(AtomicUsize::new(0));
        let counter = calls.clone();
        let bal = bal.map(|bal| {
            let raw: Bytes = alloy_rlp::encode(bal.clone().into_alloy_bal()).into();
            let hash = alloy_primitives::keccak256(&raw);
            Arc::new(DecodedBal::new_unchecked(Arc::new(bal), raw, hash))
        });
        (
            Arc::new(move |_| {
                counter.fetch_add(1, Ordering::Relaxed);
                bal.clone()
            }),
            calls,
        )
    }

    fn adapter<'a, DB: DatabaseRef>(
        db: &'a CountingDb<DB>,
        bal: &'a LazyParentBal,
        loader: Option<&'a BalLoader>,
    ) -> ParentBalDb<'a, &'a CountingDb<DB>> {
        ParentBalDb { db, hash: B256::repeat_byte(1), bal, loader, reads: Default::default() }
    }

    #[test]
    fn final_writes_partial_accounts_and_provider_fallback() {
        let (db, bal) = fixture();
        let (load, calls) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        let db_bal = adapter(&db, &lazy, Some(&load));
        let info = db_bal.basic_ref(ADDRESS).unwrap().unwrap();
        assert_eq!(info.balance, U256::from(100));
        assert_eq!(info.nonce, 7); // not in BAL: retain complete provider metadata
        assert_eq!(db.reads.basic.get(), 1);
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(1)).unwrap(), U256::from(42));
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(3)).unwrap(), U256::ZERO);
        assert_eq!(db.reads.storage.get(), 0);
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(2)).unwrap(), U256::from(99));
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(4)).unwrap(), U256::ZERO);
        assert_eq!(db.reads.storage.get(), 2);
        assert_eq!(db_bal.basic_ref(OTHER).unwrap(), None);
        db_bal.code_by_hash_ref(B256::ZERO).unwrap();
        db_bal.block_hash_ref(10).unwrap();
        assert_eq!(db.reads.code.get(), 1);
        assert_eq!(db.reads.hash.get(), 1);
        assert_eq!(calls.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn missing_or_disabled_bal_preserves_provider_reads() {
        for enabled in [true, false] {
            let (db, _) = fixture();
            let (load, calls) = loader(None);
            let lazy = LazyParentBal::default();
            let db_bal = adapter(&db, &lazy, enabled.then_some(&load));
            assert_eq!(db_bal.basic_ref(ADDRESS).unwrap(), db.db.basic_ref(ADDRESS).unwrap());
            assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(1)).unwrap(), U256::from(42));
            assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(2)).unwrap(), U256::from(99));
            assert_eq!(db.reads.storage.get(), 2);
            assert_eq!(calls.load(Ordering::Relaxed), usize::from(enabled));
        }
    }

    #[test]
    fn cached_reads_skip_bal_even_for_repeated_submissions() {
        let (db, bal) = fixture();
        let (load, calls) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        let mut cache = CachedReads::default();
        {
            let mut cached = cache.as_db_mut(adapter(&db, &lazy, Some(&load)));
            // Slot-first reads need complete account metadata. Balance alone is insufficient.
            assert_eq!(cached.storage(ADDRESS, U256::from(1)).unwrap(), U256::from(42));
            assert_eq!(cached.basic(ADDRESS).unwrap().unwrap().nonce, 7);
        }
        assert_eq!(db.reads.basic.get(), 1);
        assert_eq!(db.reads.storage.get(), 0);
        assert_eq!(calls.load(Ordering::Relaxed), 1);
        let must_not_load: BalLoader = Arc::new(|_| panic!("cache hit fetched BAL"));
        let unloaded = LazyParentBal::default();
        let mut cached = cache.as_db_mut(adapter(&db, &unloaded, Some(&must_not_load)));
        assert_eq!(cached.storage(ADDRESS, U256::from(1)).unwrap(), U256::from(42));
        assert_eq!(cached.basic(ADDRESS).unwrap().unwrap().balance, U256::from(100));
        assert!(unloaded.0.get().is_none());
    }

    #[test]
    fn complete_account_and_changed_code_avoid_provider_reads() {
        let (db, mut bal) = fixture();
        let code = Bytecode::new_raw(Bytes::from_static(&[0x60, 0, 0x00]));
        let account = bal.accounts.get_mut(&ADDRESS).unwrap();
        account.nonce = BalWrites::new(vec![(BlockAccessIndex::new(4), 7)]);
        account.code =
            BalWrites::new(vec![(BlockAccessIndex::new(4), (code.hash_slow(), code.clone()))]);
        let (load, _) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        let info = adapter(&db, &lazy, Some(&load)).basic_ref(ADDRESS).unwrap().unwrap();
        assert_eq!(info.code, Some(code));
        assert_eq!(info.nonce, 7);
        #[cfg(not(feature = "account-ext"))]
        assert_eq!(db.reads.basic.get(), 0);
    }

    #[test]
    fn partial_code_change_preserves_nonce_balance_and_existence() {
        let (db, mut bal) = fixture();
        let code = Bytecode::new_raw(Bytes::from_static(&[0x00]));
        let account = bal.accounts.get_mut(&ADDRESS).unwrap();
        account.balance = Default::default();
        account.code =
            BalWrites::new(vec![(BlockAccessIndex::new(4), (code.hash_slow(), code.clone()))]);
        let (load, _) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        let info = adapter(&db, &lazy, Some(&load)).basic_ref(ADDRESS).unwrap().unwrap();
        assert_eq!(info.code, Some(code));
        assert_eq!(info.nonce, 7);
        assert_eq!(info.balance, U256::from(100));
        assert_eq!(db.reads.basic.get(), 1);
    }

    #[test]
    fn deletion_and_storage_wide_clears_use_parent_post_state() {
        let (mut db, mut bal) = fixture();
        // The BAL has no existence bit. All-zero final fields must not turn None into Some.
        db.db.cache.accounts.remove(&ADDRESS);
        let account = bal.accounts.get_mut(&ADDRESS).unwrap();
        account.balance = BalWrites::new(vec![(BlockAccessIndex::new(4), U256::ZERO)]);
        account.nonce = BalWrites::new(vec![(BlockAccessIndex::new(4), 0)]);
        account.code = BalWrites::new(vec![(
            BlockAccessIndex::new(4),
            (alloy_consensus::constants::KECCAK_EMPTY, Bytecode::default()),
        )]);
        account
            .storage
            .storage
            .get_mut(&U256::from(1))
            .unwrap()
            .force_update(BlockAccessIndex::new(4), U256::ZERO);
        let (load, _) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        let db_bal = adapter(&db, &lazy, Some(&load));
        assert_eq!(db_bal.basic_ref(ADDRESS).unwrap(), None);
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(1)).unwrap(), U256::ZERO);
        // Omitted/read-only slots may have been wiped. Parent post-state, not BAL defaults.
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(2)).unwrap(), U256::ZERO);
        assert_eq!(db_bal.storage_ref(ADDRESS, U256::from(4)).unwrap(), U256::ZERO);
    }

    #[test]
    fn child_writes_and_child_storage_clears_win_over_parent_bal() {
        let (db, bal) = fixture();
        let (load, _) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        let mut cache = CachedReads::default();
        {
            let mut state = State::builder()
                .with_database(cache.as_db_mut(adapter(&db, &lazy, Some(&load))))
                .build();
            let mut info = state.basic(ADDRESS).unwrap().unwrap();
            assert_eq!(state.storage(ADDRESS, U256::from(1)).unwrap(), U256::from(42));
            info.balance = U256::from(200);
            let mut child = Account::from(info.clone()).with_touched_mark();
            child.storage.insert(
                U256::from(1),
                EvmStorageSlot::new_changed(U256::from(42), U256::from(88), Default::default()),
            );
            state.commit(HashMap::from_iter([(ADDRESS, child)]));
            assert_eq!(state.basic(ADDRESS).unwrap().unwrap().balance, U256::from(200));
            assert_eq!(state.storage(ADDRESS, U256::from(1)).unwrap(), U256::from(88));
            // A child creation clears old storage, including slots present in the parent's BAL.
            state.commit(HashMap::from_iter([(
                ADDRESS,
                Account::from(info).with_touched_mark().with_created_mark(),
            )]));
            assert_eq!(state.storage(ADDRESS, U256::from(1)).unwrap(), U256::ZERO);
        }
        // CachedReads stores only parent values, never committed child modifications.
        let mut next = cache.as_db_mut(adapter(&db, &lazy, Some(&load)));
        assert_eq!(next.basic(ADDRESS).unwrap().unwrap().balance, U256::from(100));
        assert_eq!(next.storage(ADDRESS, U256::from(1)).unwrap(), U256::from(42));
    }

    #[test]
    fn concurrent_submissions_share_one_lazy_fetch() {
        let (_, bal) = fixture();
        let (load, calls) = loader(Some(bal));
        let lazy = LazyParentBal::default();
        std::thread::scope(|scope| {
            for _ in 0..8 {
                scope.spawn(|| {
                    assert!(lazy.get(B256::ZERO, Some(&load)).is_some());
                });
            }
        });
        assert_eq!(calls.load(Ordering::Relaxed), 1);
    }
    #[test]
    fn ethereum_execution_receipts_bal_and_state_roots_match() {
        use alloy_consensus::{Header, SignableTransaction, TxLegacy};
        use alloy_primitives::{Signature, TxKind};
        use reth_chainspec::{ChainSpecBuilder, MAINNET};
        use reth_ethereum_primitives::{Block, BlockBody, TransactionSigned};
        use reth_evm::{execute::Executor, ConfigureEvm};
        use reth_evm_ethereum::EthEvmConfig;
        use reth_primitives_traits::RecoveredBlock;
        use reth_trie_common::{
            root::{state_root_unhashed, storage_root_unhashed},
            TrieAccount,
        };

        let code = Bytecode::new_raw(Bytes::from_static(&[
            0x60, 0x01, 0x54, 0x50, // SLOAD(1), POP
            0x60, 0x58, 0x60, 0x01, 0x55, 0x00, // SSTORE(1,88), STOP
        ]));
        let evm = EthEvmConfig::new(Arc::new(
            ChainSpecBuilder::from(&*MAINNET).amsterdam_activated().build(),
        ));
        let transactions = (0..2)
            .map(|nonce| {
                TransactionSigned::from(
                    TxLegacy {
                        chain_id: Some(1),
                        nonce,
                        gas_price: 0,
                        gas_limit: 100_000,
                        to: TxKind::Call(ADDRESS),
                        value: U256::ZERO,
                        input: Bytes::new(),
                    }
                    .into_signed(Signature::new(
                        U256::from(1),
                        U256::from(1),
                        false,
                    )),
                )
            })
            .collect();
        let block = RecoveredBlock::new_unhashed(
            Block {
                header: Header {
                    number: 1,
                    timestamp: 1,
                    gas_limit: 1_000_000,
                    base_fee_per_gas: Some(0),
                    excess_blob_gas: Some(0),
                    parent_beacon_block_root: Some(B256::ZERO),
                    block_access_list_hash: Some(B256::ZERO),
                    ..Default::default()
                },
                body: BlockBody {
                    transactions,
                    withdrawals: Some(Default::default()),
                    ..Default::default()
                },
            },
            vec![OTHER; 2],
        );

        let mut outputs = Vec::new();
        for enabled in [false, true] {
            let (mut db, mut bal) = fixture();
            db.db.insert_account_info(
                OTHER,
                AccountInfo { balance: U256::from(1_000_000), ..Default::default() },
            );
            let mut contract = db.db.basic_ref(ADDRESS).unwrap().unwrap();
            contract.code_hash = code.hash_slow();
            contract.code = Some(code.clone());
            db.db.insert_account_info(ADDRESS, contract);
            bal.accounts.get_mut(&ADDRESS).unwrap().code =
                BalWrites::new(vec![(BlockAccessIndex::new(4), (code.hash_slow(), code.clone()))]);
            let (load, _) = loader(Some(bal));
            let lazy = LazyParentBal::default();
            let mut cache = CachedReads::default();
            let mut executor =
                evm.batch_executor(cache.as_db_mut(adapter(&db, &lazy, enabled.then_some(&load))));
            let result = executor.execute_one(&block).unwrap();
            assert!(result.receipts.iter().all(|receipt| receipt.success));
            let rebuilt_bal = executor.take_bal().unwrap();
            let mut state = executor.into_state();
            let bundle = state.take_bundle();
            drop(state);
            assert_eq!(
                bundle.state[&ADDRESS].storage[&U256::from(1)].present_value,
                U256::from(88)
            );

            // Compute a real MPT root from the complete post-state, including untouched slots.
            let mut addresses: Vec<_> =
                db.db.cache.accounts.keys().copied().chain(bundle.state.keys().copied()).collect();
            addresses.sort_unstable();
            addresses.dedup();
            let accounts = addresses.into_iter().filter_map(|address| {
                let changed = bundle.state.get(&address);
                let info = changed
                    .map(|account| account.info.clone())
                    .unwrap_or_else(|| db.db.basic_ref(address).unwrap())?;
                if info.is_empty() {
                    return None
                }
                let mut slots = db
                    .db
                    .cache
                    .accounts
                    .get(&address)
                    .map(|a| a.storage.clone())
                    .unwrap_or_default();
                if let Some(account) = changed {
                    if account.was_destroyed() {
                        slots.clear();
                    }
                    for (key, value) in &account.storage {
                        slots.insert(*key, value.present_value);
                    }
                }
                let storage_root = storage_root_unhashed(
                    slots
                        .into_iter()
                        .filter(|(_, value)| !value.is_zero())
                        .map(|(key, value)| (B256::from(key), value)),
                );
                Some((
                    address,
                    TrieAccount::new(info.nonce, info.balance, storage_root, info.code_hash),
                ))
            });
            let root = state_root_unhashed(accounts);
            outputs.push((result, rebuilt_bal, bundle, root, db.reads.storage.get()));
        }
        assert_eq!(outputs[0].0, outputs[1].0);
        assert_eq!(outputs[0].1, outputs[1].1);
        assert_eq!(outputs[0].2, outputs[1].2);
        assert_eq!(outputs[0].3, outputs[1].3);
        assert!(outputs[1].4 < outputs[0].4, "BAL should reduce provider storage reads");
    }
    #[test]
    fn parent_commitment_and_pre_amsterdam_fallback() {
        let (db, bal) = fixture();
        let (load, calls) = loader(Some(bal));
        assert!(committed_loader(Some(&load), None).is_none());
        assert_eq!(calls.load(Ordering::Relaxed), 0);
        let guarded = committed_loader(Some(&load), Some(B256::ZERO)).unwrap();
        let lazy = LazyParentBal::default();
        assert_eq!(
            adapter(&db, &lazy, Some(&guarded)).storage_ref(ADDRESS, U256::from(1)).unwrap(),
            U256::from(42)
        );
        assert_eq!(db.reads.storage.get(), 1); // mismatch falls back, rather than trusting the BAL
        let expected = load(B256::ZERO).unwrap().hash();
        let guarded = committed_loader(Some(&load), Some(expected)).unwrap();
        let lazy = LazyParentBal::default();
        assert_eq!(
            adapter(&db, &lazy, Some(&guarded)).storage_ref(ADDRESS, U256::from(1)).unwrap(),
            U256::from(42)
        );
        assert_eq!(db.reads.storage.get(), 1); // matching commitment serves BAL
    }
    /// Controlled MDBX state-read benchmark. This measures the database/cache layer, not
    /// end-to-end Flashbots RPC latency or production mainnet validation.
    #[test]
    #[ignore = "explicit performance run"]
    fn benchmark_mdbx_parent_bal() {
        use alloy_eips::NumHash;
        use reth_db_api::{
            tables,
            transaction::{DbTx, DbTxMut},
        };
        use reth_ethereum_primitives::EthPrimitives;
        use reth_primitives_traits::{Account, StorageEntry};
        use reth_provider::test_utils::create_test_provider_factory;
        use reth_revm::database::StateProviderDatabase;
        use reth_rpc_eth_types::{EthStateCache, EthStateCacheConfig};
        use reth_storage_api::{BalProvider, RawBal, StateProvider};
        use reth_tasks::Runtime;
        use std::{hint::black_box, time::Instant};

        const ACCOUNTS: u64 = 16;
        const SLOTS: u64 = 64;
        const SAMPLES: u64 = 30;
        let factory = create_test_provider_factory();
        let tx = factory.provider_rw().unwrap().into_tx();
        for a in 1..=ACCOUNTS {
            let address = Address::from_word(B256::from(U256::from(a)));
            tx.put::<tables::PlainAccountState>(
                address,
                Account { nonce: 1, balance: U256::from(100), bytecode_hash: None },
            )
            .unwrap();
            for k in 0..SLOTS * 2 {
                tx.put::<tables::PlainStorageState>(
                    address,
                    StorageEntry { key: B256::from(U256::from(k)), value: U256::from(k + 1) },
                )
                .unwrap();
            }
        }
        tx.commit().unwrap();
        let runtime = Runtime::test();
        let cache = EthStateCache::<EthPrimitives>::spawn_with(
            factory.clone(),
            EthStateCacheConfig::default(),
            runtime.clone(),
        );
        let cache_loader: BalLoader =
            Arc::new(move |hash| futures::executor::block_on(cache.get_bal(hash)).unwrap());
        println!("MDBX_BENCH accounts={}, slots_per_account={}, samples={}, build=debug, OS_page_cache=warm", ACCOUNTS, SLOTS, SAMPLES);
        for overlap in [0, 25, 100] {
            let mut bal = Bal::default();
            for a in 1..=ACCOUNTS {
                let mut account = AccountBal::default();
                // Partial metadata on purpose: account reads must still hit the provider.
                account.balance = BalWrites::new(vec![(BlockAccessIndex::new(4), U256::from(100))]);
                for k in 0..SLOTS {
                    let key = if k < SLOTS * overlap / 100 { k } else { k + SLOTS };
                    account.storage.storage.insert(
                        U256::from(key),
                        BalWrites::new(vec![(BlockAccessIndex::new(4), U256::from(key + 1))]),
                    );
                }
                bal.accounts.insert(Address::from_word(B256::from(U256::from(a))), account);
            }
            let raw: Bytes = alloy_rlp::encode(bal.into_alloy_bal()).into();
            let mut decode_us = Vec::new();
            for _ in 0..SAMPLES {
                let start = Instant::now();
                let decoded = DecodedBal::from_rlp_bytes(raw.clone())
                    .unwrap()
                    .try_map(|bal| Bal::try_from(Vec::from(bal)).map(Arc::new))
                    .unwrap();
                black_box(decoded);
                decode_us.push(start.elapsed().as_secs_f64() * 1e6);
            }
            decode_us.sort_by(f64::total_cmp);
            println!(
                "BAL_COST overlap={} raw_bytes={} decode_median_us={:.3} decode_p95_us={:.3}",
                overlap,
                raw.len(),
                decode_us[15],
                decode_us[28]
            );
            for enabled in [false, true] {
                let mut first = Vec::new();
                let mut repeated = Vec::new();
                let mut account_reads = 0;
                let mut slot_reads = 0;
                for sample in 0..SAMPLES {
                    let hash = B256::from(U256::from(
                        10_000 + overlap * 1_000 + u64::from(enabled) * 100 + sample,
                    ));
                    factory
                        .bal_store()
                        .insert(NumHash::new(sample, hash), RawBal::new(raw.clone()))
                        .unwrap();
                    let state = factory.latest().unwrap();
                    let db = CountingDb {
                        db: StateProviderDatabase::new((&state).into_evm_state_provider()),
                        reads: Reads::default(),
                    };
                    let lazy = LazyParentBal::default();
                    let mut reads = CachedReads::default();
                    for round in 0..2 {
                        let start = Instant::now();
                        let mut cached = reads.as_db_mut(ParentBalDb {
                            db: &db,
                            hash,
                            bal: &lazy,
                            loader: enabled.then_some(&cache_loader),
                            reads: Default::default(),
                        });
                        for a in 1..=ACCOUNTS {
                            let address = Address::from_word(B256::from(U256::from(a)));
                            for k in 0..SLOTS {
                                assert_eq!(
                                    black_box(cached.storage(address, U256::from(k)).unwrap()),
                                    U256::from(k + 1)
                                );
                            }
                        }
                        drop(cached);
                        let elapsed = start.elapsed().as_secs_f64() * 1e6;
                        if round == 0 {
                            first.push(elapsed);
                        } else {
                            repeated.push(elapsed);
                        }
                    }
                    account_reads += db.reads.basic.get();
                    slot_reads += db.reads.storage.get();
                }
                first.sort_by(f64::total_cmp);
                repeated.sort_by(f64::total_cmp);
                println!("MDBX_RESULT overlap={} enabled={} first_median_us={:.3} first_p95_us={:.3} repeated_median_us={:.3} repeated_p95_us={:.3} provider_accounts_per_parent={} provider_slots_per_parent={}", overlap, enabled, first[15], first[28], repeated[15], repeated[28], account_reads/SAMPLES as usize, slot_reads/SAMPLES as usize);
            }
        }
        // Owned runtime shutdown happens from this synchronous benchmark thread.
        runtime.shutdown_timeout(std::time::Duration::from_secs(5));
    }
}
