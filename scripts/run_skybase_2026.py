"""Skybase 2026 multi-month settlement runner — Jan through May.

Skybase is an agent-rate-only prime (no allocator ilk, no supply-side
venues): the settlement is the agent rate (SSR + 20bps) on the subproxy's
treasury holdings. NOTE: forum demand-side totals additionally include
Distribution Rewards, which this pipeline does not compute. See
``_run_agent_rate_prime.py`` for the shared loop.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/run_skybase_2026.py
"""

from _run_agent_rate_prime import run

if __name__ == "__main__":
    raise SystemExit(run("skybase"))
