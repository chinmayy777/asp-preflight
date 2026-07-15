# ASP PreFlight

**Test-purchase your paid agent service like a real buyer — before you list it.**

PreFlight is an [OKX.AI](https://www.okx.ai/) A2MCP service. Point it at your
MCP endpoint and it runs a nine-point, commerce-grade check suite — including a
real, cryptographically-signed x402 purchase — then returns a pass/fail
scorecard with on-chain evidence at a shareable permalink.

It's CI for paid agents: catch a broken paywall, a price mismatch, or a
"paid-but-empty" delivery yourself, in seconds, instead of failing review or
burning a paying customer.

## The nine checks

| | Check | Fails when |
|---|---|---|
| C1 | Reachability & transport | endpoint down or 5xx |
| C2 | MCP handshake & tool list | handshake fails / zero tools |
| C3 | Declared vs exposed tools | a listed tool isn't actually there |
| C4 | x402 paywall challenge | paid tool doesn't return a valid 402 |
| C5 | Price integrity | 402 quote ≠ your listed price |
| C6 | Signed payment & settlement | a valid payment is rejected |
| C7 | Paid delivery has content | settles but returns nothing ("paid but empty") |
| C8 | Latency (p50 of 3) | slow enough to hurt agent workflows |
| C9 | Malformed-input resilience | bad input crashes the server |

C1, C2, C4, C5, C6, C7 are gating — a failure there fails the run. Every result
carries raw evidence (402 payload, settlement reference, latencies).

## Cash-free by design

This build never spends real money:

- PreFlight lists **free** (OKX allows free A2MCP listings).
- Settlement checks run on **mock** (offline, real signature crypto) or
  **base-sepolia** testnet (free faucet funds). **Mainnet payment is refused in
  code** regardless of configuration (`payer.py`).
- Both services run on **free hosting tiers**; platform subdomains satisfy the
  HTTPS + custom-domain requirement.

## Architecture

One Python process: FastAPI serving the report permalinks with a FastMCP app
(the A2MCP surface) mounted at `/mcp`. Runs execute synchronously inside the MCP
call under a 75-second budget with per-check timeboxes. Storage is one SQLite
table; the report page is server-rendered Jinja2. The buyer-side payer holds its
own throwaway EOA with hard spend caps and a kill switch. `BrokenBazaar` (under
`fixtures/`) is a separate, genuinely-remote deployment with toggleable bugs for
the demo. See `ARCHITECTURE.md` and `DECISIONS.md`.

## Layout

```
src/preflight/
  app.py            FastAPI + mounted FastMCP tools (preflight_run, get_report)
  runner.py         orchestration, time budgets, report assembly
  checks/suite.py   the nine checks
  payer.py          buyer-side x402 with cash-free guardrails
  x402kit.py        SDK-canonical challenge/sign/verify + offline MockFacilitator
  store.py          SQLite (reports + payment intent ledger)
  ssrf.py           target URL guard
  templates/, static/   report permalink page
fixtures/broken_bazaar/  the demo target (env-toggled BUG_PRICE / BUG_EMPTY)
scripts/
  golden_path.py    5x demo-done loop through PreFlight's own MCP surface
  verify_testnet.py user-run real testnet settlement go/no-go
tests/              22 unit + e2e tests
```

## Run it locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# tests (offline, mock network)
PYTHONPATH=src python -m pytest tests/ -q

# the golden path (boots everything, 5 rounds, all fixture states)
PYTHONPATH=src python scripts/golden_path.py
```

## Deploy & list

See `RUNBOOK.md` for the full path: deploy both services on a free tier, fund a
testnet EOA from the faucet, run `verify_testnet.py`, register the ASP on
OKX.AI, then post the demo. `DEMO_SCRIPT.md` has the ≤90-second recording script.

Status: core complete, 22/22 tests green, golden path 5/5. Working title —
final name pending a collision check before listing.
