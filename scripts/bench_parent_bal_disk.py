#!/usr/bin/env python3
"""Disposable Linux-VM validation experiment; never run cache eviction on a shared host.

Uses generated state and the public development key from the existing harness.
The --allow-isolated-vm-cache-drop acknowledgement is mandatory. Guest read I/O
does not establish a miss in the host/hypervisor or physical SSD caches.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import bench_flashbots_parent_bal as base
import bench_flashbots_parent_bal_paired as paired


def launch(binary, directory, genesis, mode='disabled', producer=False):
    directory.mkdir(exist_ok=True)
    genesis_path = directory.parent / 'genesis.json'
    jwt = directory / 'jwt.hex'
    jwt.write_text(os.urandom(32).hex())
    cmd = [str(binary), 'node', '--chain', str(genesis_path), '--datadir', str(directory / 'data'),
           '--http', '--http.addr', '127.0.0.1', '--http.port', '28545',
           '--http.api', 'eth,debug,flashbots', '--authrpc.port', '28547',
           '--authrpc.jwtsecret', str(jwt), '--port', '0', '--disable-discovery', '--ipcdisable',
           '--metrics', '127.0.0.1:28546', '--rpc-cache.prewarm-bals=0',
           '--engine.persistence-threshold', '0', '--engine.memory-block-buffer-target', '0',
           '--builder.gaslimit', '30000000']
    if producer:
        cmd += ['--dev', '--dev.block-max-transactions', '1']
    if mode != 'disabled':
        cmd += ['--rpc.flashbots-parent-bal-mode', mode]
    log = (directory / 'node.log').open('w')
    process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    node = {'process': process, 'log': log, 'port': 28545, 'command': cmd}
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'node exited: {directory / "node.log"}')
        try:
            base.rpc(28545, 'eth_chainId', [])
            if producer:
                # RPC binds before the dev miner subscribes to pool events.
                time.sleep(.5)
            return node
        except OSError:
            time.sleep(.1)
    paired.stop_node(node)
    raise TimeoutError('startup')


def io_snapshot(pid):
    stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    io = dict(line.split(': ') for line in Path(f'/proc/{pid}/io').read_text().splitlines())
    # Process major faults include non-database mappings too; retain MDBX RSS
    # separately and do not label every process read as a provider-state read.
    mdbx_rss = 0
    current = False
    for line in Path(f'/proc/{pid}/smaps').read_text().splitlines():
        if '-' in line.split()[0]:
            current = 'mdbx.dat' in line
        elif current and line.startswith('Rss:'):
            mdbx_rss += int(line.split()[1]) * 1024
    disks = {}
    for line in Path('/proc/diskstats').read_text().splitlines():
        fields = line.split()
        if fields[2].startswith(('vd', 'sd', 'nvme')):
            disks[fields[2]] = int(fields[5]) * 512
    return {'major_faults': int(stat[9]), 'minor_faults': int(stat[7]),
            'read_bytes': int(io['read_bytes']), 'rss_bytes': base.rss(pid),
            'mdbx_rss_bytes': mdbx_rss, 'device_read_bytes': disks}


def allocated(directory):
    return sum(p.stat().st_blocks * 512 for p in directory.rglob('*') if p.is_file())


def warm_executable_mappings(pid, binary):
    # Avoid conflating cold validation code/shared libraries with cold state.
    paths = {str(binary)}
    for line in Path(f'/proc/{pid}/maps').read_text().splitlines():
        path = line.split()[-1]
        if path.startswith('/') and '.so' in Path(path).name:
            paths.add(path)
    for path in paths:
        with open(path, 'rb') as stream:
            while stream.read(1024 * 1024):
                pass


def clone_state(source, destination):
    destination.mkdir()
    subprocess.run(['cp', '-a', '--reflink=never', '--sparse=always',
                    str(source / 'data'), str(destination / 'data')], check=True)


def condition_files(directory, condition):
    os.sync()
    if condition == 'cold':
        Path('/proc/sys/vm/drop_caches').write_text('3\n')
    else:
        for path in (directory / 'data').rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    # MDBX's logical map is much larger than its allocated extents.
                    position, size = 0, path.stat().st_size
                    while position < size:
                        try:
                            start = os.lseek(stream.fileno(), position, os.SEEK_DATA)
                        except OSError as error:
                            if error.errno == 6:  # ENXIO: no more allocated extents.
                                break
                            raise
                        end = os.lseek(stream.fileno(), start, os.SEEK_HOLE)
                        stream.seek(start)
                        while stream.tell() < end:
                            stream.read(min(1024 * 1024, end-stream.tell()))
                        position = end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--parents', type=int, default=8)
    parser.add_argument('--slots', type=int, default=64)
    parser.add_argument('--state-slots', type=int, default=262144)
    parser.add_argument('--allow-isolated-vm-cache-drop', action='store_true', required=True)
    args = parser.parse_args()
    assert 0 < args.parents <= 16 and args.state_slots >= 196608
    binary, output = args.binary.resolve(), args.output_dir.resolve()
    output.mkdir()
    genesis = base.genesis(args.slots)
    # Scatter accesses across plain-storage pages, not just adjacent slots.
    code = bytearray()
    for key in range(args.slots):
        code += b'\x62' + (key * 1024).to_bytes(3, 'big')
        code += bytes.fromhex('6000350180546001019055')
    genesis['alloc'][base.CONTRACT]['code'] = '0x' + (code + b'\x00').hex()
    genesis['alloc'][base.CONTRACT]['storage'] = {
        f'0x{k:064x}': f'0x{k+1:064x}' for k in range(args.state_slots)}
    result = {'scope': 'generated Amsterdam state; Linux guest file-cache experiment',
              'source_revision': os.environ.get('BENCH_SOURCE_REVISION'),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'compiler': subprocess.check_output(['rustc', '-Vv'], text=True),
              'cpu': subprocess.check_output(['lscpu'], text=True),
              'build_command': 'CARGO_BUILD_JOBS=4 CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 cargo build --locked --release -p reth --bin reth --no-default-features',
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'binary_version': subprocess.check_output([str(binary), '--version'], text=True),
              'kernel': subprocess.check_output(['uname', '-a'], text=True),
              'memory': Path('/proc/meminfo').read_text(),
              'cgroup_memory_max': Path('/sys/fs/cgroup/memory.max').read_text(),
              'cgroup_cpu_max': Path('/sys/fs/cgroup/cpu.max').read_text(),
              'filesystem': subprocess.check_output(['df', '-T', str(output)], text=True),
              'slots': args.slots, 'state_slots': args.state_slots,
              'root_result_cache': False, 'decoded_bal_cache': 'preloaded', 'rows': [], 'groups': []}
    (output / 'genesis.json').write_text(json.dumps(genesis))
    source = output / 'source'
    node = launch(binary, source, genesis, producer=True)
    paired.stop_node(node)
    for group in range(args.parents):
        node = launch(binary, source, genesis, producer=True)
        try:
            parent = base.mine(node['port'], group * 64, args.slots)
            paired.await_idle([node])
        finally:
            paired.stop_node(node)
        frozen = output / f'parent-{group}'
        clone_state(source, frozen)
        info = {'group': group, 'parent_hash': parent['hash'],
                'database_allocated_bytes': allocated(frozen / 'data'), 'requests': {}}
        for overlap in [100, 50, 0]:
            requests = []
            for candidate, offset in enumerate([
                group * 64 + (100-overlap) * args.slots // 100 * 1024,
                group * 64 + 128 * 1024,
            ]):
                prep = output / f'prepare-{group}-{overlap}-{candidate}'
                clone_state(frozen, prep)
                node = launch(binary, prep, genesis, producer=True)
                try:
                    child = base.mine(node['port'], offset, args.slots)
                    assert child['parentHash'] == parent['hash'], (parent, child)
                    requests.append(base.submission(node['port'], child))
                finally:
                    paired.stop_node(node)
                shutil.rmtree(prep)
            info['requests'][str(overlap)] = requests
        result['groups'].append(info)
    (output / 'results.json').write_text(json.dumps(result, indent=2))
    modes = ['disabled', 'lazy', 'eager-parent', 'eager-submission']
    for info in result['groups']:
        for mode in modes:
            directory = output / f'preflight-{info["group"]}-{mode}'
            clone_state(output / f'parent-{info["group"]}', directory)
            node = launch(binary, directory, genesis, mode)
            try:
                for requests in info['requests'].values():
                    for request in requests:
                        assert base.rpc(node['port'], 'flashbots_validateBuilderSubmissionV6', [request]) is None
            finally:
                paired.stop_node(node)
            shutil.rmtree(directory)
    result['preflight_all_modes_accepted'] = True
    # Preparation ends here. Each treatment starts from its frozen parent.
    for reversal in [False, True]:
        for info in result['groups']:
            group = info['group']
            for overlap in [100, 50, 0]:
                requests = info['requests'][str(overlap)]
                order = modes if (group % 2 == 0) != reversal else modes[::-1]
                for mode in order:
                    for condition in (['cold', 'warm'] if reversal else ['warm', 'cold']):
                        directory = output / f'run-{reversal}-{group}-{overlap}-{mode}-{condition}'
                        clone_state(output / f'parent-{group}', directory)
                        condition_files(directory, condition)
                        node = launch(binary, directory, genesis, mode)
                        try:
                            warm_executable_mappings(node['process'].pid, binary)
                            assert base.rpc(node['port'], 'eth_getBlockByNumber', ['latest', False])['hash'] == info['parent_hash']
                            preload_metrics = paired.metrics(node['port']+1)
                            preload_start = time.perf_counter_ns()
                            assert base.rpc(node['port'], 'eth_getBlockAccessListRaw', [info['parent_hash']])
                            preload_ms = (time.perf_counter_ns()-preload_start)/1e6
                            preload_delta = paired.delta(paired.metrics(node['port']+1), preload_metrics)
                            paired.await_idle([node])
                            for phase, request in [('first', requests[0]), ('new_accesses', requests[1]), ('exact_repeat', requests[1])]:
                                before_metrics = paired.metrics(node['port']+1)
                                before = io_snapshot(node['process'].pid)
                                started = time.perf_counter_ns()
                                response = base.rpc(node['port'], 'flashbots_validateBuilderSubmissionV6', [request])
                                elapsed = (time.perf_counter_ns()-started)/1e6
                                after = io_snapshot(node['process'].pid)
                                values = paired.delta(paired.metrics(node['port']+1), before_metrics)
                                assert response is None
                                assert not any(v for k,v in values.items() if 'state_root_cache_' in k)
                                assert not any(v for k,v in values.items()
                                               if ('bal_fetch_seconds_count' in k or 'bal_decode_seconds_count' in k))
                                provider = paired.count(values, 'provider_slots')
                                hits = paired.count(values, 'bal_slots')
                                expected_hits = args.slots * overlap // 100 if mode != 'disabled' and phase == 'first' else 0
                                assert hits == expected_hits, (mode, phase, hits, expected_hits)
                                assert provider == (0 if phase == 'exact_repeat' else args.slots-expected_hits), (mode, phase, provider)
                                row = {'group': group, 'overlap': overlap, 'mode': mode, 'condition': condition,
                                       'reversed': reversal, 'phase': phase, 'latency_ms': elapsed,
                                       'accepted': True, 'claimed_and_validated_root': request['execution_payload']['state_root'],
                                       'block_hash': request['message']['block_hash'], 'parent_hash': info['parent_hash'],
                                       'provider_slots': provider, 'bal_slots': hits,
                                       'provider_accounts': paired.count(values, 'provider_accounts'),
                                       'provider_code': paired.count(values, 'provider_code'),
                                       'stages_us': paired.stages(values), 'metrics_delta': values,
                                       'preload_ms': preload_ms, 'preload_metrics_delta': preload_delta,
                                       'shared_bal_cache_before': {k: v for k, v in before_metrics.items()
                                                                   if 'rpc_eth_cache' in k and 'bal' in k},
                                       'io_before': before, 'io_after': after, 'command': node['command']}
                                result['rows'].append(row)
                        finally:
                            paired.stop_node(node)
                        (output / 'results.json').write_text(json.dumps(result, indent=2))
                        print(json.dumps({k: row[k] for k in ['group','mode','condition','reversed','overlap','phase']}), flush=True)
                        shutil.rmtree(directory)
    print(f'Completed: {output / "results.json"}')


if __name__ == '__main__':
    main()
