from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# VM SKU definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VMSku:
    name: str     # e.g. "Standard_D4s_v5"
    series: str   # "D" or "E"
    vcpus: int
    ram_gb: int


DSERIES_V5: tuple[VMSku, ...] = (
    VMSku("Standard_D2s_v5",  "D",  2,   8),
    VMSku("Standard_D4s_v5",  "D",  4,  16),
    VMSku("Standard_D8s_v5",  "D",  8,  32),
    VMSku("Standard_D16s_v5", "D", 16,  64),
    VMSku("Standard_D32s_v5", "D", 32, 128),
    VMSku("Standard_D48s_v5", "D", 48, 192),
    VMSku("Standard_D64s_v5", "D", 64, 256),
    VMSku("Standard_D96s_v5", "D", 96, 384),
)

ESERIES_V5: tuple[VMSku, ...] = (
    VMSku("Standard_E2s_v5",  "E",  2,  16),
    VMSku("Standard_E4s_v5",  "E",  4,  32),
    VMSku("Standard_E8s_v5",  "E",  8,  64),
    VMSku("Standard_E16s_v5", "E", 16, 128),
    VMSku("Standard_E20s_v5", "E", 20, 160),
    VMSku("Standard_E32s_v5", "E", 32, 256),
    VMSku("Standard_E48s_v5", "E", 48, 384),
    VMSku("Standard_E64s_v5", "E", 64, 512),
    VMSku("Standard_E96s_v5", "E", 96, 672),
)

ALL_VM_SKUS: tuple[VMSku, ...] = DSERIES_V5 + ESERIES_V5


# ---------------------------------------------------------------------------
# SKU match result
# ---------------------------------------------------------------------------

@dataclass
class SKUMatch:
    sku: VMSku
    notes: list[str] = field(default_factory=list)


def find_vm_sku(vcpus: int, ram_gb: float) -> SKUMatch | None:
    """
    Find the smallest Azure VM SKU that satisfies the vCPU and RAM requirements.

    Strategy:
    1. Compute RAM/vCPU ratio. If > 8.0, prefer E-series; otherwise prefer D-series.
    2. Find all SKUs in preferred series that fit (vcpus >= req AND ram_gb >= req).
    3. Pick smallest by (vcpus, ram_gb).
    4. Fall back to other series if no match in preferred.
    5. Return None if nothing fits.
    """
    ratio = ram_gb / max(vcpus, 1)
    prefer_e = ratio > 8.0

    preferred_series = ESERIES_V5 if prefer_e else DSERIES_V5
    fallback_series = DSERIES_V5 if prefer_e else ESERIES_V5

    def _candidates(skus: tuple[VMSku, ...]) -> list[VMSku]:
        return [s for s in skus if s.vcpus >= vcpus and s.ram_gb >= ram_gb]

    preferred_candidates = _candidates(preferred_series)

    if preferred_candidates:
        best = min(preferred_candidates, key=lambda s: (s.vcpus, s.ram_gb))
        notes: list[str] = []
        if prefer_e:
            notes.append("E-series: high RAM/vCPU ratio")
        return SKUMatch(sku=best, notes=notes)

    # Try fallback series
    fallback_candidates = _candidates(fallback_series)
    if fallback_candidates:
        best = min(fallback_candidates, key=lambda s: (s.vcpus, s.ram_gb))
        notes = []
        if prefer_e:
            # Preferred was E but fell back to D
            notes.append("D-series fallback")
        else:
            # Preferred was D but fell back to E
            notes.append("E-series fallback")
        return SKUMatch(sku=best, notes=notes)

    return None


# ---------------------------------------------------------------------------
# Disk tier definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiskTier:
    tier: str      # e.g. "P10"
    max_gb: int
    api_sku: str   # e.g. "P10 LRS"


# Premium SSD tiers (sorted ascending by max_gb)
_PREMIUM_SSD_TIERS: tuple[DiskTier, ...] = (
    DiskTier("P1",   4,     "P1 LRS"),
    DiskTier("P2",   8,     "P2 LRS"),
    DiskTier("P3",   16,    "P3 LRS"),
    DiskTier("P4",   32,    "P4 LRS"),
    DiskTier("P6",   64,    "P6 LRS"),
    DiskTier("P10",  128,   "P10 LRS"),
    DiskTier("P15",  256,   "P15 LRS"),
    DiskTier("P20",  512,   "P20 LRS"),
    DiskTier("P30",  1024,  "P30 LRS"),
    DiskTier("P40",  2048,  "P40 LRS"),
    DiskTier("P50",  4096,  "P50 LRS"),
    DiskTier("P60",  8192,  "P60 LRS"),
    DiskTier("P70",  16384, "P70 LRS"),
    DiskTier("P80",  32767, "P80 LRS"),
)

# Standard SSD tiers (same size breaks as Premium SSD)
_STANDARD_SSD_TIERS: tuple[DiskTier, ...] = (
    DiskTier("E1",   4,     "E1 LRS"),
    DiskTier("E2",   8,     "E2 LRS"),
    DiskTier("E3",   16,    "E3 LRS"),
    DiskTier("E4",   32,    "E4 LRS"),
    DiskTier("E6",   64,    "E6 LRS"),
    DiskTier("E10",  128,   "E10 LRS"),
    DiskTier("E15",  256,   "E15 LRS"),
    DiskTier("E20",  512,   "E20 LRS"),
    DiskTier("E30",  1024,  "E30 LRS"),
    DiskTier("E40",  2048,  "E40 LRS"),
    DiskTier("E50",  4096,  "E50 LRS"),
    DiskTier("E60",  8192,  "E60 LRS"),
    DiskTier("E70",  16384, "E70 LRS"),
    DiskTier("E80",  32767, "E80 LRS"),
)

# Standard HDD tiers
_STANDARD_HDD_TIERS: tuple[DiskTier, ...] = (
    DiskTier("S4",   32,    "S4 LRS"),
    DiskTier("S6",   64,    "S6 LRS"),
    DiskTier("S10",  128,   "S10 LRS"),
    DiskTier("S15",  256,   "S15 LRS"),
    DiskTier("S20",  512,   "S20 LRS"),
    DiskTier("S30",  1024,  "S30 LRS"),
    DiskTier("S40",  2048,  "S40 LRS"),
    DiskTier("S50",  4096,  "S50 LRS"),
    DiskTier("S60",  8192,  "S60 LRS"),
    DiskTier("S70",  16384, "S70 LRS"),
    DiskTier("S80",  32767, "S80 LRS"),
)

_DISK_TIERS: dict[str, tuple[DiskTier, ...]] = {
    "premium-ssd":   _PREMIUM_SSD_TIERS,
    "standard-ssd":  _STANDARD_SSD_TIERS,
    "standard-hdd":  _STANDARD_HDD_TIERS,
}


def find_disk_tier(capacity_gb: float, disk_type: str) -> DiskTier | None:
    """
    Return the smallest disk tier (by max_gb) that accommodates capacity_gb.
    Returns None if capacity exceeds the largest tier for the given disk_type.

    Parameters
    ----------
    capacity_gb:
        Required disk capacity in GB.
    disk_type:
        One of "premium-ssd", "standard-ssd", "standard-hdd".
    """
    tiers = _DISK_TIERS.get(disk_type)
    if tiers is None:
        raise ValueError(
            f"Unknown disk type '{disk_type}'. "
            f"Valid types: {list(_DISK_TIERS.keys())}"
        )

    for tier in tiers:  # already sorted ascending
        if tier.max_gb >= capacity_gb:
            return tier

    return None  # exceeds largest tier
