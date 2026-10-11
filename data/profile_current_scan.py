"""Profile a read-only current broad plan against an isolated temporary cache.

Run ``python -m data.profile_current_scan``. This never saves a plan, opens a
portfolio database, or runs a historical benchmark. Cold and warm runs share
only the disposable cache created for this process.
"""

from __future__ import annotations

import json
import argparse
import re
import tempfile
from datetime import datetime, timezone
from functools import partial
from time import perf_counter

from data.market_data import load_market_data
from data.scanner_universe import BROAD_CURRENT_SCANNER_UNIVERSE
from paper_trading.trading_plan import CONSENSUS_MODE, load_and_build_trading_plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    account = {"Starting Capital": 100_000.0, "Cash": 100_000.0, "Realized P&L": 0.0}
    print(f"Broad symbols: {len(BROAD_CURRENT_SCANNER_UNIVERSE)}; UTC start: {datetime.now(timezone.utc).isoformat()}", flush=True)
    with tempfile.TemporaryDirectory(prefix="stock-comp-scan-profile-") as cache_dir:
        loader = partial(
            load_market_data, cache_dir=cache_dir,
            max_workers=args.max_workers, live_failure_retry=True,
        )
        for pass_name in ("cold", "warm"):
            started = perf_counter()
            last_stage = None

            def progress(stage, detail):
                nonlocal last_stage
                match = re.search(r"\((\d+)/(\d+)\)", detail)
                should_log = stage != last_stage or (match and (int(match.group(1)) % 25 == 0 or int(match.group(1)) == int(match.group(2))))
                if should_log:
                    print(f"{pass_name} +{perf_counter() - started:.1f}s {stage}: {detail}", flush=True)
                last_stage = stage

            plan = load_and_build_trading_plan(
                CONSENSUS_MODE, account, [], universe=BROAD_CURRENT_SCANNER_UNIVERSE,
                load_market_data_fn=loader, status_callback=progress,
            )
            coverage = plan["scan_coverage"]
            print(json.dumps({
                "pass": pass_name,
                "elapsed_seconds": round(perf_counter() - started, 3),
                "stage_timings": plan["scan_stage_timings"],
                "coverage": {key: value for key, value in coverage.items() if key != "issues"},
                "issues": coverage["issues"],
                "downloaded_tickers": len(plan["cache_info"]["Downloaded Tickers"]),
                "data_source_failures": plan["cache_info"]["Data Source Failures"],
                "buy_count": len(plan["buys"]),
            }, default=str), flush=True)


if __name__ == "__main__":
    main()
