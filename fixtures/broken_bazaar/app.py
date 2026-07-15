"""BrokenBazaar — the demo fixture PreFlight tests against.

A tiny paid data vendor ("market_pulse") behind a real x402 v1 paywall, with
two intentionally toggleable bugs:

  BUG_PRICE=1  the 402 quotes 0.25 while the listing says 0.05 (overcharge)
  BUG_EMPTY=1  payment settles but the paid tool delivers empty content

Payment verification is pluggable: MockFacilitator (offline; default) or the
OKX facilitator when deployed for real testnet settlement.
"""
from __future__ import annotations

import json
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from preflight.x402kit import MockFacilitator, build_challenge, parse_challenge  # noqa: E402

LISTED_PRICE = 0.05
PAID_TOOL = "market_pulse"
DEFAULT_PAY_TO = "0x2f7cF9d979A98d0C4Cd2c92c8DC0d9DFf4a04d2A"


def _pulse_text() -> str:
    return json.dumps({
        "pulse": "steady",
        "signals": [
            {"pair": "OKB/USDT", "drift_bps": 12, "note": "range-bound"},
            {"pair": "ETH/USDT", "drift_bps": -8, "note": "cooling"},
        ],
        "disclaimer": "demo data — BrokenBazaar is a test fixture",
    })


def build_mcp(bug_empty: bool) -> FastMCP:
    mcp = FastMCP("BrokenBazaar", instructions="Demo paid data vendor for PreFlight.")

    @mcp.tool
    def ping() -> str:
        """Free liveness check."""
        return "pong"

    @mcp.tool
    def market_pulse() -> str:
        """Paid: a tiny synthetic market pulse report (x402-gated)."""
        return "" if bug_empty else _pulse_text()

    return mcp


class PaywallASGI:
    """Pure-ASGI wrapper: gates tools/call for the paid tool behind x402.

    Buffers the request body (so downstream still receives it), returns a
    schema-exact 402 when unpaid, verifies X-PAYMENT via the facilitator when
    paid, and passes through on success.
    """

    def __init__(self, app, *, pay_to: str, network: str, quote_price: float,
                 facilitator, resource: str) -> None:
        self.app = app
        self.pay_to, self.network = pay_to, network
        self.quote_price, self.facilitator = quote_price, facilitator
        self.resource = resource

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope.get("path", "").rstrip("/") != "/mcp"):
            return await self.app(scope, receive, send)

        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}

        gated = False
        try:
            data = json.loads(body or b"{}")
            gated = (data.get("method") == "tools/call"
                     and data.get("params", {}).get("name") == PAID_TOOL)
        except json.JSONDecodeError:
            gated = False

        if gated:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            challenge = build_challenge(
                pay_to=self.pay_to, amount_usdt=self.quote_price,
                network=self.network, resource=self.resource,
                description=f"BrokenBazaar {PAID_TOOL}",
            )
            payment = headers.get("x-payment")
            if not payment:
                return await self._json(send, 402, challenge)
            req = parse_challenge(challenge).accepts[0]
            ok, who_or_reason, tx_ref = self.facilitator.verify(payment, req)
            if not ok:
                challenge["error"] = f"payment rejected: {who_or_reason}"
                return await self._json(send, 402, challenge)
            # settled — pass through, exposing a settle reference header
            async def send_with_receipt(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append(
                        (b"x-payment-response", tx_ref.encode()))
                await send(message)
            return await self.app(scope, replay, send_with_receipt)

        return await self.app(scope, replay, send)

    @staticmethod
    async def _json(send, status: int, payload: dict):
        body = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_app(*, bug_price: bool = False, bug_empty: bool = False,
               network: str = "mock", pay_to: str = DEFAULT_PAY_TO,
               facilitator=None, resource: str = "http://bazaar.local/mcp/"):
    mcp = build_mcp(bug_empty)
    inner = mcp.http_app(path="/mcp/", stateless_http=True, json_response=True)
    quote = 0.25 if bug_price else LISTED_PRICE
    wrapped = PaywallASGI(inner, pay_to=pay_to, network=network, quote_price=quote,
                          facilitator=facilitator or MockFacilitator(), resource=resource)
    wrapped.lifespan = inner.lifespan  # let servers reuse the MCP lifespan
    wrapped.inner_app = inner
    return wrapped


def env_app():
    flag = lambda n: os.getenv(n, "0").lower() in {"1", "true", "yes"}
    fac = OkxFacilitator() if os.getenv("FACILITATOR", "mock") == "okx" else None
    return create_app(
        bug_price=flag("BUG_PRICE"), bug_empty=flag("BUG_EMPTY"),
        network=os.getenv("NETWORK", "mock"),
        pay_to=os.getenv("PAY_TO", DEFAULT_PAY_TO),
        facilitator=fac,
        resource=os.getenv("RESOURCE_URL", "http://bazaar.local/mcp/"),
    )


app = env_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8801")))


class OkxFacilitator:
    """Real verify+settle via OKX's hosted facilitator (async SDK, sync bridge).

    Used when the fixture is deployed with FACILITATOR=okx and a real network
    (base-sepolia for the cash-free demo). Same .verify() contract as the mock.
    """

    def __init__(self, base_url: str | None = None) -> None:
        from x402.http.okx_facilitator_client import (OKXFacilitatorClient,
                                                      OKXFacilitatorConfig)
        cfg = OKXFacilitatorConfig(base_url=base_url) if base_url else OKXFacilitatorConfig()
        self._client = OKXFacilitatorClient(cfg)

    def verify(self, header_value: str, req) -> tuple[bool, str, str]:
        import asyncio
        import base64 as b64
        from x402.schemas.v1 import PaymentPayloadV1
        try:
            payload = PaymentPayloadV1.model_validate_json(b64.b64decode(header_value))
        except Exception as e:
            return False, f"X-PAYMENT header malformed: {e}", ""

        async def _go():
            v = await self._client.verify(payload, req)
            if not getattr(v, "is_valid", False):
                return False, str(getattr(v, "invalid_reason", "verify failed")), ""
            s = await self._client.settle(payload, req)
            if not getattr(s, "success", False):
                return False, str(getattr(s, "error_reason", "settle failed")), ""
            tx = getattr(s, "transaction", "") or getattr(s, "tx_hash", "") or ""
            return True, payload.payload["authorization"]["from"], str(tx)

        try:
            return asyncio.get_event_loop().run_until_complete(_go())
        except RuntimeError:
            return asyncio.run(_go())
        except Exception as e:  # facilitator unreachable etc.
            return False, f"facilitator error: {e}", ""
