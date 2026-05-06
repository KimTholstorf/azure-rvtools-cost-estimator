from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .sku_mapper import ALL_VM_SKUS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOURS_PER_MONTH: int = 730
CACHE_TTL_HOURS: int = 24
API_BASE: str = "https://prices.azure.com/api/retail/prices"
API_VERSION: str = "2023-01-01-preview"

# Product name fragments used in API OData filters
_VM_PRODUCTS: dict[str, str] = {
    "Dsv5": "Dsv5",
    "Esv5": "Esv5",
}

_DISK_PRODUCTS: dict[str, str] = {
    "premium-ssd":  "Premium SSD Managed Disks",
    "standard-ssd": "Standard SSD Managed Disks",
    "standard-hdd": "Standard HDD Managed Disks",
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class VMPrices:
    payg_hourly: float
    reserved_1yr_hourly: float | None = None  # None = no reservation price found
    reserved_3yr_hourly: float | None = None

    @property
    def payg_monthly(self) -> float:
        return self.payg_hourly * HOURS_PER_MONTH

    @property
    def reserved_1yr_monthly(self) -> float | None:
        if self.reserved_1yr_hourly is None:
            return None
        return self.reserved_1yr_hourly * HOURS_PER_MONTH

    @property
    def reserved_3yr_monthly(self) -> float | None:
        if self.reserved_3yr_hourly is None:
            return None
        return self.reserved_3yr_hourly * HOURS_PER_MONTH


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(region: str, currency: str) -> Path:
    cache_dir = Path.home() / ".cache" / "azure-rvtools"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"prices_{region}_{currency}.json"


def _is_cache_valid(cache_file: Path) -> bool:
    """Return True if the cache file exists and is less than CACHE_TTL_HOURS old."""
    if not cache_file.exists():
        return False
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        age_hours = (
            datetime.now(timezone.utc) - fetched_at
        ).total_seconds() / 3600
        return age_hours < CACHE_TTL_HOURS
    except Exception:
        return False


def _load_cache(cache_file: Path) -> dict:
    return json.loads(cache_file.read_text(encoding="utf-8"))


def _save_cache(cache_file: Path, data: dict) -> None:
    cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _build_filter(parts: dict[str, str]) -> str:
    """Build an OData filter string from key-value equality pairs."""
    clauses = [f"{k} eq '{v}'" for k, v in parts.items()]
    return " and ".join(clauses)


def _fetch_all_pages(url: str) -> list[dict]:
    """Fetch all pages from the Azure Retail Prices API and return all items."""
    items: list[dict] = []
    next_url: str | None = url

    while next_url:
        with urllib.request.urlopen(next_url, timeout=30) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))

        items.extend(body.get("Items", []))
        next_url = body.get("NextPageLink")

    return items


def _make_api_url(odata_filter: str, currency: str) -> str:
    params = urllib.parse.urlencode(
        {
            "api-version": API_VERSION,
            "$filter": odata_filter,
            "currencyCode": currency,
        }
    )
    return f"{API_BASE}?{params}"


def _is_spot_or_low(item: dict) -> bool:
    sku_name: str = item.get("skuName", "").lower()
    return "spot" in sku_name or "low priority" in sku_name


def _detect_os_from_product(product_name: str) -> str:
    if "windows" in product_name.lower():
        return "windows"
    return "linux"


# ---------------------------------------------------------------------------
# Fetching logic
# ---------------------------------------------------------------------------

def _fetch_vm_prices(region: str, currency: str) -> dict[str, dict]:
    """
    Fetch PAYG and 1-year reservation prices for Dsv5 and Esv5 VM series.

    Returns dict: sku_name → {linux_payg, windows_payg, linux_1yr, windows_1yr}
    All values are hourly prices. Missing values are omitted from the dict.
    """
    # Build set of known SKU names for quick validation
    known_skus = {s.name for s in ALL_VM_SKUS}

    # Accumulate prices; keep the minimum when duplicates appear
    prices: dict[str, dict[str, float]] = {}

    def _upsert(sku_name: str, key: str, value: float) -> None:
        prices.setdefault(sku_name, {})
        existing = prices[sku_name].get(key)
        if existing is None or value < existing:
            prices[sku_name][key] = value

    for series_key, series_fragment in _VM_PRODUCTS.items():
        # ---- PAYG ----
        print(
            f"[INFO] Fetching prices for Virtual Machines {series_key} (PAYG)...",
            file=sys.stderr,
        )
        base_filter = _build_filter({
            "serviceName": "Virtual Machines",
            "armRegionName": region,
            "priceType": "Consumption",
        })
        full_filter = f"{base_filter} and contains(productName, '{series_fragment}')"
        for item in _fetch_all_pages(_make_api_url(full_filter, currency)):
            if _is_spot_or_low(item):
                continue
            arm_sku: str = item.get("armSkuName", "").strip()
            if arm_sku not in known_skus:
                continue
            product_name: str = item.get("productName", "")
            os_type = _detect_os_from_product(product_name)
            # retailPrice for Consumption is an hourly rate
            _upsert(arm_sku, f"{os_type}_payg", item.get("retailPrice", 0.0))

        # ---- Reservations: 1-year and 3-year ----
        # Azure reservation products are compute-only (no separate Windows SKU in API).
        # retailPrice for Reservation is the TOTAL cost for the full term:
        #   1yr → divide by 8,760 to get effective hourly rate
        #   3yr → divide by 26,280 (3 × 8,760) to get effective hourly rate
        for term_api, term_label, price_key, term_hours in [
            ("1 Year",  "1-Year Reserved", "1yr",  8_760),
            ("3 Years", "3-Year Reserved", "3yr", 26_280),
        ]:
            print(
                f"[INFO] Fetching prices for Virtual Machines {series_key} ({term_label})...",
                file=sys.stderr,
            )
            base_filter = _build_filter({
                "serviceName": "Virtual Machines",
                "armRegionName": region,
                "priceType": "Reservation",
                "reservationTerm": term_api,
            })
            full_filter = f"{base_filter} and contains(productName, '{series_fragment}')"
            for item in _fetch_all_pages(_make_api_url(full_filter, currency)):
                if _is_spot_or_low(item):
                    continue
                arm_sku = item.get("armSkuName", "").strip()
                if arm_sku not in known_skus:
                    continue
                _upsert(arm_sku, f"linux_{price_key}", item.get("retailPrice", 0.0) / term_hours)

    # Azure Reserved VMs are priced at the compute-only (Linux) rate.
    # Windows Server license premium is NOT included in the reservation and
    # remains a PAYG charge on top of the reserved compute price.
    # Therefore: windows_Xyr_hourly = linux_Xyr_hourly + (windows_payg - linux_payg)
    for sku_name, entry in prices.items():
        linux_payg = entry.get("linux_payg", 0.0)
        windows_payg = entry.get("windows_payg", 0.0)
        win_premium = max(0.0, windows_payg - linux_payg)
        for price_key in ("1yr", "3yr"):
            linux_rsv = entry.get(f"linux_{price_key}")
            if linux_rsv is not None and f"windows_{price_key}" not in entry:
                entry[f"windows_{price_key}"] = linux_rsv + win_premium

    return prices


def _fetch_disk_prices(region: str, currency: str, disk_type: str) -> dict[str, float]:
    """
    Fetch monthly prices for managed disk tiers.

    Returns dict: tier_name (e.g. "P10") → monthly_price
    """
    product_name = _DISK_PRODUCTS.get(disk_type)
    if product_name is None:
        raise ValueError(f"Unknown disk type: {disk_type}")

    print(f"[INFO] Fetching disk prices ({disk_type})...", file=sys.stderr)

    odata_filter_parts = {
        "serviceName": "Storage",
        "armRegionName": region,
        "priceType": "Consumption",
    }
    base_filter = _build_filter(odata_filter_parts)
    full_filter = f"{base_filter} and productName eq '{product_name}'"
    url = _make_api_url(full_filter, currency)
    items = _fetch_all_pages(url)

    prices: dict[str, float] = {}
    for item in items:
        sku_name: str = item.get("skuName", "").strip()
        # Strip " LRS" / " ZRS" suffix → tier name
        tier = sku_name.replace(" LRS", "").replace(" ZRS", "").strip()
        retail_price: float = item.get("retailPrice", 0.0)
        if tier and retail_price > 0:
            existing = prices.get(tier)
            if existing is None or retail_price < existing:
                prices[tier] = retail_price

    return prices


# ---------------------------------------------------------------------------
# PricingClient
# ---------------------------------------------------------------------------

class PricingClient:
    """
    Client for Azure Retail Prices API with local disk-based caching.

    Cache file: ~/.cache/azure-rvtools/prices_{region}_{currency}.json
    """

    def __init__(
        self,
        region: str,
        currency: str = "USD",
        no_cache: bool = False,
    ) -> None:
        self.region = region
        self.currency = currency
        self.no_cache = no_cache
        self._cache_file = _cache_path(region, currency)

        # In-memory store
        self._vm_prices: dict[str, dict[str, float]] = {}
        self._disk_prices: dict[str, dict[str, float]] = {}  # disk_type → {tier: price}
        self._loaded_disk_types: set[str] = set()
        self._vm_prices_loaded: bool = False

    # ------------------------------------------------------------------
    # Loading / caching
    # ------------------------------------------------------------------

    def ensure_loaded(self, disk_type: str) -> None:
        """
        Ensure VM prices and disk prices for the given disk_type are loaded.
        Uses cache if valid and no_cache is False; otherwise fetches from API.
        """
        need_vm = not self._vm_prices_loaded
        need_disk = disk_type not in self._loaded_disk_types

        if not (need_vm or need_disk):
            return

        # Try cache first
        if not self.no_cache and _is_cache_valid(self._cache_file):
            print(
                "[INFO] Using cached prices (< 24h old). Use --no-cache to refresh.",
                file=sys.stderr,
            )
            cached = _load_cache(self._cache_file)
            self._vm_prices = cached.get("vm_prices", {})
            raw_disk = cached.get("disk_prices", {})
            for dt, tiers in raw_disk.items():
                self._disk_prices[dt] = tiers
                self._loaded_disk_types.add(dt)
            self._vm_prices_loaded = True

            # If the disk_type we need is already in the cache, we're done
            if disk_type in self._loaded_disk_types:
                return

        # Need to (re-)fetch from API
        print(
            f"[INFO] Fetching Azure prices for {self.region}...",
            file=sys.stderr,
        )

        if need_vm or not self._vm_prices_loaded:
            self._vm_prices = _fetch_vm_prices(self.region, self.currency)
            self._vm_prices_loaded = True

        if disk_type not in self._loaded_disk_types:
            self._disk_prices[disk_type] = _fetch_disk_prices(
                self.region, self.currency, disk_type
            )
            self._loaded_disk_types.add(disk_type)

        # Persist cache
        self._save_to_cache()

    def _save_to_cache(self) -> None:
        data = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "region": self.region,
            "currency": self.currency,
            "vm_prices": self._vm_prices,
            "disk_prices": self._disk_prices,
        }
        _save_cache(self._cache_file, data)
        print(
            f"[INFO] Cached prices saved to {self._cache_file}",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # Price lookups
    # ------------------------------------------------------------------

    def get_vm_prices(
        self,
        sku_name: str,
        os_type: str,
        hybrid_benefit: bool = False,
    ) -> VMPrices:
        """
        Return VMPrices for a given SKU and OS type.
        Falls back to 0.0 if the SKU is not in the price data.

        Parameters
        ----------
        hybrid_benefit:
            When True, apply Linux pricing for all VMs regardless of OS.
            Models Azure Hybrid Benefit for customers with existing Windows
            Server Software Assurance licenses.
        """
        # With Azure Hybrid Benefit, customers pay the Linux compute rate
        effective_os = "linux" if hybrid_benefit else os_type

        entry = self._vm_prices.get(sku_name, {})
        payg_hourly = entry.get(f"{effective_os}_payg", 0.0)
        rsv_1yr_hourly = entry.get(f"{effective_os}_1yr")
        rsv_3yr_hourly = entry.get(f"{effective_os}_3yr")

        return VMPrices(
            payg_hourly=payg_hourly,
            reserved_1yr_hourly=rsv_1yr_hourly,
            reserved_3yr_hourly=rsv_3yr_hourly,
        )

    def get_disk_price(self, tier: str, disk_type: str) -> float:
        """
        Return monthly price for a disk tier. Returns 0.0 if not found.

        Parameters
        ----------
        tier:
            Tier name, e.g. "P10", "E20", "S30".
        disk_type:
            One of "premium-ssd", "standard-ssd", "standard-hdd".
        """
        type_prices = self._disk_prices.get(disk_type, {})
        return type_prices.get(tier, 0.0)
