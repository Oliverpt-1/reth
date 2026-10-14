#!/usr/bin/env python3
"""Replay captured valid Flashbots JSON-RPC requests against one frozen node.

This is an offline sequential replay tool, not a workload generator. It needs a
compatible database and full original validation request envelopes in NDJSON.
Metrics/RSS collection and JSON serialization happen outside request timing.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen


def fetch(url, data=None):
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def metrics(url):
    values = {}
    for line in fetch(url).decode().splitlines():
        match = re.match(r"^((?:reth_)?(?:builder_validation_|rpc_eth_cache_)\S+)\s+(\S+)", line)
        if match and 'quantile=' not in match[1]:
            values[match[1]] = float(match[2])
    if not any("builder_validation_" in key for key in values):
        raise RuntimeError("Validation metrics are missing; check the node and metrics URL")
    return values


def rss(pid):
    if pid is None:
        return None
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("Node PID has no resident-memory measurement")


def replay(args):
    requests = []
    for line_number, line in enumerate(args.corpus.read_text().splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("method") not in {
            f"flashbots_validateBuilderSubmissionV{version}" for version in range(3, 7)
        }:
            raise ValueError(f"Line {line_number}: expected an original validation RPC envelope")
        payload = record["params"][0]["execution_payload"]
        parent = payload["parent_hash"]
        # Preserve all RPC parameters, including version-specific auxiliary data.
        envelope = {"jsonrpc": "2.0", "id": len(requests) + 1,
                    "method": record["method"], "params": record["params"]}
        body = json.dumps(envelope, separators=(",", ":")).encode()
        requests.append((body, parent, payload["block_hash"], payload["state_root"]))
    if not requests:
        raise ValueError("Corpus is empty")

    head_request = json.dumps({"jsonrpc": "2.0", "id": 0,
                               "method": "eth_getBlockByNumber", "params": ["latest", False]}).encode()
    head = json.loads(fetch(args.rpc, head_request))
    if "error" in head or head.get("result") is None:
        raise RuntimeError("Cannot read the frozen node head")
    last_parent, within_group = None, 0
    # A new output file prevents silently overwriting a previous measurement.
    with args.output.open("x") as output:
        output.write(json.dumps({"metadata": {
            "label": args.label, "corpus_sha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
            "head_hash": head["result"]["hash"], "head_number": head["result"]["number"],
            "rpc": args.rpc, "request_count": len(requests),
            "scope": "sequential local RPC wall time; process/cache setup supplied externally",
        }}) + "\n")
        for index, (body, parent, block, state_root) in enumerate(requests):
            within_group = within_group + 1 if parent == last_parent else 0
            last_parent = parent
            before, rss_before = metrics(args.metrics), rss(args.pid)
            start = time.perf_counter_ns()
            raw_response = fetch(args.rpc, body)
            elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
            response = json.loads(raw_response)
            after, rss_after = metrics(args.metrics), rss(args.pid)
            row = {"index": index, "label": args.label, "parent_hash": parent,
                   "block_hash": block, "state_root": state_root,
                   "phase": "first_in_group" if within_group == 0 else "repeat_in_group",
                   "request_sha256": hashlib.sha256(body).hexdigest(), "latency_ms": elapsed_ms,
                   "response": response, "metrics_before": before, "metrics_after": after,
                   "rss_before_bytes": rss_before, "rss_after_bytes": rss_after}
            output.write(json.dumps(row) + "\n")
            output.flush()
            if "error" in response or "result" not in response or response["result"] is not None:
                raise RuntimeError(f"Request {index} failed validation; recorded run is incomplete")
    print(f"Recorded {len(requests)} successful validations in {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--metrics", default="http://127.0.0.1:9001/metrics")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--pid", type=int)
    replay(parser.parse_args())


if __name__ == "__main__":
    main()
