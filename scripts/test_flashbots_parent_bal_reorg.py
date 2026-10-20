#!/usr/bin/env python3
"""Check V6 validation across real Engine API reorgs on generated Amsterdam state.

This is a correctness regression, not a performance benchmark. It reuses the
existing node benchmark's signing, authenticated Engine RPC and metric helpers.
No public network, funded key, or historical database is used.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time
import traceback
import urllib.request

import bench_flashbots_parent_bal as base
import bench_flashbots_parent_bal_paired as paired
from eth_hash.auto import keccak
import rlp
from trie import HexaryTrie


MODES = ['disabled', 'lazy', 'eager-parent', 'eager-submission']


def poll(description, condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(.05)
    raise TimeoutError(description)


def launch(binary, directory, genesis, port, mode='disabled', bal_cache='normal'):
    node = paired.start_node(binary, directory, port, mode != 'disabled', False,
                             64, 'bal', mode, bal_cache, genesis_override=genesis)
    assert json.loads((directory / 'genesis.json').read_text()) == genesis
    return node


def choice(node, block_hash):
    result = paired.engine_rpc(node, 'engine_forkchoiceUpdatedV3', [{
        'headBlockHash': block_hash, 'safeBlockHash': base.ZERO,
        'finalizedBlockHash': base.ZERO}, None])
    assert result['payloadStatus']['status'] == 'VALID', result
    def observed():
        block = base.rpc(node['port'], 'eth_getBlockByNumber', ['latest', False])
        return block if block['hash'] == block_hash else None

    return poll('canonical head switch', observed)


def submit(node, fixture):
    result = paired.engine_rpc(node, 'engine_newPayloadV5', [fixture['payload'], [],
        fixture['parent_beacon_root'], fixture['execution_requests']])
    assert result['status'] == 'VALID', result
    return result


def header_bytes(payload, beacon_root):
    raw = lambda value: bytes.fromhex(value.removeprefix('0x'))
    integer = lambda key: int(payload[key], 16)
    txs = HexaryTrie({})
    for index, transaction in enumerate(payload['transactions']):
        txs[rlp.encode(index)] = raw(transaction)
    assert payload['withdrawals'] == []
    fields = [raw(payload['parentHash']), keccak(rlp.encode([])), raw(payload['feeRecipient']),
              raw(payload['stateRoot']), txs.root_hash, raw(payload['receiptsRoot']),
              raw(payload['logsBloom']), 0, integer('blockNumber'), integer('gasLimit'),
              integer('gasUsed'), integer('timestamp'), raw(payload['extraData']),
              raw(payload['prevRandao']), bytes(8), integer('baseFeePerGas'),
              HexaryTrie({}).root_hash, integer('blobGasUsed'), integer('excessBlobGas'),
              raw(beacon_root), hashlib.sha256(b'').digest(),
              keccak(raw(payload['blockAccessList'])), integer('slotNumber')]
    header = rlp.encode(fields)
    assert '0x' + keccak(header).hex() == payload['blockHash']
    return '0x' + header.hex()


def validation_request(payload, beacon_root, parent):
    names = [('parent_hash', 'parentHash'), ('fee_recipient', 'feeRecipient'),
             ('state_root', 'stateRoot'), ('receipts_root', 'receiptsRoot'),
             ('logs_bloom', 'logsBloom'), ('prev_randao', 'prevRandao'),
             ('extra_data', 'extraData'), ('block_hash', 'blockHash'),
             ('block_access_list', 'blockAccessList'), ('transactions', 'transactions'),
             ('withdrawals', 'withdrawals')]
    out = {snake: payload[camel] for snake, camel in names}
    for snake, camel in [('block_number', 'blockNumber'), ('gas_limit', 'gasLimit'),
                        ('gas_used', 'gasUsed'), ('timestamp', 'timestamp'),
                        ('base_fee_per_gas', 'baseFeePerGas'), ('blob_gas_used', 'blobGasUsed'),
                        ('excess_blob_gas', 'excessBlobGas'), ('slot_number', 'slotNumber')]:
        out[snake] = str(int(payload[camel], 16))
    return {'message': {'slot': out['slot_number'], 'parent_hash': out['parent_hash'],
                'block_hash': out['block_hash'], 'builder_pubkey': '0x' + '00' * 48,
                'proposer_pubkey': '0x' + '00' * 48,
                'proposer_fee_recipient': out['fee_recipient'],
                'gas_limit': out['gas_limit'], 'gas_used': out['gas_used'], 'value': '0'},
            'execution_payload': out,
            'execution_requests': {name: [] for name in ['deposits', 'withdrawals',
                'consolidations', 'builder_deposits', 'builder_exits']},
            'blobs_bundle': {'commitments': [], 'proofs': [], 'blobs': []},
            'signature': '0x' + '00' * 96,
            'registered_gas_limit': str(int(parent['gasLimit'], 16)),
            'parent_beacon_block_root': beacon_root}


def build(node, parent, name, offset, increment, serial):
    assert choice(node, parent['hash'])['hash'] == parent['hash']
    base.rpc(node['port'], 'debug_clearTxpool', [])
    nonce = int(base.rpc(node['port'], 'eth_getTransactionCount',
                         [base.DEV_ACCOUNT.address, 'latest']), 16)
    transaction = base.DEV_ACCOUNT.sign_transaction({
        'chainId': 1337, 'nonce': nonce, 'gasPrice': 10**9, 'gas': 3_000_000,
        'to': base.CONTRACT, 'value': 0,
        'data': offset.to_bytes(32, 'big') + increment.to_bytes(32, 'big')})
    raw_transaction = '0x' + transaction.raw_transaction.hex()
    tx_hash = base.rpc(node['port'], 'eth_sendRawTransaction', [raw_transaction])
    poll('transaction enters pool', lambda: base.rpc(
        node['port'], 'eth_getTransactionByHash', [tx_hash]))
    beacon_root = '0x' + hashlib.sha256(('beacon/' + name).encode()).hexdigest()
    attributes = {'timestamp': hex(int(parent['timestamp'], 16) + serial + 1),
                  'prevRandao': '0x' + hashlib.sha256(name.encode()).hexdigest(),
                  'suggestedFeeRecipient': '0x' + '22' * 20, 'withdrawals': [],
                  'parentBeaconBlockRoot': beacon_root,
                  'slotNumber': hex(int(parent['number'], 16) + 1)}
    result = paired.engine_rpc(node, 'engine_forkchoiceUpdatedV4', [{
        'headBlockHash': parent['hash'], 'safeBlockHash': base.ZERO,
        'finalizedBlockHash': base.ZERO}, attributes, None])
    assert result['payloadStatus']['status'] == 'VALID', result
    envelope = paired.engine_rpc(node, 'engine_getPayloadV6', [result['payloadId']])
    payload = envelope['executionPayload']
    assert payload['transactions'] == [raw_transaction], envelope
    assert envelope['executionRequests'] == [], envelope
    assert int(payload['blobGasUsed'], 16) == 0
    fixture = {'name': name, 'payload': payload, 'parent_beacon_root': beacon_root,
               'execution_requests': [], 'offset': offset, 'increment': increment,
               'raw_header': header_bytes(payload, beacon_root),
               'request': validation_request(payload, beacon_root, parent)}
    return fixture


def response(node, request):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1,
        'method': 'flashbots_validateBuilderSubmissionV6', 'params': [request]}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{node["port"]}', body,
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as stream:
        value = json.load(stream)
    value.pop('id', None)
    value.pop('jsonrpc', None)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=29545)
    parser.add_argument('--modes', nargs='+', choices=MODES, default=MODES)
    parser.add_argument('--bal-caches', nargs='+', choices=['normal', 'disabled'],
                        default=['normal', 'disabled'])
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output_dir.resolve()
    output.mkdir()
    genesis = base.genesis(64)
    code = bytearray()
    for key in range(64):
        code += b'\x61' + key.to_bytes(2, 'big')
        code += bytes.fromhex('600035018054602035019055')
    genesis['alloc'][base.CONTRACT]['code'] = '0x' + (code + b'\x00').hex()
    record = {'scope': 'actual-node generated Amsterdam two-block reorg correctness; no latency benchmark',
              'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'binary_version': subprocess.check_output([str(binary), '--version'], text=True).strip(),
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'helper_sha256': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                               for name in ['bench_flashbots_parent_bal.py', 'bench_flashbots_parent_bal_paired.py']},
              'platform': platform.platform(), 'fixtures': {}, 'rows': [], 'switches': [],
              'root_result_cache': False, 'status': 'started'}
    path = output / 'results.json'
    nodes = []
    try:
        producer = launch(binary, output / 'producer', genesis, args.port)
        nodes.append(producer)
        root = base.rpc(args.port, 'eth_getBlockByNumber', ['latest', False])
        common = build(producer, root, 'common', 0, 1, 1)
        submit(producer, common)
        common_header = choice(producer, common['payload']['blockHash'])
        fixtures = {'common': common}
        for branch, increment in [('a', 2), ('b', 7)]:
            fixtures[branch + '1'] = build(producer, common_header, branch + '1', 0, increment, 2)
        for branch in ['a', 'b']:
            submit(producer, fixtures[branch + '1'])
        for branch, increment in [('a', 3), ('b', 11)]:
            parent = choice(producer, fixtures[branch + '1']['payload']['blockHash'])
            fixtures[branch + '2'] = build(producer, parent, branch + '2', 0, increment, 3)
        for branch in ['a', 'b']:
            submit(producer, fixtures[branch + '2'])
        for branch in ['a', 'b']:
            parent = choice(producer, fixtures[branch + '2']['payload']['blockHash'])
            for name, offset in [('full', 0), ('partial', 32)]:
                fixtures[branch + '_' + name] = build(
                    producer, parent, branch + '_' + name, offset, 5, 4)
        assert fixtures['a2']['payload']['stateRoot'] != fixtures['b2']['payload']['stateRoot']
        assert fixtures['a_full']['payload']['stateRoot'] != fixtures['b_full']['payload']['stateRoot']
        record['genesis'] = genesis
        record['fixtures'] = fixtures
        paired.stop_node(producer)
        nodes.remove(producer)
        path.write_text(json.dumps(record, indent=2) + '\n')

        for bal_cache in args.bal_caches:
            for mode in args.modes:
                node = launch(binary, output / f'{bal_cache}-{mode}', genesis,
                              args.port, mode, bal_cache)
                nodes.append(node)
                for name in ['common', 'a1', 'a2']:
                    submit(node, fixtures[name])
                    choice(node, fixtures[name]['payload']['blockHash'])
                for name in ['b1', 'b2']:
                    submit(node, fixtures[name])
                # A -> B -> A -> B -> A checks two-block reorgs and switching back.
                for stage, branch in enumerate(['a', 'b', 'a', 'b', 'a']):
                    head = choice(node, fixtures[branch + '2']['payload']['blockHash'])
                    expected = 7 if branch == 'a' else 20
                    stored = base.rpc(node['port'], 'eth_getStorageAt',
                                      [base.CONTRACT, '0x0', 'latest'])
                    assert int(stored, 16) == expected, (branch, stored)
                    parent_bal = base.rpc(node['port'], 'eth_getBlockAccessListRaw', [head['hash']])
                    assert parent_bal == fixtures[branch + '2']['payload']['blockAccessList']
                    assert '0x' + keccak(bytes.fromhex(parent_bal[2:])).hex() == head['blockAccessListHash']
                    record['switches'].append({'mode': mode, 'bal_cache': bal_cache,
                        'stage': stage, 'branch': branch, 'head': head['hash'],
                        'state_root': head['stateRoot'], 'slot_zero': expected,
                        'parent_bal_matches_header_and_fixture': True})
                    for requested_branch in [branch, 'b' if branch == 'a' else 'a']:
                        for kind in ['full', 'partial']:
                            fixture = fixtures[requested_branch + '_' + kind]
                            for repeat in range(2):
                                before = paired.metrics(node['port'] + 1)
                                result = response(node, fixture['request'])
                                values = paired.delta(paired.metrics(node['port'] + 1), before)
                                canonical = requested_branch == branch
                                if canonical:
                                    assert result == {'result': None}, result
                                    hits = 64 if mode != 'disabled' and kind == 'full' and repeat == 0 else 0
                                    provider = (64 if mode == 'disabled' else 0) if kind == 'full' else 32
                                    provider = 0 if repeat else provider
                                    assert paired.count(values, 'bal_slots') == hits, values
                                    assert paired.count(values, 'provider_slots') == provider, values
                                else:
                                    # The existing provider exposes canonical/pending state, not
                                    # arbitrary executed side branches. This exact baseline
                                    # rejection must remain identical with BAL enabled.
                                    assert result == {'error': {'code': -32603,
                                        'message': 'parent block not found'}}, result
                                    assert paired.count(values, 'provider_slots') == 0
                                    assert paired.count(values, 'bal_slots') == 0
                                assert not any(v for k, v in values.items() if 'state_root_cache_' in k)
                                record['rows'].append({'mode': mode, 'bal_cache': bal_cache,
                                    'stage': stage, 'head_branch': branch,
                                    'requested_branch': requested_branch, 'canonical_parent': canonical,
                                    'kind': kind, 'repeat': repeat, 'response': result,
                                    'parent_hash': fixture['payload']['parentHash'],
                                    'block_hash': fixture['payload']['blockHash'],
                                    'state_root': fixture['payload']['stateRoot'],
                                    'metrics_delta': values,
                                    'provider_slots': paired.count(values, 'provider_slots'),
                                    'bal_slots': paired.count(values, 'bal_slots')})
                            if requested_branch == branch:
                                other = fixtures[('b' if branch == 'a' else 'a') + '_' + kind]
                                invalid = paired.header_variant(fixture['request'], fixture['raw_header'],
                                    state_root=bytes.fromhex(other['payload']['stateRoot'][2:]))
                                result = response(node, invalid)
                                assert 'mismatched block state root' in result.get('error', {}).get('message', ''), result
                                record['rows'].append({'mode': mode, 'bal_cache': bal_cache,
                                    'stage': stage, 'head_branch': branch, 'kind': kind,
                                    'invalid_cross_fork_root': True, 'response': result})
                    path.write_text(json.dumps(record, indent=2) + '\n')
                paired.stop_node(node)
                nodes.remove(node)
                print(json.dumps({'mode': mode, 'bal_cache': bal_cache,
                                  'rows_so_far': len(record['rows'])}), flush=True)
        baseline = {}
        for row in record['rows']:
            key = (row['bal_cache'], row['stage'], row.get('requested_branch'),
                   row['kind'], row.get('repeat'), row.get('invalid_cross_fork_root', False))
            if row['mode'] == 'disabled':
                baseline[key] = row['response']
            else:
                assert row['response'] == baseline[key], (key, row)
        record['status'] = 'passed'
        record['all_responses_match_disabled'] = True
    except BaseException:
        record['status'] = 'failed'
        record['failure'] = traceback.format_exc()
        raise
    finally:
        for node in nodes:
            paired.stop_node(node)
        path.write_text(json.dumps(record, indent=2) + '\n')
    print(f'Passed: {path}', flush=True)


if __name__ == '__main__':
    main()
