#!/usr/bin/env python3
"""
Fetch Azure pricing data for all supported regions and write per-region JSON
files to docs/prices/ for use by the browser-based web app.

Each output file (prices_{region}_USD.json) uses the same format as the CLI
price cache — the web app monkey-patches PricingClient to read from these
files instead of calling the Azure Retail Prices API (which is blocked by
browser CORS policy).

Usage:
    python scripts/fetch_web_prices.py

Typically run by the GitHub Actions update-prices workflow on a weekly
schedule, but can also be run locally with outbound internet access.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from azure_rvtools.excel import _REGION_NAMES
from azure_rvtools.pricing import PricingClient

OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "prices"
CURRENCY = "USD"
DISK_TYPES = ["premium-ssd", "standard-ssd", "standard-hdd"]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    regions = list(_REGION_NAMES.keys())
    print(f"Fetching prices for {len(regions)} regions → {OUTPUT_DIR}")

    failed: list[str] = []

    for i, region in enumerate(regions, 1):
        print(f"\n[{i}/{len(regions)}] {region}", flush=True)
        try:
            client = PricingClient(region=region, currency=CURRENCY, no_cache=True)
            # Redirect cache output to docs/prices/ instead of ~/.cache/azure-rvtools/
            client._cache_file = OUTPUT_DIR / f"prices_{region}_{CURRENCY}.json"

            for disk_type in DISK_TYPES:
                client.ensure_loaded(disk_type)

            print(f"  ✓ {client._cache_file.name}", flush=True)

        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ Failed: {exc}", file=sys.stderr)
            failed.append(region)

        # Brief pause between regions to be a polite API consumer
        if i < len(regions):
            time.sleep(0.5)

    print(f"\n{'─' * 60}")
    print(f"Done. {len(regions) - len(failed)}/{len(regions)} regions succeeded.")
    if failed:
        print(f"Failed regions: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
