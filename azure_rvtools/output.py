from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field

from tabulate import tabulate

from .rvtools import VMRecord
from .sku_mapper import VMSku, ALL_VM_SKUS


# ---------------------------------------------------------------------------
# Max SKU limits (used for "no match" message)
# ---------------------------------------------------------------------------

_MAX_VCPUS = max(s.vcpus for s in ALL_VM_SKUS)
_MAX_RAM_GB = max(s.ram_gb for s in ALL_VM_SKUS)


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class VMResult:
    vm: VMRecord
    sku: VMSku | None
    sku_notes: list[str]
    disk_tiers: list[tuple[str, int]]       # [("P10", 3), ("P30", 1)] sorted by tier
    payg_compute_monthly: float
    reserved_compute_monthly: float | None
    disk_monthly: float

    @property
    def payg_total_monthly(self) -> float:
        return self.payg_compute_monthly + self.disk_monthly

    @property
    def reserved_total_monthly(self) -> float | None:
        if self.reserved_compute_monthly is None:
            return None
        return self.reserved_compute_monthly + self.disk_monthly


@dataclass
class ReservationRec:
    sku_name: str
    vm_count: int
    payg_compute_monthly: float    # total for all VMs on this SKU
    rsv_compute_monthly: float     # total reserved for all VMs on this SKU

    @property
    def savings_monthly(self) -> float:
        return self.payg_compute_monthly - self.rsv_compute_monthly

    @property
    def savings_pct(self) -> float:
        if self.payg_compute_monthly == 0:
            return 0.0
        return self.savings_monthly / self.payg_compute_monthly * 100


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_disk_summary(tiers: list[tuple[str, int]]) -> str:
    """Return e.g. '3× P10, 1× P30' or '—' if empty."""
    if not tiers:
        return "\u2014"
    parts = [f"{count}\u00d7 {tier}" for tier, count in tiers]
    return ", ".join(parts)


def _fmt_ram(ram_gb: float) -> str:
    """Format RAM as integer if whole number, else 1 decimal."""
    import math
    if math.isnan(ram_gb) or math.isinf(ram_gb):
        return "0"
    if ram_gb == int(ram_gb):
        return str(int(ram_gb))
    return f"{ram_gb:.1f}"


def _fmt_money(val: float | None, currency: str = "USD") -> str:
    """Format monetary value. Returns '—' for None."""
    if val is None:
        return "\u2014"
    return f"${val:,.2f}"


def _fmt_divider(width: int = 72) -> str:
    return "\u2500" * width


def _rsv_label(reserved_term: str) -> str:
    """Return short reservation term label, e.g. '3-Yr' or '1-Yr'."""
    return "3-Yr" if reserved_term == "3-year" else "1-Yr"


def _rsv_label_long(reserved_term: str) -> str:
    """Return long reservation term label, e.g. '3-Year Reserved'."""
    return "3-Year Reserved" if reserved_term == "3-year" else "1-Year Reserved"


# ---------------------------------------------------------------------------
# VM table
# ---------------------------------------------------------------------------

def print_vm_table(
    results: list[VMResult],
    pricing_mode: str,
    currency: str,
    reserved_term: str = "3-year",
    stream=sys.stdout,
) -> None:
    """
    Print a per-VM table to *stream* using tabulate.

    pricing_mode: "all-payg" | "all-reserved" | "optimized"
    """
    show_reserved = pricing_mode in ("all-reserved", "optimized")
    rsv_col = f"{_rsv_label(reserved_term)} Rsv/mo"

    headers: list[str]
    if show_reserved:
        headers = [
            "VM Name",
            "vCPUs",
            "RAM (GB)",
            "Azure SKU",
            "OS",
            "Disks",
            "PAYG/mo",
            rsv_col,
            "Notes",
        ]
    else:
        headers = [
            "VM Name",
            "vCPUs",
            "RAM (GB)",
            "Azure SKU",
            "OS",
            "Disks",
            "PAYG/mo",
            "Notes",
        ]

    rows: list[list] = []
    for r in results:
        notes_parts: list[str] = []

        if not r.vm.is_powered_on:
            notes_parts.append("Powered off")

        if r.sku is None:
            notes_parts.append(
                f"No match (>{_MAX_VCPUS} vCPU or >{_MAX_RAM_GB}GB RAM)"
            )
        else:
            notes_parts.extend(r.sku_notes)

        notes_str = "; ".join(notes_parts) if notes_parts else ""
        sku_name = r.sku.name if r.sku else "\u2014"
        disk_str = format_disk_summary(r.disk_tiers)

        payg_str = _fmt_money(r.payg_compute_monthly + r.disk_monthly) if r.sku else "\u2014"
        rsv_str = _fmt_money(r.reserved_total_monthly) if r.sku else "\u2014"

        row: list
        if show_reserved:
            row = [
                r.vm.name,
                r.vm.vcpus,
                _fmt_ram(r.vm.ram_gb),
                sku_name,
                r.vm.os_type,
                disk_str,
                payg_str,
                rsv_str,
                notes_str,
            ]
        else:
            row = [
                r.vm.name,
                r.vm.vcpus,
                _fmt_ram(r.vm.ram_gb),
                sku_name,
                r.vm.os_type,
                disk_str,
                payg_str,
                notes_str,
            ]

        rows.append(row)

    print(tabulate(rows, headers=headers, tablefmt="simple"), file=stream)


# ---------------------------------------------------------------------------
# Summary block
# ---------------------------------------------------------------------------

def print_summary(
    results: list[VMResult],
    recommendations: list[ReservationRec],
    pricing_mode: str,
    currency: str,
    region: str,
    disk_type: str,
    disk_source: str = "provisioned",
    reserved_term: str = "3-year",
    hybrid_benefit: bool = False,
    support_plan: str = "basic",
    realistic_top_skus: frozenset[str] = frozenset(),
    stream=sys.stdout,
) -> None:
    """Print a summary block after the VM table."""
    show_reserved = pricing_mode in ("all-reserved", "realistic")

    powered_on = [r for r in results if r.vm.is_powered_on]
    powered_off = [r for r in results if not r.vm.is_powered_on]

    total_vcpus = sum(r.vm.vcpus for r in powered_on)
    total_ram_gb = sum(r.vm.ram_gb for r in powered_on)

    total_disk_count = sum(len(r.vm.effective_disks) for r in powered_on)
    total_disk_monthly = sum(r.disk_monthly for r in powered_on)

    total_payg_compute = sum(r.payg_compute_monthly for r in powered_on)
    total_payg = total_payg_compute + total_disk_monthly

    rsv_long = _rsv_label_long(reserved_term)
    divider = _fmt_divider()
    print(divider, file=stream)
    print(f"  Region:    {region}", file=stream)
    print(f"  Disk type: {disk_type}", file=stream)
    if hybrid_benefit:
        print("  Azure Hybrid Benefit: enabled (Linux compute pricing applied)", file=stream)
    if support_plan and support_plan != "basic":
        print(f"  Support:   {support_plan}", file=stream)
    print(file=stream)

    vm_count_str = f"{len(powered_on)} powered-on VM(s)"
    if powered_off:
        vm_count_str += f", {len(powered_off)} powered-off (excluded from costs)"
    print(f"  VMs:       {vm_count_str}", file=stream)
    print(f"  vCPUs:     {total_vcpus:,}", file=stream)
    print(f"  RAM:       {_fmt_ram(total_ram_gb)} GB", file=stream)
    disk_source_label = "in-use capacity" if disk_source == "in-use" else "provisioned capacity"
    print(f"  Disks:     {total_disk_count:,} ({disk_type}, {disk_source_label})", file=stream)
    print(file=stream)

    print(f"  Total PAYG/mo:         ${total_payg:>12,.2f}", file=stream)
    print(f"    Compute:             ${total_payg_compute:>12,.2f}", file=stream)
    print(f"    Disk:                ${total_disk_monthly:>12,.2f}", file=stream)

    if show_reserved:
        if pricing_mode == "realistic":
            # Top SKUs use reserved pricing, rest use PAYG
            effective_compute = sum(
                (r.reserved_compute_monthly
                 if r.sku and r.sku.name in realistic_top_skus and r.reserved_compute_monthly is not None
                 else r.payg_compute_monthly)
                for r in powered_on if r.sku is not None
            )
            total_rsv_compute = effective_compute
            mode_label = f"Realistic ({rsv_long})"
        else:
            # All reserved
            rsv_eligible = [
                r for r in powered_on
                if r.sku is not None and r.reserved_compute_monthly is not None
            ]
            total_rsv_compute = sum(r.reserved_compute_monthly for r in rsv_eligible)  # type: ignore[arg-type]
            mode_label = rsv_long

        total_rsv = total_rsv_compute + total_disk_monthly

        print(file=stream)
        print(f"  Total {mode_label}/mo:".ljust(26) + f"${total_rsv:>12,.2f}", file=stream)
        print(f"    Compute (effective): ${total_rsv_compute:>12,.2f}", file=stream)
        print(f"    Disk (unchanged):    ${total_disk_monthly:>12,.2f}", file=stream)

        if total_payg_compute > 0:
            savings_compute = total_payg_compute - total_rsv_compute
            savings_pct = savings_compute / total_payg_compute * 100
            print(file=stream)
            print(
                f"  Compute savings ({_rsv_label(reserved_term).lower()}): "
                f"${savings_compute:>11,.2f}/mo  ({savings_pct:.1f}%)",
                file=stream,
            )

    print(divider, file=stream)


# ---------------------------------------------------------------------------
# Reservation recommendations table
# ---------------------------------------------------------------------------

def print_recommendations(
    recs: list[ReservationRec],
    currency: str,
    total_disk_monthly: float,
    reserved_term: str = "3-year",
    stream=sys.stdout,
) -> None:
    """
    Print reservation recommendations table (optimized mode only).
    *recs* should already be sorted by savings_monthly descending.
    """
    headers = [
        "SKU",
        "VMs",
        "PAYG Compute/mo",
        f"{_rsv_label(reserved_term)} Rsv/mo",
        "Saves/mo",
        "Saves%",
    ]

    rows = [
        [
            r.sku_name,
            r.vm_count,
            f"${r.payg_compute_monthly:,.2f}",
            f"${r.rsv_compute_monthly:,.2f}",
            f"${r.savings_monthly:,.2f}",
            f"{r.savings_pct:.1f}%",
        ]
        for r in recs
    ]

    print("Reservation Recommendations:", file=stream)
    print(tabulate(rows, headers=headers, tablefmt="simple"), file=stream)

    # Footer totals
    total_payg = sum(r.payg_compute_monthly for r in recs)
    total_rsv = sum(r.rsv_compute_monthly for r in recs)
    total_saves = total_payg - total_rsv
    total_pct = (total_saves / total_payg * 100) if total_payg > 0 else 0.0

    print(file=stream)
    print(
        f"  Total compute savings: ${total_saves:,.2f}/mo  ({total_pct:.1f}% vs all-PAYG compute baseline)",
        file=stream,
    )
    print(
        f"  Disk costs (${total_disk_monthly:,.2f}/mo) are identical regardless of reservation.",
        file=stream,
    )
    print(
        "  Azure Instance Size Flexibility may extend reservation coverage across sizes within the same VM family.",
        file=stream,
    )


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_csv(
    results: list[VMResult],
    recommendations: list[ReservationRec],
    path: str,
    pricing_mode: str,
    reserved_term: str = "3-year",
) -> None:
    """Write per-VM rows and optional recommendation rows to a CSV file."""

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)

        # --- Per-VM section ---
        rsv_long = _rsv_label_long(reserved_term)
        writer.writerow([
            "VM Name",
            "vCPUs",
            "RAM (GB)",
            "Powerstate",
            "OS Type",
            "Azure SKU",
            "Disks",
            "PAYG Compute/mo",
            "PAYG Total/mo",
            f"{rsv_long} Compute/mo",
            f"{rsv_long} Total/mo",
            "Disk/mo",
            "Notes",
        ])

        for r in results:
            notes_parts: list[str] = []
            if not r.vm.is_powered_on:
                notes_parts.append("Powered off")
            if r.sku is None:
                notes_parts.append(
                    f"No match (>{_MAX_VCPUS} vCPU or >{_MAX_RAM_GB}GB RAM)"
                )
            else:
                notes_parts.extend(r.sku_notes)

            writer.writerow([
                r.vm.name,
                r.vm.vcpus,
                _fmt_ram(r.vm.ram_gb),
                r.vm.powerstate,
                r.vm.os_type,
                r.sku.name if r.sku else "",
                format_disk_summary(r.disk_tiers),
                f"{r.payg_compute_monthly:.2f}" if r.sku else "",
                f"{r.payg_total_monthly:.2f}" if r.sku else "",
                f"{r.reserved_compute_monthly:.2f}" if r.reserved_compute_monthly is not None else "",
                f"{r.reserved_total_monthly:.2f}" if r.reserved_total_monthly is not None else "",
                f"{r.disk_monthly:.2f}",
                "; ".join(notes_parts),
            ])

        # Blank separator
        writer.writerow([])

        # --- Recommendations section ---
        if recommendations:
            writer.writerow([
                "SKU",
                "VMs",
                "PAYG Compute/mo",
                f"{_rsv_label_long(reserved_term)} Compute/mo",
                "Saves/mo",
                "Saves%",
            ])
            for rec in recommendations:
                writer.writerow([
                    rec.sku_name,
                    rec.vm_count,
                    f"{rec.payg_compute_monthly:.2f}",
                    f"{rec.rsv_compute_monthly:.2f}",
                    f"{rec.savings_monthly:.2f}",
                    f"{rec.savings_pct:.1f}%",
                ])
            writer.writerow([])

        # --- Summary row ---
        powered_on = [r for r in results if r.vm.is_powered_on]
        total_payg = sum(r.payg_total_monthly for r in powered_on if r.sku)
        total_rsv = sum(r.reserved_total_monthly for r in powered_on if r.reserved_total_monthly is not None)
        writer.writerow([
            "TOTALS",
            "",
            "",
            "",
            "",
            "",
            "",
            f"{sum(r.payg_compute_monthly for r in powered_on if r.sku):.2f}",
            f"{total_payg:.2f}",
            f"{sum(r.reserved_compute_monthly for r in powered_on if r.reserved_compute_monthly is not None):.2f}",
            f"{total_rsv:.2f}",
            f"{sum(r.disk_monthly for r in powered_on):.2f}",
            "",
        ])
