"""Aggregate benchmark results from individual JSON files into a summary."""

from __future__ import annotations

import json
import os
from pathlib import Path

RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", "./results"))


def load_results() -> dict[str, list[dict]]:
    """Load all result JSON files grouped by test key."""
    groups: dict[str, list[dict]] = {}
    for f in sorted(RESULTS_DIR.glob("*.json")):
        if f.name == "summary.json":
            continue
        # e.g. http_inmemory_1.json → http_inmemory
        parts = f.stem.rsplit("_", 1)
        key = parts[0] if len(parts) > 1 else f.stem
        data = json.loads(f.read_bytes())
        groups.setdefault(key, []).append(data)
    return groups


def aggregate_group(results: list[dict], transport: str) -> dict:
    """Combine results from multiple clients into one summary."""
    if transport == "http":
        total_rps = sum(r.get("rps", 0) for r in results)
        total_reqs = sum(r.get("total_requests", 0) for r in results)
        total_errors = sum(r.get("errors", 0) for r in results)
        all_lats = []
        for r in results:
            lat = r.get("latency_ms", {})
            if lat:
                all_lats.append(lat)

        avg_lat = {}
        if all_lats:
            for key in ["min", "p50", "p95", "p99", "max", "mean"]:
                vals = [l[key] for l in all_lats if key in l]
                avg_lat[key] = round(sum(vals) / len(vals), 2) if vals else 0

        return {
            "clients": len(results),
            "total_requests": total_reqs,
            "aggregate_rps": round(total_rps, 1),
            "total_errors": total_errors,
            "avg_latency_ms": avg_lat,
            "concurrency_per_client": results[0].get("concurrency", 0) if results else 0,
            "duration_s": results[0].get("duration_s", 0) if results else 0,
        }
    else:
        total_mps = sum(r.get("messages_per_sec", 0) for r in results)
        total_msgs = sum(r.get("total_messages", 0) for r in results)
        total_errors = sum(r.get("errors", 0) for r in results)
        all_lats = []
        for r in results:
            lat = r.get("latency_ms", {})
            if lat:
                all_lats.append(lat)

        avg_lat = {}
        if all_lats:
            for key in ["min", "p50", "p95", "p99", "max", "mean"]:
                vals = [l[key] for l in all_lats if key in l]
                avg_lat[key] = round(sum(vals) / len(vals), 2) if vals else 0

        return {
            "clients": len(results),
            "total_messages": total_msgs,
            "aggregate_msg_per_sec": round(total_mps, 1),
            "total_errors": total_errors,
            "avg_latency_ms": avg_lat,
            "concurrency_per_client": results[0].get("concurrency", 0) if results else 0,
            "duration_s": results[0].get("duration_s", 0) if results else 0,
        }


def main() -> None:
    groups = load_results()
    summary: dict[str, dict] = {}

    for key, results in groups.items():
        transport = "http" if key.startswith("http") else "ws"
        summary[key] = aggregate_group(results, transport)
        label = key.replace("_", " ").upper()
        s = summary[key]
        if transport == "http":
            print(f"  {label}: {s['aggregate_rps']} RPS "
                  f"(p50={s['avg_latency_ms'].get('p50', '?')}ms, "
                  f"p99={s['avg_latency_ms'].get('p99', '?')}ms) "
                  f"[{s['total_requests']} reqs, {s['total_errors']} errors]")
        else:
            print(f"  {label}: {s['aggregate_msg_per_sec']} msg/s "
                  f"(p50={s['avg_latency_ms'].get('p50', '?')}ms, "
                  f"p99={s['avg_latency_ms'].get('p99', '?')}ms) "
                  f"[{s['total_messages']} msgs, {s['total_errors']} errors]")

    out = RESULTS_DIR / "summary.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n  Summary: {out}")


if __name__ == "__main__":
    main()
