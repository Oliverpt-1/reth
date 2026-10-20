#!/usr/bin/env python3
"""Stage I/O attribution using existing frozen databases; isolated Linux VM only."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

import bench_flashbots_parent_bal as base
import bench_flashbots_parent_bal_paired as paired
import bench_parent_bal_disk as disk


def diagnostics(path):
    return [json.loads(line.split('BAL_IO_DIAGNOSTIC ', 1)[1])
            for line in path.read_text().splitlines() if 'BAL_IO_DIAGNOSTIC ' in line]


def start_trace(pid, database, directory):
    stat = database.stat()
    device = (os.major(stat.st_dev) << 20) | os.minor(stat.st_dev)
    path = directory / 'faults.log'
    output = path.open('w')
    command = ['bpftrace', '-B', 'line', str(Path(__file__).with_name('parent_bal_faults.bt')),
               str(pid), str(stat.st_ino), str(device)]
    process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
    deadline = time.monotonic()+30
    while 'READY' not in path.read_text():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(f'trace failed: {path.read_text()}')
        time.sleep(.05)
    return process, output, {'command': command, 'inode': stat.st_ino,
                             'device': device, 'path': str(database)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--frozen', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--parents', type=int, default=8)
    parser.add_argument('--allow-isolated-vm-cache-drop', action='store_true', required=True)
    args = parser.parse_args()
    assert os.sysconf('SC_PAGE_SIZE') == 4096
    binary, frozen, output = args.binary.resolve(), args.frozen.resolve(), args.output.resolve()
    prior = json.loads((frozen/'results.json').read_text())
    assert 0 < args.parents <= len(prior['groups'])
    output.mkdir()
    shutil.copyfile(frozen/'genesis.json', output/'genesis.json')
    result = {'binary_version': subprocess.check_output([str(binary), '--version'], text=True),
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'tracer_sha256': hashlib.sha256(Path(__file__).with_name('parent_bal_faults.bt').read_bytes()).hexdigest(),
              'frozen_source_sha256': hashlib.sha256((frozen/'results.json').read_bytes()).hexdigest(),
              'kernel': subprocess.check_output(['uname', '-a'], text=True),
              'compiler': subprocess.check_output(['rustc', '-Vv'], text=True),
              'tracer': subprocess.check_output(['bpftrace', '--version'], text=True),
              'memory': Path('/proc/meminfo').read_text(),
              'groups': prior['groups'][:args.parents], 'runs': []}
    # Check default-off instrumentation and baseline acceptance before any diagnosis.
    directory = output/'default-off'
    disk.clone_state(frozen/'parent-0', directory)
    os.environ.pop('RETH_FLASHBOTS_IO_DIAGNOSTIC', None)
    node = disk.launch(binary, directory, None)
    try:
        request = result['groups'][0]['requests']['100'][0]
        assert base.rpc(node['port'], 'flashbots_validateBuilderSubmissionV6', [request]) is None
        assert diagnostics(directory/'node.log') == []
    finally:
        paired.stop_node(node)
    shutil.rmtree(directory)
    result['default_off_verified'] = True
    os.environ['RETH_FLASHBOTS_IO_DIAGNOSTIC'] = '1'
    for reversal in [False, True]:
        for group in result['groups']:
            for overlap in [100, 0]:
                modes = ['disabled', 'lazy'] if (group['group'] % 2 == 0) != reversal else ['lazy', 'disabled']
                for mode in modes:
                    for condition in (['cold', 'warm'] if reversal else ['warm', 'cold']):
                        name = f'{reversal}-{group["group"]}-{overlap}-{mode}-{condition}'
                        directory = output/name
                        disk.clone_state(frozen/f'parent-{group["group"]}', directory)
                        disk.condition_files(directory, condition)
                        node = disk.launch(binary, directory, None, mode)
                        tracer = None
                        run = {'group': group['group'], 'overlap': overlap, 'mode': mode,
                               'condition': condition, 'reversed': reversal, 'rows': [],
                               'command': node['command'], 'pid': node['process'].pid}
                        try:
                            disk.warm_executable_mappings(node['process'].pid, binary)
                            assert base.rpc(node['port'], 'eth_getBlockByNumber', ['latest', False])['hash'] == group['parent_hash']
                            preload = paired.metrics(node['port']+1)
                            assert base.rpc(node['port'], 'eth_getBlockAccessListRaw', [group['parent_hash']])
                            run['preload_metrics_delta'] = paired.delta(paired.metrics(node['port']+1), preload)
                            tracer, trace_output, identity = start_trace(node['process'].pid, directory/'data/db/mdbx.dat', directory)
                            run['database'] = identity
                            run['maps'] = Path(f'/proc/{node["process"].pid}/maps').read_text()
                            paired.await_idle([node])
                            requests = group['requests'][str(overlap)]
                            for phase, request in [('first', requests[0]), ('new_accesses', requests[1]), ('exact_repeat', requests[1])]:
                                before_metrics = paired.metrics(node['port']+1)
                                before = disk.io_snapshot(node['process'].pid)
                                # Correlate BPF monotonic timestamps with node wall-clock samples.
                                wall = time.time_ns()
                                mono = time.monotonic_ns()
                                assert base.rpc(node['port'], 'flashbots_validateBuilderSubmissionV6', [request]) is None
                                after = disk.io_snapshot(node['process'].pid)
                                values = paired.delta(paired.metrics(node['port']+1), before_metrics)
                                samples = diagnostics(directory/'node.log')
                                assert len(samples) == len(run['rows'])+1
                                sample = samples[-1]
                                assert sample['computed_root'] == request['execution_payload']['state_root']
                                assert sample['block'] == request['message']['block_hash']
                                assert [s['stage'] for s in sample['snapshots']] == ['validation_start', 'execution_start', 'execution_end', 'root_start', 'root_end']
                                expected = 64 if mode == 'lazy' and overlap == 100 and phase == 'first' else 0
                                assert paired.count(values, 'bal_slots') == expected
                                assert paired.count(values, 'provider_slots') == (0 if phase == 'exact_repeat' else 64-expected)
                                assert not any(v for k,v in values.items() if 'state_root_cache_' in k or 'bal_fetch_seconds_count' in k or 'bal_decode_seconds_count' in k)
                                run['rows'].append({'phase': phase, 'accepted': True, 'diagnostic': sample,
                                                    'wall_ns': wall, 'monotonic_ns': mono,
                                                    'metrics_delta': values, 'before': before, 'after': after})
                        finally:
                            if tracer:
                                tracer.send_signal(signal.SIGINT)
                                tracer.wait(timeout=15)
                                trace_output.close()
                            paired.stop_node(node)
                        run['trace'] = (directory/'faults.log').read_text()
                        assert 'Lost ' not in run['trace'] and 'ERROR' not in run['trace']
                        result['runs'].append(run)
                        (output/'results.json.gz').write_bytes(gzip.compress(json.dumps(result).encode(), mtime=0))
                        shutil.rmtree(directory)
                        print(name, flush=True)
    print('Completed', output/'results.json.gz')


if __name__ == '__main__':
    main()
