#!/usr/bin/env python3
"""Replay real V6 validation on isolated Amsterdam dev nodes; never uses funded keys.

Requires eth-account (pip install eth-account). Writes genesis, node logs, and raw
JSON measurements beneath --output-dir. Use an optimized binary for performance
conclusions. Debug results are useful for correctness and read-count verification.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import time
import urllib.request

from eth_account import Account

ZERO = '0x' + '00' * 32
CONTRACT = '0x0000000000000000000000000000000000001000'
# Publicly known, unfunded development key. Allocated only in this isolated genesis.
DEV_KEY = bytes.fromhex('00' * 31 + '01')
DEV_ACCOUNT = Account.from_key(DEV_KEY)


def rpc(port, method, params):
    data = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}', data, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if 'error' in result:
        raise RuntimeError(f'{method}: {result["error"]}')
    return result['result']


def metrics(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=10) as response:
        text = response.read().decode()
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.match(r'((?:reth_)?builder_validation_parent_bal_\S+)\s+(\S+)', line)
        if match and 'quantile=' not in match[1]:
            result[match[1]] = float(match[2])
    return result


def rss(pid):
    match = re.search(r'^VmRSS:\s+(\d+)', Path(f'/proc/{pid}/status').read_text(), re.M)
    return int(match[1]) * 1024 if match else None


def genesis(slots, amsterdam=True):
    # Each call increments slots [calldata_offset, calldata_offset + slots).
    code = bytearray()
    for key in range(slots):
        code += bytes([0x61]) + key.to_bytes(2, 'big')
        code += bytes.fromhex('6000350180546001019055')
    code += b'\x00'
    config = {'chainId': 1337, 'terminalTotalDifficulty': 0, 'terminalTotalDifficultyPassed': True}
    for fork in ['homestead', 'eip150', 'eip155', 'eip158', 'byzantium', 'constantinople',
                 'petersburg', 'istanbul', 'berlin', 'london']:
        config[fork + 'Block'] = 0
    for fork in ['shanghai', 'cancun', 'prague', 'osaka']:
        config[fork + 'Time'] = 0
    if amsterdam:
        config['amsterdamTime'] = 0
    schedule = {'target': 6, 'max': 9, 'baseFeeUpdateFraction': 5007716}
    config['blobSchedule'] = {'cancun': {'target': 3, 'max': 6, 'baseFeeUpdateFraction': 3338477},
                              'prague': schedule, 'osaka': schedule, 'amsterdam': schedule}
    return {'config': config, 'nonce': '0x0', 'timestamp': '0x0', 'extraData': '0x',
            'gasLimit': '0x1c9c380', 'difficulty': '0x0', 'mixHash': ZERO,
            'coinbase': '0x' + '00' * 20, 'baseFeePerGas': '0x0',
            'alloc': {DEV_ACCOUNT.address: {'balance': hex(10**24)},
                      CONTRACT: {'balance': '0x1', 'nonce': '0x1', 'code': '0x' + code.hex(),
                                 'storage': {f'0x{k:064x}': '0x' + f'{1:064x}' for k in range(slots * 2)}}}}


def mine(port, offset, slots):
    nonce = int(rpc(port, 'eth_getTransactionCount', [DEV_ACCOUNT.address, 'latest']), 16)
    tx = {'chainId': 1337, 'nonce': nonce, 'gasPrice': 10**9,
          'gas': min(16_000_000, slots * 30_000 + 100_000), 'to': CONTRACT,
          'value': 0, 'data': offset.to_bytes(32, 'big')}
    signed = DEV_ACCOUNT.sign_transaction(tx)
    tx_hash = rpc(port, 'eth_sendRawTransaction', ['0x' + signed.raw_transaction.hex()])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        receipt = rpc(port, 'eth_getTransactionReceipt', [tx_hash])
        if receipt:
            if receipt['status'] != '0x1':
                raise RuntimeError(f'contract call reverted: {receipt}')
            return rpc(port, 'eth_getBlockByHash', [receipt['blockHash'], False])
        time.sleep(0.025)
    raise TimeoutError('dev block mining timed out')


def submission(port, block):
    bal = rpc(port, 'eth_getBlockAccessListRaw', [block['hash']])
    if not bal:
        raise RuntimeError('Amsterdam block has no available BAL')
    payload = {out: block[inp] for out, inp in [
        ('parent_hash', 'parentHash'), ('fee_recipient', 'miner'), ('state_root', 'stateRoot'),
        ('receipts_root', 'receiptsRoot'), ('logs_bloom', 'logsBloom'), ('prev_randao', 'mixHash'),
        ('extra_data', 'extraData'), ('block_hash', 'hash')]}
    for out, inp in [('block_number', 'number'), ('gas_limit', 'gasLimit'), ('gas_used', 'gasUsed'),
                     ('timestamp', 'timestamp'), ('base_fee_per_gas', 'baseFeePerGas'),
                     ('blob_gas_used', 'blobGasUsed'), ('excess_blob_gas', 'excessBlobGas')]:
        payload[out] = str(int(block[inp], 16))
    payload['transactions'] = [rpc(port, 'debug_getRawTransaction', [tx]) for tx in block['transactions']]
    payload['withdrawals'] = [{key: str(int(value, 16)) if key != 'address' else value
                              for key, value in withdrawal.items()} for withdrawal in block.get('withdrawals', [])]
    payload['block_access_list'] = bal
    payload['slot_number'] = str(int(block['slotNumber'], 16))
    parent = rpc(port, 'eth_getBlockByHash', [block['parentHash'], False])
    return {'message': {'slot': payload['slot_number'], 'parent_hash': block['parentHash'],
                        'block_hash': block['hash'], 'builder_pubkey': '0x' + '00' * 48,
                        'proposer_pubkey': '0x' + '00' * 48, 'proposer_fee_recipient': block['miner'],
                        'gas_limit': payload['gas_limit'], 'gas_used': payload['gas_used'], 'value': '0'},
            'execution_payload': payload, 'blobs_bundle': {'commitments': [], 'proofs': [], 'blobs': []},
            'execution_requests': {'deposits': [], 'withdrawals': [], 'consolidations': [],
                                   'builder_deposits': [], 'builder_exits': []},
            'signature': '0x' + '00' * 96, 'registered_gas_limit': str(int(parent['gasLimit'], 16)),
            'parent_beacon_block_root': block['parentBeaconBlockRoot']}


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=30)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--slots', type=int, default=64)
    parser.add_argument('--port', type=int, default=18545)
    args = parser.parse_args()
    args.binary = args.binary.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output([str(args.binary), '--version'], text=True).strip()
    results = {'binary': str(args.binary), 'version': version, 'slots': args.slots,
               'samples': args.samples, 'repeats': args.repeats, 'scope': 'isolated Amsterdam dev nodes',
               'cpu_quota': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
               'memory_limit_bytes': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
               'rows': [], 'raw': []}
    for cache_kind in ['cold', 'normal']:
        for enabled in [False, True]:
            label = f'{cache_kind}-{enabled}'
            run_dir = args.output_dir / label
            # Refuse to reuse state from another benchmark; it changes read/latency distributions.
            run_dir.mkdir()
            genesis_path = run_dir / 'genesis.json'
            genesis_path.write_text(json.dumps(genesis(args.slots)))
            cmd = [str(args.binary), 'node', '--chain', str(genesis_path.resolve()), '--dev',
                   '--dev.block-max-transactions', '1', '--builder.gaslimit', '30000000', '--datadir', str((run_dir / 'data').resolve()),
                   '--http', '--http.addr', '127.0.0.1', '--http.port', str(args.port),
                   '--http.api', 'eth,debug,flashbots', '--rpc.eth-proof-window', '3', '--authrpc.port', '0', '--port', '0',
                   '--ipcdisable', '--metrics', f'127.0.0.1:{args.port+1}',
                   '--rpc-cache.prewarm-bals=0']
            if cache_kind == 'cold':
                cmd += ['--rpc-cache.max-bals', '0']
            if enabled:
                cmd += ['--rpc.flashbots-parent-bal']
            log = (run_dir / 'node.log').open('w')
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 40
                while True:
                    if proc.poll() is not None:
                        raise RuntimeError(f'node exited; see {run_dir / "node.log"}')
                    try:
                        rpc(args.port, 'eth_chainId', [])
                        break
                    except (OSError, ValueError):
                        if time.monotonic() > deadline:
                            raise TimeoutError('node startup timed out')
                        time.sleep(0.1)
                # Warm execution/database code and allocator; leave a different parent per sample.
                for _ in range(2):
                    mine(args.port, 0, args.slots)
                for overlap in [0, 25, 100]:
                    first, repeated, records = [], [], []
                    for sample in range(args.samples):
                        mine(args.port, 0, args.slots)
                        child = mine(args.port, args.slots * (100-overlap) // 100, args.slots)
                        request = submission(args.port, child)
                        before = metrics(args.port+1)
                        rss_before = rss(proc.pid)
                        started = time.perf_counter_ns()
                        assert rpc(args.port, 'flashbots_validateBuilderSubmissionV6', [request]) is None
                        elapsed = (time.perf_counter_ns() - started)/1e6
                        after_first = metrics(args.port+1)
                        first.append(elapsed)
                        for _ in range(args.repeats):
                            started = time.perf_counter_ns()
                            assert rpc(args.port, 'flashbots_validateBuilderSubmissionV6', [request]) is None
                            repeated.append((time.perf_counter_ns()-started)/1e6)
                        after = metrics(args.port+1)
                        record = {'cache': cache_kind, 'enabled': enabled, 'overlap': overlap,
                                  'sample': sample, 'first_ms': elapsed, 'rss_before': rss_before,
                                  'rss_after': rss(proc.pid), 'state_root': child['stateRoot'],
                                  'block_hash': child['hash'], 'parent_hash': child['parentHash'],
                                  'child_bal_bytes': (len(request['execution_payload']['block_access_list'])-2)//2,
                                  'first_metrics': {k: after_first.get(k, 0)-before.get(k, 0) for k in set(before)|set(after_first)},
                                  'repeat_metrics': {k: after.get(k, 0)-after_first.get(k, 0) for k in set(after)|set(after_first)}}
                        # Inspect serialized parent size after all timed/metric intervals.
                        parent_bal = rpc(args.port, 'eth_getBlockAccessListRaw', [child['parentHash']])
                        record['parent_bal_bytes'] = (len(parent_bal)-2)//2 if parent_bal else 0
                        def read_count(kind, phase):
                            return sum(value for key, value in record[phase].items()
                                       if '_reads{' in key and f'kind="{kind}"' in key)
                        expected_hits = args.slots * overlap // 100 if enabled else 0
                        assert read_count('bal_slots', 'first_metrics') == expected_hits, record
                        assert read_count('provider_slots', 'first_metrics') == args.slots - expected_hits, record
                        assert all(value == 0 for value in record['repeat_metrics'].values()), record
                        record['evm_provider_slots'] = read_count('provider_slots', 'first_metrics')
                        record['evm_provider_accounts'] = read_count('provider_accounts', 'first_metrics')
                        record['bal_load_ms'] = 1000 * sum(value for key, value in record['first_metrics'].items()
                                                         if key.endswith('load_seconds_sum'))
                        records.append(record)
                        results['raw'].append(record)
                    row = {'cache': cache_kind, 'enabled': enabled, 'overlap': overlap,
                           'first_median_ms': statistics.median(first), 'first_p95_ms': percentile(first, .95),
                           'repeat_median_ms': statistics.median(repeated), 'repeat_p95_ms': percentile(repeated, .95),
                           'rss_after_median_bytes': statistics.median([r['rss_after'] for r in records]),
                           'bal_load_median_ms': statistics.median([r['bal_load_ms'] for r in records]),
                           'provider_slots_per_first': statistics.mean([r['evm_provider_slots'] for r in records]),
                           'provider_accounts_per_first': statistics.mean([r['evm_provider_accounts'] for r in records]),
                           'first_metrics_mean': {k: statistics.mean([r['first_metrics'].get(k, 0) for r in records])
                                                  for k in set().union(*(r['first_metrics'] for r in records))}}
                    results['rows'].append(row)
                    (args.output_dir / 'results.json').write_text(json.dumps(results, indent=2))
                    print(json.dumps(row), flush=True)
            finally:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=10)
                log.close()
    print(f'Full measurements: {args.output_dir / "results.json"}')


if __name__ == '__main__':
    main()
