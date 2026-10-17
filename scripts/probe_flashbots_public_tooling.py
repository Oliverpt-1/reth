#!/usr/bin/env python3
"""Check public rbuilder protocol/data readiness against a real local RETH node.

This does not run rbuilder backtesting or the public test-relay process and does
not measure performance. It checks upstream source compatibility, archive access,
and whether one generated Amsterdam payload validates through V6 versus V5.
Requires the same Python dependencies as bench_flashbots_parent_bal_paired.py.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.request

import bench_flashbots_parent_bal_paired as paired


def archive_access(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as response:
            return {"url": url, "http_status": response.status}
    except urllib.error.HTTPError as error:
        return {"url": url, "http_status": error.code}
    except (OSError, urllib.error.URLError) as error:
        return {"url": url, "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rbuilder", type=Path, required=True, help="Read-only upstream checkout")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=20545)
    args = parser.parse_args()
    args.output_dir.mkdir()
    source = args.rbuilder / "crates/test-relay/src/validation_api_client.rs"
    methods = sorted(set(re.findall(r"flashbots_validateBuilderSubmissionV\d+", source.read_text())))
    cargo = tomllib.loads((args.rbuilder / "Cargo.toml").read_text())
    revision = subprocess.check_output(["git", "-C", str(args.rbuilder), "rev-parse", "HEAD"], text=True).strip()
    result = {
        "scope": "Source/readiness inspection and real-node protocol probe; no historical or relay performance benchmark",
        "upstream_commit": revision,
        "upstream_reth_dependency": cargo["workspace"]["dependencies"]["reth"],
        "test_relay_methods": methods,
        "upstream_test_relay_has_v6_route": "flashbots_validateBuilderSubmissionV6" in methods,
        "upstream_source": f"https://github.com/flashbots/rbuilder/blob/{revision}/crates/test-relay/src/validation_api_client.rs",
        "archive_access": [archive_access(url) for url in [
            "https://mempool-dumpster.flashbots.net/index.html",
            "https://mempool-dumpster.flashbots.net/ethereum/mainnet/2023-09/2023-09-08.parquet",
        ]],
        "workspace_free_bytes": shutil.disk_usage(args.output_dir).free,
        "upstream_backtest_executed": False,
        "upstream_test_relay_process_executed": False,
        "new_latency_samples": 0,
    }
    node = paired.start_node(args.binary.resolve(), args.output_dir / "node", args.port,
                             True, True, 64, "bal", "lazy", "normal")
    try:
        paired.base.mine(node["port"], 0, 64)
        child = paired.base.mine(node["port"], 0, 64)
        request = paired.base.submission(node["port"], child)
        result["binary_version"] = subprocess.check_output([str(args.binary.resolve()), "--version"], text=True)
        result["parent_hash"] = child["parentHash"]
        result["child_hash"] = child["hash"]
        result["child_state_root"] = child["stateRoot"]
        responses = {}
        reads = {}
        for version in [6, 5]:
            before = paired.metrics(node["port"] + 1)
            try:
                responses[str(version)] = {"result": paired.base.rpc(
                    node["port"], f"flashbots_validateBuilderSubmissionV{version}", [request])}
            except RuntimeError as error:
                responses[str(version)] = {"error": str(error)}
            delta = paired.delta(paired.metrics(node["port"] + 1), before)
            reads[str(version)] = {kind: paired.count(delta, kind)
                                   for kind in ["bal_slots", "provider_slots", "provider_accounts"]}
        result["responses"] = responses
        result["read_counts"] = reads
        assert responses["6"] == {"result": None}, responses
        assert "block hash mismatch" in responses["5"].get("error", ""), responses
        assert reads["6"]["bal_slots"] == 64 and reads["6"]["provider_slots"] == 0, reads
        assert reads["5"]["bal_slots"] == 0, reads
    finally:
        paired.stop_node(node)
    path = args.output_dir / "result.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
