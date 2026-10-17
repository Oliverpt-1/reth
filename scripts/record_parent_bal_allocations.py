#!/usr/bin/env python3
"""Run the isolated allocation test and retain complete machine-readable evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    command = ['cargo', 'test', '--locked', '-p', 'reth-rpc', '--test',
               'parent_bal_allocations', 'allocation_lifetimes', '--', '--ignored',
               '--exact', '--nocapture', '--test-threads=1']
    result = subprocess.run(command, text=True, capture_output=True)
    rows = [json.loads(line.split('ALLOCATION_RESULT ', 1)[1])
            for line in result.stdout.splitlines() if 'ALLOCATION_RESULT ' in line]
    record = {
        'command': command, 'exit_code': result.returncode,
        'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'harness_sha256': hashlib.sha256(Path('crates/rpc/rpc/tests/parent_bal_allocations.rs').read_bytes()).hexdigest(),
        'platform': platform.platform(),
        'compiler': subprocess.check_output(['rustc', '-Vv'], text=True),
        'allocation_unit': 'requested heap bytes; excludes allocator prefix, alignment padding and allocator metadata',
        'scope': 'production adapter, parent view, committed loader and real one-entry EthStateCache; allocation profiling only, not latency',
        'rows': rows, 'stdout': result.stdout, 'stderr': result.stderr,
    }
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'exit_code': result.returncode, 'cases': len(rows), 'output': str(args.output)}))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
