#!/usr/bin/env python3
"""Attribute diagnostic counters and filemap events to validation stages."""

import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import statistics as stats


def counters(snapshot):
    fields = snapshot['process_stat'].rsplit(')', 1)[1].split()
    io = dict(line.split(': ') for line in snapshot['process_io'].splitlines())
    return {'read_bytes': int(io['read_bytes']), 'major_faults': int(fields[9])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with gzip.open(args.input, 'rt') as stream:
        raw = json.load(stream)
    rows = []
    for run in raw['runs']:
        faults, fills = [], []
        for line in run['trace'].splitlines():
            fields = line.split()
            if fields and fields[0] == 'F':
                start, end, pid, tid, device, inode, offset, flags = map(int, fields[1:])
                faults.append({'start': start, 'end': end, 'pid': pid, 'tid': tid,
                               'device': device, 'inode': inode, 'offset': offset, 'flags': flags})
            elif fields and fields[0] == 'C':
                timestamp, pid, tid, device, inode, offset, order = map(int, fields[1:])
                fills.append({'time': timestamp, 'pid': pid, 'tid': tid, 'device': device,
                              'inode': inode, 'offset': offset, 'order': order})
        for request in run['rows']:
            samples = request['diagnostic']['snapshots']
            adjustment = request['wall_ns']-request['monotonic_ns']
            stages = {}
            for name, left, right in [('preparation', 0, 1), ('execution', 1, 2),
                                      ('intervening', 2, 3), ('root', 3, 4)]:
                before, after = samples[left], samples[right]
                begin = before['snapshot_end_unix_ns']-adjustment
                end = after['snapshot_start_unix_ns']-adjustment
                observed = [f for f in faults if begin <= f['start'] and f['end'] <= end]
                database = [f for f in observed if f['inode'] == run['database']['inode']
                            and f['device'] == run['database']['device']]
                major = [f for f in database if f['flags'] & 4]  # Linux VM_FAULT_MAJOR.
                inserted = [f for f in fills if begin <= f['time'] <= end]
                a, b = counters(before), counters(after)
                devices = []
                for sample in [before, after]:
                    devices.append({line.split()[2]: int(line.split()[5])*512
                                    for line in sample['disks'].splitlines()})
                thread_deltas = []
                old_threads = {t['tid']: t for t in before['threads']}
                for thread in after['threads']:
                    old = old_threads.get(thread['tid'])
                    if old:
                        old_io = dict(line.split(': ') for line in old['io'].splitlines())
                        new_io = dict(line.split(': ') for line in thread['io'].splitlines())
                        old_stat = old['stat'].rsplit(')', 1)[1].split()
                        new_stat = thread['stat'].rsplit(')', 1)[1].split()
                        thread_deltas.append({'tid': thread['tid'],
                            'read_bytes': int(new_io['read_bytes'])-int(old_io['read_bytes']),
                            'major_faults': int(new_stat[9])-int(old_stat[9])})
                stages[name] = {
                    'duration_ms': (end-begin)/1e6,
                    'read_bytes': b['read_bytes']-a['read_bytes'],
                    'major_faults': b['major_faults']-a['major_faults'],
                    'guest_device_read_bytes': devices[1]['vdb']-devices[0]['vdb'],
                    'snapshot_overhead_ms': (before['snapshot_end_unix_ns']-before['snapshot_start_unix_ns'])/1e6,
                    'database_fault_events': len(database),
                    'database_major_fault_events': len(major),
                    'database_fault_offsets': sorted({f['offset'] for f in database}),
                    'database_major_offsets': sorted({f['offset'] for f in major}),
                    'database_cache_insertions': len(inserted),
                    'database_inserted_offsets': sorted({f['offset'] for f in inserted}),
                    'other_file_major_faults': sum(bool(f['flags'] & 4) for f in observed if f not in database),
                    'database_fault_tids': sorted({f['tid'] for f in database}),
                    'caller_before': before['calling_thread'], 'caller_after': after['calling_thread'],
                    'thread_deltas': thread_deltas,
                }
            rows.append({**{k: run[k] for k in ['group', 'mode', 'overlap', 'condition', 'reversed']},
                         'phase': request['phase'], 'stages': stages,
                         'whole_request_read_bytes': request['after']['read_bytes']-request['before']['read_bytes'],
                         'whole_request_major_faults': request['after']['major_faults']-request['before']['major_faults'],
                         'unassigned_read_bytes': request['after']['read_bytes']-request['before']['read_bytes']
                            - sum(s['read_bytes'] for s in stages.values()),
                         'unassigned_major_faults': request['after']['major_faults']-request['before']['major_faults']
                            - sum(s['major_faults'] for s in stages.values()),
                         'metrics_delta': request['metrics_delta']})
    pairs = []
    by_key = {(r['group'], r['overlap'], r['condition'], r['reversed'], r['phase'], r['mode']): r for r in rows}
    for key, lazy in by_key.items():
        if key[-1] != 'lazy':
            continue
        disabled = by_key[(*key[:-1], 'disabled')]
        d = disabled['stages']
        l = lazy['stages']
        moved = set(d['execution']['database_major_offsets']) & set(l['root']['database_major_offsets'])
        moved -= set(d['root']['database_major_offsets'])
        d_all = set(d['execution']['database_major_offsets']) | set(d['root']['database_major_offsets'])
        l_all = set(l['execution']['database_major_offsets']) | set(l['root']['database_major_offsets'])
        pairs.append({**{k: lazy[k] for k in ['group','overlap','condition','reversed','phase']},
                      'disabled_execution_to_lazy_root_major_offsets': sorted(moved),
                      'major_offset_union_equal': d_all == l_all,
                      'disabled_only_major_offsets': sorted(d_all-l_all),
                      'lazy_only_major_offsets': sorted(l_all-d_all),
                      'stage_read_byte_deltas': {s: l[s]['read_bytes']-d[s]['read_bytes'] for s in d}})
    buckets = defaultdict(list)
    for row in rows:
        for stage, values in row['stages'].items():
            buckets[(row['condition'],row['overlap'],row['phase'],row['mode'],stage)].append(values)
    summary = []
    for key, values in sorted(buckets.items()):
        summary.append({'condition': key[0], 'overlap': key[1], 'phase': key[2],
                        'mode': key[3], 'stage': key[4], 'n': len(values),
                        'measurements': {metric: {'median': stats.median(v[metric] for v in values),
                                                  'min': min(v[metric] for v in values),
                                                  'max': max(v[metric] for v in values)}
                                         for metric in ['duration_ms','read_bytes','major_faults',
                                                        'guest_device_read_bytes','snapshot_overhead_ms',
                                                        'database_fault_events','database_major_fault_events',
                                                        'database_cache_insertions','other_file_major_faults']}})
    encoded = (json.dumps({'source': str(args.input), 'rows': rows,
                           'paired_offsets': pairs, 'summary': summary}, indent=2)+'\n').encode()
    args.output.write_bytes(gzip.compress(encoded, mtime=0) if args.output.suffix == '.gz' else encoded)


if __name__ == '__main__':
    main()
