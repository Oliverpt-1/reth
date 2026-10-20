#!/usr/bin/env python3
"""Summarize node measurements, pairing treatments and resampling parents."""

import argparse
from collections import defaultdict
import gzip
import json
import math
from pathlib import Path
import random
import statistics as stats


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    opener = gzip.open if args.input.suffix == '.gz' else open
    with opener(args.input, 'rt') as stream:
        raw = json.load(stream)
    buckets = defaultdict(list)
    for row in raw['rows']:
        buckets[(row['condition'], row['overlap'], row['phase'], row['mode'])].append(row)
    summaries = []
    rng = random.Random(7928)
    for (condition, overlap, phase, mode), rows in sorted(buckets.items()):
        latencies = [r['latency_ms'] for r in rows]
        reads = [r['io_after']['read_bytes']-r['io_before']['read_bytes'] for r in rows]
        faults = [r['io_after']['major_faults']-r['io_before']['major_faults'] for r in rows]
        device_reads = [r['io_after']['device_read_bytes'].get('vdb', 0)
                        - r['io_before']['device_read_bytes'].get('vdb', 0) for r in rows]
        baseline = {(r['group'], r['reversed']): r for r in
                    buckets[(condition, overlap, phase, 'disabled')]}
        by_parent = defaultdict(list)
        for row in rows:
            off = baseline[(row['group'], row['reversed'])]
            by_parent[row['group']].append(row['latency_ms']-off['latency_ms'])
        differences = [stats.mean(v) for v in by_parent.values()]
        samples = [stats.mean(rng.choices(differences, k=len(differences))) for _ in range(10000)]
        summaries.append({
            'condition': condition, 'overlap': overlap, 'phase': phase, 'mode': mode,
            'requests': len(rows), 'parent_groups': len(by_parent),
            'latency_p50_ms': stats.median(latencies), 'latency_p95_ms': percentile(latencies, .95),
            'paired_mean_delta_ms': stats.mean(differences),
            'parent_bootstrap_95ci_ms': [percentile(samples, .025), percentile(samples, .975)],
            'reversal_mean_deltas_ms': {
                str(reversal): stats.mean(r['latency_ms']-baseline[(r['group'], reversal)]['latency_ms']
                                         for r in rows if r['reversed'] == reversal)
                for reversal in [False, True]},
            'process_read_bytes_median': stats.median(reads),
            'major_faults_median': stats.median(faults),
            'guest_device_read_bytes_median': stats.median(device_reads),
            'requests_with_process_reads': sum(v > 0 for v in reads),
            'requests_with_major_faults': sum(v > 0 for v in faults),
            'requests_with_guest_device_reads': sum(v > 0 for v in device_reads),
            'provider_slots_median': stats.median(r['provider_slots'] for r in rows),
            'bal_slots_median': stats.median(r['bal_slots'] for r in rows),
            'provider_accounts_median': stats.median(r['provider_accounts'] for r in rows),
            'provider_code_median': stats.median(r['provider_code'] for r in rows),
            'bal_acquisition_median_us': stats.median(sum(v for k, v in r['metrics_delta'].items()
                if k.endswith('parent_bal_load_seconds_sum')) * 1e6 for r in rows),
            'preload_fetch_median_us': stats.median(sum(v for k, v in r['preload_metrics_delta'].items()
                if k.endswith('bal_fetch_seconds_sum')) * 1e6 for r in rows),
            'preload_decode_median_us': stats.median(sum(v for k, v in r['preload_metrics_delta'].items()
                if k.endswith('bal_decode_seconds_sum')) * 1e6 for r in rows),
            'shared_bal_cache_reported_bytes_median': stats.median(sum(v for k, v in r['shared_bal_cache_before'].items()
                if 'memory_usage' in k) for r in rows),
            'rss_median_bytes': stats.median(r['io_after']['rss_bytes'] for r in rows),
            'stages_median_us': {key: stats.median(r['stages_us'].get(key, 0) for r in rows)
                                 for key in rows[0]['stages_us']},
        })
    args.output.write_text(json.dumps({'source': str(args.input), 'rows': summaries,
        'uncertainty': '10,000 parent-cluster bootstrap draws, seed 7928; average the two orders within each parent before resampling; descriptive for one generated chain'}, indent=2)+'\n')


if __name__ == '__main__':
    main()
