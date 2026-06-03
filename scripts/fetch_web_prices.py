#!/usr/bin/env python3
"""
Fetch Azure pricing data for all supported regions and currencies, writing one
JSON file per region to docs/prices/ for use by the browser-based web app.

Output format: prices_{region}.json  (all currencies embedded)
  {
    "fetched_at": "...",
    "region": "westeurope",
    "currencies": {
      "USD": {"vm_prices": {...}, "disk_prices": {...}},
      "EUR": {"vm_prices": {...}, "disk_prices": {...}},
      ...
    }
  }

Currencies are fetched in parallel (one thread per currency) within each
region, keeping max concurrent connections to 6 while being sequential
across regions to avoid hammering the Azure Retail Prices API.

Usage:
    python scripts/fetch_web_prices.py

Typically run by the GitHub Actions update-prices workflow on a weekly
schedule, but can also be run locally with outbound internet access.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from azure_rvtools.excel import _REGION_NAMES
import azure_rvtools.pricing as _pricing_module
from azure_rvtools.pricing import _fetch_disk_prices, _fetch_vm_prices

# Suppress all [INFO] print output from the pricing module —
# monkey-patching the module's print is more reliable than redirecting
# sys.stderr when rich is managing the terminal.
_pricing_module.print = lambda *args, **kwargs: None  # type: ignore[attr-defined]

try:
    from rich.console import Console
    from rich.live import Live
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    RICH = True
except ImportError:
    RICH = False

OUTPUT_DIR    = Path(__file__).parent.parent / "docs" / "prices"
CURRENCIES    = ["USD", "EUR", "GBP", "DKK", "SEK", "NOK"]
DISK_TYPES    = ["premium-ssd", "standard-ssd", "standard-hdd"]
SPINNER       = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# ---------------------------------------------------------------------------
# Fetch helper
# ---------------------------------------------------------------------------

def _fetch_one_currency(region: str, currency: str) -> tuple[str, dict]:
    """Fetch VM + all disk prices for one region/currency."""
    vm_prices = _fetch_vm_prices(region, currency)
    disk_prices: dict[str, dict] = {}
    for disk_type in DISK_TYPES:
        disk_prices[disk_type] = _fetch_disk_prices(region, currency, disk_type)
    return currency, {"vm_prices": vm_prices, "disk_prices": disk_prices}


# ---------------------------------------------------------------------------
# Rich display builder
# ---------------------------------------------------------------------------

def _make_display(
    regions: list[str],
    region_status: dict[str, str],
    current_region: str,
    currency_status: dict[str, str],
    completed: int,
    elapsed: float,
) -> Table:
    total  = len(regions)
    frame  = SPINNER[int(time.monotonic() * 10) % len(SPINNER)]
    mins, secs = divmod(int(elapsed), 60)

    # Outer table — no borders, holds all sections
    outer = Table.grid(padding=0)
    outer.add_column()

    # Title
    outer.add_row(Text(""))
    outer.add_row(
        Text(
            f"  Azure Web Pricing Fetcher — {total} regions × {len(CURRENCIES)} currencies",
            style="bold",
        )
    )
    outer.add_row(Rule(style="dim"))

    # Region grid — 3 columns
    grid = Table.grid(padding=(0, 2))
    for _ in range(3):
        grid.add_column(width=22)

    rows = [regions[i:i + 3] for i in range(0, len(regions), 3)]
    for row_regions in rows:
        cells = []
        for region in row_regions:
            st = region_status.get(region, "waiting")
            if st == "done":
                cells.append(Text(f"  ✓ {region}", style="green"))
            elif st == "active":
                cells.append(Text(f"  ● {region}", style="bold yellow"))
            elif st == "failed":
                cells.append(Text(f"  ✗ {region}", style="bold red"))
            else:
                cells.append(Text(f"  ○ {region}", style="dim"))
        while len(cells) < 3:
            cells.append(Text(""))
        grid.add_row(*cells)

    outer.add_row(grid)
    outer.add_row(Rule(style="dim"))

    # Currently fetching
    outer.add_row(
        Text(f"  Currently fetching   {current_region or '—'}", style="bold cyan")
    )

    # Currency status line
    currency_text = Text("  ")
    for c in CURRENCIES:
        cs = currency_status.get(c, "waiting")
        if cs == "done":
            currency_text.append(f"{c} ✓   ", style="green")
        elif cs == "active":
            currency_text.append(f"{c} {frame}   ", style="yellow")
        elif cs == "failed":
            currency_text.append(f"{c} ✗   ", style="bold red")
        else:
            currency_text.append(f"{c} ○   ", style="dim")
    outer.add_row(currency_text)
    outer.add_row(Text(""))

    # Progress bar
    bar_width = 38
    filled = int(completed / total * bar_width) if total else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    outer.add_row(
        Text(f"  {bar}   {completed}/{total} regions   Elapsed: {mins}m {secs:02d}s")
    )
    outer.add_row(Text(""))

    return outer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    start_time = time.monotonic()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Remove old single-currency files
    for old_file in OUTPUT_DIR.glob("prices_*_*.json"):
        old_file.unlink()

    regions = list(_REGION_NAMES.keys())
    failed: list[str] = []

    region_status: dict[str, str]   = {r: "waiting" for r in regions}
    currency_status: dict[str, str] = {c: "waiting" for c in CURRENCIES}
    current_region = ""
    completed      = 0

    if RICH:
        console = Console()

        with Live(
            _make_display(regions, region_status, current_region,
                          currency_status, completed, 0),
            console=console,
            refresh_per_second=10,
        ) as live:

            def refresh() -> None:
                live.update(_make_display(
                    regions, region_status, current_region,
                    currency_status, completed,
                    time.monotonic() - start_time,
                ))

            # Background thread keeps the spinner animating at 10fps
            # independently of when futures complete.
            _stop = threading.Event()
            def _auto_refresh() -> None:
                while not _stop.is_set():
                    refresh()
                    time.sleep(0.1)
            threading.Thread(target=_auto_refresh, daemon=True).start()

            for region in regions:
                current_region        = region
                region_status[region] = "active"
                currency_status       = {c: "active" for c in CURRENCIES}
                refresh()

                currencies_data: dict[str, dict] = {}

                with ThreadPoolExecutor(max_workers=len(CURRENCIES)) as executor:
                    futures = {
                        executor.submit(_fetch_one_currency, region, c): c
                        for c in CURRENCIES
                    }
                    for future in as_completed(futures):
                        c = futures[future]
                        try:
                            _, data = future.result()
                            currencies_data[c] = data
                            currency_status[c]  = "done"
                        except Exception as exc:  # noqa: BLE001
                            currency_status[c] = "failed"
                            console.print(f"  [red]✗ {region}/{c}: {exc}[/red]")
                        refresh()

                if currencies_data:
                    out = {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "region":     region,
                        "currencies": currencies_data,
                    }
                    (OUTPUT_DIR / f"prices_{region}.json").write_text(
                        json.dumps(out, indent=2), encoding="utf-8"
                    )
                    region_status[region] = "done"
                else:
                    region_status[region] = "failed"
                    failed.append(region)

                completed += 1
                refresh()

                if region != regions[-1]:
                    time.sleep(0.3)

            _stop.set()

    else:
        # ── Plain fallback (no rich) ───────────────────────────────────────
        print(f"Fetching {len(CURRENCIES)} currencies × {len(regions)} regions\n")

        for i, region in enumerate(regions, 1):
            current_region  = region
            currency_status = {c: "active" for c in CURRENCIES}
            currencies_data: dict[str, dict] = {}

            print(f"[{i}/{len(regions)}] {region}", flush=True)

            def _print_status() -> None:
                marks = {"done": "✓", "failed": "✗", "active": "..."}
                line = "  " + "  ".join(
                    f"{c} {marks.get(currency_status[c], '○')}"
                    for c in CURRENCIES
                )
                print(f"\r{line}", end="", flush=True)

            _print_status()

            with ThreadPoolExecutor(max_workers=len(CURRENCIES)) as executor:
                futures = {
                    executor.submit(_fetch_one_currency, region, c): c
                    for c in CURRENCIES
                }
                for future in as_completed(futures):
                    c = futures[future]
                    try:
                        _, data = future.result()
                        currencies_data[c] = data
                        currency_status[c]  = "done"
                    except Exception as exc:  # noqa: BLE001
                        currency_status[c] = "failed"
                        print(f"\n  ✗ {c}: {exc}")
                    _print_status()

            print(flush=True)

            if currencies_data:
                out = {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "region":     region,
                    "currencies": currencies_data,
                }
                (OUTPUT_DIR / f"prices_{region}.json").write_text(
                    json.dumps(out, indent=2), encoding="utf-8"
                )
                print("  ✓ saved", flush=True)
            else:
                failed.append(region)
                print("  ✗ all currencies failed", flush=True)

            completed += 1
            if region != regions[-1]:
                time.sleep(0.3)

    elapsed = time.monotonic() - start_time
    mins, secs = divmod(int(elapsed), 60)

    print(f"\n{'─' * 60}")
    print(
        f"Done in {mins}m {secs:02d}s.  "
        f"{len(regions) - len(failed)}/{len(regions)} regions succeeded."
    )
    if failed:
        print(f"Failed regions: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
