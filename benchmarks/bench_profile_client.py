#!/usr/bin/env python3
"""Profile the Haystack client locally with PyInstrument.

Runs HTTP and/or WebSocket load generation against a remote (or Docker)
Haystack server with pyinstrument profiling the local async event loop.

Useful for identifying bottlenecks in SCRAM auth, request serialization,
response parsing, and WebSocket framing on the client side.

Usage::

    # Profile HTTP client against a Docker server
    uv run python benchmarks/bench_profile_client.py \\
        --url http://localhost:8080/api --transport http

    # Profile both transports, 10s each, 10 concurrent workers
    uv run python benchmarks/bench_profile_client.py \\
        --url http://localhost:8080/api --duration 10 --concurrency 10

Results are written to ``benchmarks/results/profiles/``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyinstrument import Profiler

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("profile_client")

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "profiles"


# ---------------------------------------------------------------------------
# SCRAM auth (httpx-based)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _parse_header_params(header: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not header:
        return result
    parts = header.split(None, 1)
    param_str = header
    if len(parts) >= 2 and "=" not in parts[0]:
        param_str = parts[1]
    for pair in param_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = v.strip()
    return result


async def scram_auth(client: Any, base_url: str, username: str, password: str) -> str:
    """SCRAM-SHA-256 handshake via httpx, returns auth token."""
    about = f"{base_url}/about"

    # Step 1: HELLO
    hello = f"HELLO username={_b64url_encode(username.encode())}"
    r = await client.get(about, headers={"Authorization": hello})
    assert r.status_code == 401, f"HELLO expected 401, got {r.status_code}"
    p = _parse_header_params(r.headers.get("www-authenticate", ""))
    ht = p.get("handshakeToken", "")

    # Step 2: client-first
    c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
    safe = username.replace("=", "=3D").replace(",", "=2C")
    bare = f"n={safe},r={c_nonce}"
    hdr = f"SCRAM handshakeToken={ht}, data={_b64url_encode(f'n,,{bare}'.encode())}"
    r = await client.get(about, headers={"Authorization": hdr})
    assert r.status_code == 401
    p2 = _parse_header_params(r.headers.get("www-authenticate", ""))
    ht = p2.get("handshakeToken", ht)
    sf = _b64url_decode(p2.get("data", "")).decode()

    fields: dict[str, str] = {}
    for item in sf.split(","):
        if "=" in item:
            k, _, v = item.partition("=")
            fields[k] = v

    salt = base64.b64decode(fields["s"])
    iters = int(fields["i"])
    s_nonce = fields["r"]

    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters, dklen=32)
    ck = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    sk = hashlib.sha256(ck).digest()
    cfnp = f"c={_b64url_encode(b'n,,')},r={s_nonce}"
    auth_msg = f"{bare},{sf},{cfnp}"
    sig = hmac.new(sk, auth_msg.encode(), hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(ck, sig))
    cf = f"{cfnp},p={base64.b64encode(proof).decode()}"

    # Step 3: client-final
    hdr = f"SCRAM handshakeToken={ht}, data={_b64url_encode(cf.encode())}"
    r = await client.get(about, headers={"Authorization": hdr})
    assert r.status_code == 200
    tp = _parse_header_params(r.headers.get("authentication-info", ""))
    token = tp.get("authToken", "")
    if not token:
        raise RuntimeError("No authToken in SCRAM response")
    return token


# ---------------------------------------------------------------------------
# HTTP load driver
# ---------------------------------------------------------------------------

import orjson

_POST_OPS = [
    ("/read", {"meta": {"ver": "3.0"}, "cols": [{"name": "filter"}], "rows": [{"filter": "site"}]}),
    ("/read", {"meta": {"ver": "3.0"}, "cols": [{"name": "filter"}], "rows": [{"filter": "point"}]}),
    ("/nav", {"meta": {"ver": "3.0"}, "cols": [{"name": "navId"}], "rows": [{}]}),
]
_GET_OPS = ["/about", "/ops"]
_ALL_OPS: list[tuple[str, bytes | None]] = [
    (ep, None) for ep in _GET_OPS
] + [
    (ep, orjson.dumps(body)) for ep, body in _POST_OPS
]


async def _http_worker(
    client: Any,
    base_url: str,
    headers: dict[str, str],
    results: list[tuple[str, float, int]],
    stop: asyncio.Event,
) -> None:
    post_headers = {**headers, "Content-Type": "application/json"}
    idx = 0
    while not stop.is_set():
        ep, body = _ALL_OPS[idx % len(_ALL_OPS)]
        url = f"{base_url}{ep}"
        t0 = time.monotonic()
        try:
            if body is None:
                r = await client.get(url, headers=headers)
            else:
                r = await client.post(url, headers=post_headers, content=body)
            results.append((ep, time.monotonic() - t0, r.status_code))
        except Exception:
            results.append((ep, time.monotonic() - t0, 0))
        idx += 1


# ---------------------------------------------------------------------------
# WebSocket load driver
# ---------------------------------------------------------------------------


async def _ws_worker(
    ws_url: str,
    token: str,
    results: list[tuple[str, float, bool]],
    stop: asyncio.Event,
) -> None:
    import websockets

    ops = [
        {"op": "about"},
        {"op": "ops"},
        {"op": "read", "grid": {
            "_kind": "grid",
            "meta": {"ver": "3.0"},
            "cols": [{"name": "filter"}],
            "rows": [{"filter": "site"}],
        }},
        {"op": "read", "grid": {
            "_kind": "grid",
            "meta": {"ver": "3.0"},
            "cols": [{"name": "filter"}],
            "rows": [{"filter": "point"}],
        }},
        {"op": "nav", "grid": {
            "_kind": "grid",
            "meta": {"ver": "3.0"},
            "cols": [{"name": "navId"}],
            "rows": [{}],
        }},
    ]
    try:
        async with websockets.connect(
            ws_url, subprotocols=["haystack"], max_size=2**24,
        ) as ws:
            await ws.send(json.dumps({"authToken": token}))
            idx, mid = 0, 0
            while not stop.is_set():
                op = ops[idx % len(ops)]
                mid += 1
                t0 = time.monotonic()
                try:
                    await ws.send(json.dumps({**op, "id": mid}))
                    await asyncio.wait_for(ws.recv(), timeout=5.0)
                    results.append((op["op"], time.monotonic() - t0, True))
                except Exception:
                    results.append((op["op"], time.monotonic() - t0, False))
                idx += 1
    except Exception as exc:
        _log.warning("WS worker error: %s", exc)


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _pct(data: list[float], p: int) -> float:
    i = int(len(data) * p / 100)
    return data[min(i, len(data) - 1)]


def _http_stats(results: list[tuple[str, float, int]], wall: float) -> dict[str, Any]:
    total = len(results)
    errs = sum(1 for _, _, s in results if s != 200)
    lats = sorted(t for _, t, s in results if s == 200)
    if not lats:
        return {"transport": "http", "total": total, "errors": errs, "rps": 0}
    return {
        "transport": "http",
        "total_requests": total,
        "errors": errs,
        "wall_time_s": round(wall, 2),
        "rps": round(total / wall, 1),
        "latency_ms": {
            "min": round(lats[0] * 1000, 2),
            "p50": round(_pct(lats, 50) * 1000, 2),
            "p95": round(_pct(lats, 95) * 1000, 2),
            "p99": round(_pct(lats, 99) * 1000, 2),
            "max": round(lats[-1] * 1000, 2),
            "mean": round(statistics.mean(lats) * 1000, 2),
        },
    }


def _ws_stats(results: list[tuple[str, float, bool]], wall: float) -> dict[str, Any]:
    total = len(results)
    errs = sum(1 for _, _, ok in results if not ok)
    lats = sorted(t for _, t, ok in results if ok)
    if not lats:
        return {"transport": "websocket", "total": total, "errors": errs, "mps": 0}
    return {
        "transport": "websocket",
        "total_messages": total,
        "errors": errs,
        "wall_time_s": round(wall, 2),
        "messages_per_sec": round(total / wall, 1),
        "latency_ms": {
            "min": round(lats[0] * 1000, 2),
            "p50": round(_pct(lats, 50) * 1000, 2),
            "p95": round(_pct(lats, 95) * 1000, 2),
            "p99": round(_pct(lats, 99) * 1000, 2),
            "max": round(lats[-1] * 1000, 2),
            "mean": round(statistics.mean(lats) * 1000, 2),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _run(
    url: str,
    username: str,
    password: str,
    duration: int,
    concurrency: int,
    transports: list[str],
) -> None:
    import httpx

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_results: dict[str, Any] = {"server_url": url, "duration_s": duration}

    async with httpx.AsyncClient() as client:
        _log.info("Authenticating to %s as %s…", url, username)
        token = await scram_auth(client, url, username, password)
        _log.info("Authenticated.")
        auth = {"Authorization": f"BEARER authToken={token}"}

        for transport in transports:
            _log.info("--- Profiling %s client (%ds, %d workers) ---",
                      transport.upper(), duration, concurrency)

            profiler = Profiler(async_mode="enabled")
            profiler.start()
            stop = asyncio.Event()
            t0 = time.monotonic()

            if transport == "http":
                res: list[Any] = []
                tasks = [
                    asyncio.create_task(_http_worker(client, url, auth, res, stop))
                    for _ in range(concurrency)
                ]
                await asyncio.sleep(duration)
                stop.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                wall = time.monotonic() - t0
                stats = _http_stats(res, wall)
            else:
                ws_url = url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
                res = []
                tasks = [
                    asyncio.create_task(_ws_worker(ws_url, token, res, stop))
                    for _ in range(concurrency)
                ]
                await asyncio.sleep(duration)
                stop.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                wall = time.monotonic() - t0
                stats = _ws_stats(res, wall)

            profiler.stop()

            # Save
            html = RESULTS_DIR / f"client_{transport}.html"
            txt = RESULTS_DIR / f"client_{transport}.txt"
            html.write_text(profiler.output_html())
            txt.write_text(profiler.output_text(unicode=True, color=False))

            print(f"\n{'=' * 60}")
            print(f"  CLIENT {transport.upper()} — {duration}s, {concurrency} workers")
            print(f"{'=' * 60}")
            print(json.dumps(stats, indent=2))
            print(f"\nProfile: {html}")
            print(profiler.output_text(unicode=True, color=True))

            all_results[transport] = stats

    out = RESULTS_DIR / "client_profile.json"
    out.write_text(json.dumps(all_results, indent=2))
    _log.info("Results → %s", out)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile the Haystack client locally with PyInstrument."
    )
    parser.add_argument("--url", default="http://localhost:8080/api", help="Server API URL")
    parser.add_argument("--user", default="admin", help="Username (default: admin)")
    parser.add_argument("--password", default="secret", help="Password (default: secret)")
    parser.add_argument("--duration", type=int, default=10, help="Seconds per transport (default: 10)")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent workers (default: 10)")
    parser.add_argument(
        "--transport", choices=["http", "ws", "both"],
        default="both", help="Transport to profile (default: both)",
    )
    args = parser.parse_args()
    transports = ["http", "ws"] if args.transport == "both" else [args.transport]

    print(f"PyInstrument Client Profiler")
    print(f"  Server:      {args.url}")
    print(f"  Duration:    {args.duration}s per transport")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Transports:  {', '.join(transports)}")
    print(f"  Profiles:    {RESULTS_DIR}/")
    print()

    asyncio.run(_run(args.url, args.user, args.password, args.duration, args.concurrency, transports))


if __name__ == "__main__":
    main()
