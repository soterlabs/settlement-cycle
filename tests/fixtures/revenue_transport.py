"""Deterministic transport for configured-prime cache acceptance, not live parity."""

import json
from datetime import UTC, datetime

import requests

EPOCH = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp())


def async_reply(address, selector):
    """Valid ownership metadata with zero claims for the transport scenario."""
    from settle.domain.config import load_prime_by_id
    from settle.extract._keccak import keccak256
    from functools import lru_cache

    @lru_cache(maxsize=1)
    def replies():
        p = load_prime_by_id("spark")
        out = {}

        def add(contract, signature, *values):
            key = contract, "0x" + keccak256(signature.encode())[:4].hex()
            out[key] = "0x" + "".join(format(v, "064x") for v in values)

        managers = {
            "S14": "0x7ad5ffa5fdf509e30186f4609c2f6269f4b6158f",
            "S15": "0x0cda32e08b48bfddbc7ee96b44b09cf286f9e21a",
        }
        for vid in ["S14", "S15"]:
            v = next(v for v in p.venues if v.id == vid)
            m = v.async_allocation.managers[0]
            add(v.token.address.hex, "asset()", int(v.underlying.address.hex, 16))
            add(v.token.address.hex, "manager()", int(managers[vid], 16))
            add(managers[vid], "withdrawalManager()", int(m.address.hex, 16))
            add(m.address.hex, "poolManager()", int(managers[vid], 16))
            add(m.address.hex, "pool()", int(v.token.address.hex, 16))
            add(m.address.hex, "implementation()", int(m.implementations[0].hex, 16))
            add(m.address.hex, "userEscrowedShares(address)", 0)
            add(m.address.hex, "manualSharesAvailable(address)", 0)
        v = next(v for v in p.venues if v.id == "S16")
        add(v.token.address.hex, "asset()", int(v.underlying.address.hex, 16))
        add(v.token.address.hex, "silo()", int("7fc7c91d556b400afa565013e3f32055a0713425", 16))
        add(v.token.address.hex, "cooldowns(address)", 0, 0)
        return out

    if "values" not in _ASYNC_REPLIES:
        _ASYNC_REPLIES["values"] = replies()
    return _ASYNC_REPLIES["values"].get((address, selector))


_ASYNC_REPLIES = {}


def send(session, request, **kwargs):
    if request.url.startswith("https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?"):
        from urllib.parse import urlparse, parse_qs
        from datetime import date, timedelta

        query = parse_qs(urlparse(request.url).query)
        current, end = (
            date.fromisoformat(query["startDate"][0]),
            date.fromisoformat(query["endDate"][0]),
        )
        rows = []
        while current <= end:
            if current.weekday() < 5:
                rows.append({"type": "SOFR", "effectiveDate": str(current), "percentRate": "3.62"})
            current += timedelta(days=1)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"refRates": rows}).encode()
        return response
    if request.url.startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?"):
        from datetime import date, timedelta
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(request.url).query)
        current, end = date.fromisoformat(query["cosd"][0]), date.fromisoformat(query["coed"][0])
        rows = ["observation_date,DFEDTARL"]
        while current <= end:
            rows.append(f"{current},3.50")
            current += timedelta(days=1)
        response = requests.Response()
        response.status_code = 200
        response._content = ("\n".join(rows) + "\n").encode()
        return response
    body = json.loads(request.body)
    if "hypersync.xyz" in request.url:
        lo, hi = body["from_block"], body["to_block"]
        data = {
            "archive_height": 50_000_000,
            "next_block": hi,
            "data": [{"logs": [], "blocks": [{"number": lo, "timestamp": EPOCH + lo * 60}]}],
        }
        from settle.normalize.sources.hypersync_ssr import FILE_TOPIC, SSR_KEY, SUSDS

        ssr_block = int((datetime(2024, 9, 1, tzinfo=UTC).timestamp() - EPOCH) // 60)
        if any(FILE_TOPIC in str(sel) for sel in body.get("logs", [])) and lo <= ssr_block < hi:
            data["data"][0]["blocks"].append(
                {"number": ssr_block, "timestamp": EPOCH + ssr_block * 60}
            )
            data["data"][0]["logs"].append(
                {
                    "block_number": ssr_block,
                    "log_index": 0,
                    "address": SUSDS,
                    "topic0": FILE_TOPIC,
                    "topic1": SSR_KEY,
                    "data": "0x" + format(10**27, "064x"),
                }
            )
    else:
        method, args = body["method"], body["params"]
        if method == "eth_call":
            selector = args[0]["data"][:10]
            zero = {
                "0x70a08231",
                "0x18160ddd",
                "0x01e1d114",
                "0xce7c2ac2",
                "0x1da24f3e",
                "0x26c6f96c",
                "0x995ea21a",
                "0xf5a23d8d",
                "0xeaed1d07",
            }
            value = "0x" + format(0 if selector in zero else 10**18, "064x")
            # Zero-position async vaults still expose the full manager ABI.
            if selector == "0xb7570e0c":  # asyncManager()
                value = "0x" + ("12" * 20).rjust(64, "0")
            if selector == "0x97780020":  # investments(vault, controller)
                value = "0x" + "0" * (64 * 10)
            value = async_reply(args[0]["to"].lower(), selector) or value
            from settle.extract.uniswap_v4 import (
                SEL_GET_POOL_AND_POSITION_INFO,
                SEL_GET_POSITION_LIQUIDITY,
            )

            if selector in {"0x1e2eaeaf", "0x6352211e", SEL_GET_POSITION_LIQUIDITY}:
                value = "0x" + "0" * 64
            if selector == SEL_GET_POOL_AND_POSITION_INFO:
                value = "0x" + "0" * (64 * 6)
            if selector == "0x3850c7bd":
                value = "0x" + "".join(format(v, "064x") for v in [2**96, 0, 0, 0, 0, 0, 1])
            if selector in {"0x0dfe1681", "0xd21220a7"}:
                value = "0x" + (
                    "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
                    if selector == "0x0dfe1681"
                    else "dc035d45d973e3ec169d2276ddab16f1e407384f"
                ).rjust(64, "0")
            # New incentive input: an unlisted SparkLend reserve in this
            # deliberately zero-position fixture, with the full ABI shape.
            if selector == "0x35ea6a75":  # getReserveData(address)
                value = "0x" + "0" * (64 * 15)
            if selector == "0xd9638d36":
                value = "0x" + "".join(format(v, "064x") for v in [0, 10**27, 0, 0, 0])
        elif method == "eth_getCode":
            value = "0x6000"
        elif method == "eth_getBalance":
            value = "0x0"
        elif method == "eth_getBlockByNumber":
            n = int(args[0], 16)
            value = {"number": hex(n), "timestamp": hex(EPOCH + n * 60)}
        else:
            raise AssertionError(f"Unexpected method {method}")
        data = {"jsonrpc": "2.0", "id": body.get("id"), "result": value}
        if method == "eth_call" and args[0]["data"][:10] in {"0xc66106f8", "0xc6610657"}:
            idx = int(args[0]["data"][10:], 16)
            if idx < 2:
                addresses = [
                    "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    "dc035d45d973e3ec169d2276ddab16f1e407384f",
                ]
                data["result"] = "0x" + addresses[idx].rjust(64, "0")
            if idx >= 2:
                data = {
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "error": {"code": 3, "message": "execution reverted"},
                }
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(data).encode()
    return response
