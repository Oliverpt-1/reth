#!/usr/bin/env python3
"""Paired V6 replay: mirror identical blocks into two isolated Amsterdam nodes.

Uses bench_flashbots_parent_bal's public development key only in a custom genesis.
Requires eth-account. Timed requests alternate in seeded random order; Engine API
imports, mining, metrics, bootstrap analysis, and RSS sampling are outside timing.
"""
import argparse
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import statistics
import subprocess
import time
import urllib.request

import bench_flashbots_parent_bal as base


def metrics(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=10) as response:
        lines = response.read().decode().splitlines()
    values = {}
    for line in lines:
        match = re.match(r'((?:reth_)?builder_validation_(?:parent_bal_|stage_)\S+)\s+(\S+)', line)
        if match and 'quantile=' not in match[1]:
            values[match[1]] = float(match[2])
    return values


def delta(after, before):
    return {key: after.get(key, 0)-before.get(key, 0) for key in set(after)|set(before)}


def stages(values):
    out = {}
    for key, value in values.items():
        if '_stage_' in key and key.endswith('_seconds_sum'):
            count = values.get(key.removesuffix('_sum')+'_count', 0)
            name = key.split('_stage_')[1].removesuffix('_seconds_sum')
            out[name] = value/count*1e6 if count else 0
    return out


def count(values, kind):
    return sum(v for k, v in values.items() if '_reads{' in k and f'kind="{kind}"' in k)


def engine_rpc(node, method, params):
    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=')
    header = b64(b'{"alg":"HS256","typ":"JWT"}')
    claims = b64(json.dumps({'iat': int(time.time())}).encode())
    unsigned = header+b'.'+claims
    token = (unsigned+b'.'+b64(hmac.new(node['jwt'], unsigned, hashlib.sha256).digest())).decode()
    request = urllib.request.Request(
        f'http://127.0.0.1:{node["port"]+2}',
        json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
        {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if 'error' in result:
        raise RuntimeError(f'{method}: {result["error"]}')
    return result['result']


def mirror(producer, follower, block):
    payload = {out: block[inp] for out, inp in [
        ('parentHash', 'parentHash'), ('feeRecipient', 'miner'), ('stateRoot', 'stateRoot'),
        ('receiptsRoot', 'receiptsRoot'), ('logsBloom', 'logsBloom'), ('prevRandao', 'mixHash'),
        ('blockNumber', 'number'), ('gasLimit', 'gasLimit'), ('gasUsed', 'gasUsed'),
        ('timestamp', 'timestamp'), ('extraData', 'extraData'), ('baseFeePerGas', 'baseFeePerGas'),
        ('blockHash', 'hash'), ('blobGasUsed', 'blobGasUsed'), ('excessBlobGas', 'excessBlobGas'),
        ('slotNumber', 'slotNumber')]}
    payload['withdrawals'] = block.get('withdrawals', [])
    payload['transactions'] = [base.rpc(producer['port'], 'debug_getRawTransaction', [tx])
                               for tx in block['transactions']]
    payload['blockAccessList'] = base.rpc(producer['port'], 'eth_getBlockAccessListRaw', [block['hash']])
    status = engine_rpc(follower, 'engine_newPayloadV5',
                        [payload, [], block['parentBeaconBlockRoot'], []])
    assert status['status'] == 'VALID', status
    choice = engine_rpc(follower, 'engine_forkchoiceUpdatedV3',
                        [{'headBlockHash': block['hash'], 'safeBlockHash': base.ZERO,
                          'finalizedBlockHash': base.ZERO}, None])
    assert choice['payloadStatus']['status'] == 'VALID', choice
    imported = base.rpc(follower['port'], 'eth_getBlockByNumber', ['latest', False])
    assert imported['hash'] == block['hash'] and imported['stateRoot'] == block['stateRoot']


def start_node(binary, directory, port, enabled, producer, slots):
    directory.mkdir()
    genesis = directory/'genesis.json'
    genesis.write_text(json.dumps(base.genesis(slots)))
    # Ephemeral authentication only for these isolated test nodes. Never print the key/token.
    jwt = os.urandom(32)
    jwt_path = directory/'jwt.hex'
    jwt_path.write_text(jwt.hex())
    cmd = [str(binary), 'node', '--chain', str(genesis.resolve()), '--builder.gaslimit', '30000000',
           '--datadir', str((directory/'data').resolve()), '--http', '--http.addr', '127.0.0.1',
           '--http.port', str(port), '--http.api', 'eth,debug,flashbots', '--rpc.eth-proof-window', '3',
           '--authrpc.port', str(port+2), '--authrpc.jwtsecret', str(jwt_path.resolve()),
           '--port', '0', '--disable-discovery', '--ipcdisable',
           '--metrics', f'127.0.0.1:{port+1}', '--rpc-cache.prewarm-bals=0']
    if producer:
        cmd += ['--dev', '--dev.block-max-transactions', '1']
    if enabled:
        cmd += ['--rpc.flashbots-parent-bal']
    log = (directory/'node.log').open('w')
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    node = {'process': proc, 'log': log, 'port': port, 'jwt': jwt, 'enabled': enabled}
    try:
        deadline = time.monotonic()+40
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f'node exited: {directory / "node.log"}')
            try:
                assert base.rpc(port, 'eth_chainId', []) == '0x539'
                return node
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError('node startup timed out')
                time.sleep(.1)
    except BaseException:
        stop_node(node)
        raise


def stop_node(node):
    proc = node['process']
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=10)
    node['log'].close()


def paired_change(pairs, phase, rng):
    # Resample whole parents, not correlated repeat requests, for the confidence interval.
    log_ratios = [math.log(statistics.median(p['on'][phase])/statistics.median(p['off'][phase]))
                  for p in pairs]
    boot = sorted(statistics.median(rng.choices(log_ratios, k=len(log_ratios))) for _ in range(10000))
    return {'median_percent': 100*math.expm1(statistics.median(log_ratios)),
            'ci95_percent': [100*math.expm1(boot[250]), 100*math.expm1(boot[9749])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=100)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--slots', type=int, nargs='+', default=[64, 512])
    parser.add_argument('--overlaps', type=int, nargs='+', default=[0, 25, 100])
    parser.add_argument('--port', type=int, default=18545)
    parser.add_argument('--seed', type=int, default=1729)
    parser.add_argument('--reverse-nodes', action='store_true', help='optimized node produces the blocks')
    args = parser.parse_args()
    assert args.samples > 0 and args.repeats > 0
    assert all(0 <= value <= 100 for value in args.overlaps)
    args.binary = args.binary.resolve()
    args.output_dir.mkdir()
    rng = random.Random(args.seed)
    results = {'binary': str(args.binary),
               'binary_sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest(),
               'version': subprocess.check_output([str(args.binary), '--version'], text=True).strip(),
               'samples': args.samples, 'repeats': args.repeats, 'seed': args.seed,
               'reverse_nodes': args.reverse_nodes, 'cpu_quota': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
               'memory_limit_bytes': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
               'scope': 'identical payloads on isolated Amsterdam nodes; seeded randomized paired order',
               'rows': [], 'raw': []}
    for slots in args.slots:
        run_dir = args.output_dir/f'slots-{slots}'
        run_dir.mkdir()
        nodes = []
        try:
            for enabled in [False, True]:
                nodes.append(start_node(args.binary, run_dir/str(enabled), args.port+10*int(enabled),
                                        enabled, enabled == args.reverse_nodes, slots))
            producer = nodes[int(args.reverse_nodes)]
            follower = nodes[int(not args.reverse_nodes)]
            for _ in range(4):
                mirror(producer, follower, base.mine(producer['port'], 0, slots))
            for overlap in args.overlaps:
                pairs = []
                for sample in range(args.samples):
                    mirror(producer, follower, base.mine(producer['port'], 0, slots))
                    child = base.mine(producer['port'], slots*(100-overlap)//100, slots)
                    mirror(producer, follower, child)
                    request = base.submission(producer['port'], child)
                    pair = {'slots': slots, 'overlap': overlap, 'sample': sample,
                            'parent_hash': child['parentHash'], 'block_hash': child['hash'],
                            'state_root': child['stateRoot'], 'off': {}, 'on': {}}
                    before = [metrics(node['port']+1) for node in nodes]
                    rss_before = [base.rss(node['process'].pid) for node in nodes]
                    order = [0, 1]
                    rng.shuffle(order)
                    pair['first_order'] = order.copy()
                    for i in order:
                        start = time.perf_counter_ns()
                        assert base.rpc(nodes[i]['port'], 'flashbots_validateBuilderSubmissionV6', [request]) is None
                        pair['on' if i else 'off']['first_ms'] = [(time.perf_counter_ns()-start)/1e6]
                    after_first = [metrics(node['port']+1) for node in nodes]
                    for _ in range(args.repeats):
                        rng.shuffle(order)
                        for i in order:
                            start = time.perf_counter_ns()
                            assert base.rpc(nodes[i]['port'], 'flashbots_validateBuilderSubmissionV6', [request]) is None
                            pair['on' if i else 'off'].setdefault('repeat_ms', []).append(
                                (time.perf_counter_ns()-start)/1e6)
                    after = [metrics(node['port']+1) for node in nodes]
                    for i, node in enumerate(nodes):
                        record = pair['on' if i else 'off']
                        first_metrics = delta(after_first[i], before[i])
                        repeat_metrics = delta(after[i], after_first[i])
                        expected_hits = slots*overlap//100 if i else 0
                        assert count(first_metrics, 'bal_slots') == expected_hits, first_metrics
                        assert count(first_metrics, 'provider_slots') == slots-expected_hits, first_metrics
                        assert all(value == 0 for key, value in repeat_metrics.items()
                                   if 'parent_bal_' in key), repeat_metrics
                        record.update(first_stages_us=stages(first_metrics), repeat_stages_us=stages(repeat_metrics),
                                      first_provider_slots=count(first_metrics, 'provider_slots'),
                                      first_provider_accounts=count(first_metrics, 'provider_accounts'),
                                      bal_load_us=1e6*sum(v for k, v in first_metrics.items() if k.endswith('load_seconds_sum')),
                                      bal_loads=sum(v for k, v in first_metrics.items() if k.endswith('_loads')),
                                      rss_before=rss_before[i], rss_after=base.rss(node['process'].pid))
                    pairs.append(pair)
                    results['raw'].append(pair)
                row = {'slots': slots, 'overlap': overlap,
                       'first_change': paired_change(pairs, 'first_ms', random.Random(args.seed+slots+overlap)),
                       'repeat_change': paired_change(pairs, 'repeat_ms', random.Random(args.seed+slots+overlap+1))}
                for name in ['off', 'on']:
                    first = [p[name]['first_ms'][0] for p in pairs]
                    repeats = [value for p in pairs for value in p[name]['repeat_ms']]
                    row[name] = {'first_median_ms': statistics.median(first), 'first_p95_ms': base.percentile(first, .95),
                                 'repeat_median_ms': statistics.median(repeats), 'repeat_p95_ms': base.percentile(repeats, .95),
                                 'bal_load_median_us': statistics.median(p[name]['bal_load_us'] for p in pairs),
                                 'provider_slots': statistics.mean(p[name]['first_provider_slots'] for p in pairs),
                                 'provider_accounts': statistics.mean(p[name]['first_provider_accounts'] for p in pairs),
                                 'rss_median_bytes': statistics.median(p[name]['rss_after'] for p in pairs)}
                    for phase in ['first', 'repeat']:
                        key = phase+'_stages_us'
                        row[name][key] = {stage: statistics.mean(p[name][key].get(stage, 0) for p in pairs)
                                         for stage in set().union(*(p[name][key] for p in pairs))}
                results['rows'].append(row)
                (args.output_dir/'results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps(row), flush=True)
        finally:
            for node in reversed(nodes):
                stop_node(node)
    print(f'Full paired measurements: {args.output_dir / "results.json"}')


if __name__ == '__main__':
    main()
