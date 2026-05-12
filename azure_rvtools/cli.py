from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .excel import write_workbook
from .output import (
    ReservationRec,
    VMResult,
    print_recommendations,
    print_summary,
    write_csv,
)
from .pricing import PricingClient
from .rvtools import VMRecord, filter_vms, list_topology, parse_rvtools
from .sku_mapper import ALL_VM_SKUS, find_disk_tier, find_vm_sku

VERSION = "1.0.1"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="azure-rvtools",
        description="Convert RVTools Excel exports into an Azure IaaS cost estimate.",
    )

    parser.add_argument(
        "--rvtools",
        metavar="PATH",
        required=True,
        help="RVTools XLSX export file",
    )
    parser.add_argument(
        "--region",
        metavar="REGION",
        default=None,
        help="Azure region slug (e.g. westeurope, eastus). Required unless --list is used.",
    )

    parser.add_argument(
        "--pricing",
        choices=["realistic", "all-payg", "all-reserved"],
        default="realistic",
        help="Pricing mode (default: realistic)",
    )
    parser.add_argument(
        "--realistic-top",
        type=int,
        default=3,
        metavar="N",
        dest="realistic_top",
        help=(
            "Number of top SKUs by VM count to price as reserved in realistic mode (default: 3). "
            "All other SKUs are priced as PAYG."
        ),
    )
    parser.add_argument(
        "--disk-type",
        choices=["premium-ssd", "standard-ssd", "standard-hdd"],
        default="premium-ssd",
        help="Managed disk type (default: premium-ssd)",
    )
    parser.add_argument(
        "--include-powered-off",
        action="store_true",
        help="Include VMs that are not powered on",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print all Datacenters and Clusters in the file and exit",
    )
    parser.add_argument(
        "--datacenter",
        metavar="NAME",
        nargs="+",
        help="Only include VMs from these Datacenter(s) (case-insensitive, repeatable)",
    )
    parser.add_argument(
        "--cluster",
        metavar="NAME",
        nargs="+",
        help="Only include VMs from these Cluster(s) (case-insensitive, repeatable)",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default="azure_estimate.xlsx",
        help="Excel workbook output path (default: azure_estimate.xlsx)",
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="Also write a CSV to this path",
    )
    parser.add_argument(
        "--reserved-term",
        choices=["1-year", "3-year"],
        default="3-year",
        help="Reservation term for optimized / all-reserved pricing (default: 3-year)",
    )
    parser.add_argument(
        "--support",
        choices=["basic", "developer", "standard", "professional-direct"],
        default="basic",
        metavar="PLAN",
        help=(
            "Azure support plan to include in the estimate: "
            "basic (free, default), developer ($29/mo), "
            "standard ($100/mo), professional-direct ($1000/mo)"
        ),
    )
    parser.add_argument(
        "--os-license-included",
        action="store_true",
        dest="os_license_included",
        help=(
            "Use OS-included (Windows) pricing for Windows VMs. "
            "By default Azure Hybrid Benefit is assumed (Linux compute rate)."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local pricing cache",
    )
    parser.add_argument(
        "--currency",
        metavar="CODE",
        default="USD",
        help="Currency code (default: USD)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=VERSION,
    )

    args = parser.parse_args(argv)

    # --region is required for everything except --list
    if not args.list and not args.region:
        parser.error(
            "--region is required (e.g. --region westeurope). "
            "Use --list to inspect the file without specifying a region."
        )

    return args


# ---------------------------------------------------------------------------
# Recommendation builder
# ---------------------------------------------------------------------------

def build_recommendations(results: list[VMResult]) -> list[ReservationRec]:
    """
    Group VMResults by SKU name and build reservation recommendations.
    Only includes VMs where both sku and reserved_compute_monthly are not None.
    Sorted by savings_monthly descending.
    """
    groups: dict[str, list[VMResult]] = {}
    for r in results:
        if r.sku is None or r.reserved_compute_monthly is None:
            continue
        groups.setdefault(r.sku.name, []).append(r)

    recs: list[ReservationRec] = []
    for sku_name, group in groups.items():
        payg_total = sum(r.payg_compute_monthly for r in group)
        rsv_total = sum(r.reserved_compute_monthly for r in group)  # type: ignore[misc]
        recs.append(
            ReservationRec(
                sku_name=sku_name,
                vm_count=len(group),
                payg_compute_monthly=payg_total,
                rsv_compute_monthly=rsv_total,
            )
        )

    recs.sort(key=lambda rec: rec.savings_monthly, reverse=True)
    return recs


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)

        # --- Validate input file ---
        rvtools_path = Path(args.rvtools)
        if not rvtools_path.exists():
            print(f"[ERROR] File not found: {rvtools_path}", file=sys.stderr)
            raise SystemExit(1)

        # --- --list: print topology and exit (no --region needed) ---
        if args.list:
            list_topology(rvtools_path)
            return 0

        # --- Parse RVTools (always include powered-off so we can count them) ---
        print(f"[INFO] Reading {args.rvtools}...", file=sys.stderr)
        try:
            all_vms: list[VMRecord] = parse_rvtools(rvtools_path, include_powered_off=True)
        except ValueError as exc:
            print(f"[ERROR] Failed to parse RVTools file: {exc}", file=sys.stderr)
            raise SystemExit(1)

        # --- Apply datacenter / cluster filters ---
        if args.datacenter or args.cluster:
            before = len(all_vms)
            all_vms = filter_vms(all_vms, args.datacenter, args.cluster)
            after = len(all_vms)
            filters = []
            if args.datacenter:
                filters.append(f"Datacenter={', '.join(args.datacenter)}")
            if args.cluster:
                filters.append(f"Cluster={', '.join(args.cluster)}")
            print(
                f"[INFO] Filter ({' | '.join(filters)}): {before} → {after} VM(s).",
                file=sys.stderr,
            )

        if not all_vms:
            print(
                "[WARN] No VMs remain after filtering (use --list to see available names).",
                file=sys.stderr,
            )
            return 0

        powered_on_count  = sum(1 for v in all_vms if v.is_powered_on)
        powered_off_count = sum(1 for v in all_vms if not v.is_powered_on)
        print(
            f"[INFO] Found {len(all_vms)} VM(s) "
            f"({powered_on_count} powered-on, {powered_off_count} powered-off).",
            file=sys.stderr,
        )

        # Drop powered-off VMs from cost processing unless explicitly included
        vms = all_vms if args.include_powered_off else [v for v in all_vms if v.is_powered_on]

        # --- Pricing client ---
        client = PricingClient(
            region=args.region,
            currency=args.currency,
            no_cache=args.no_cache,
        )
        client.ensure_loaded(args.disk_type)

        # --- Build results ---
        results: list[VMResult] = []

        for vm in vms:
            # SKU matching
            sku_match = find_vm_sku(vm.vcpus, vm.ram_gb)

            # Disk tier mapping
            disk_tier_counts: dict[str, int] = {}
            disk_monthly = 0.0

            for disk in vm.effective_disks:
                tier = find_disk_tier(disk.capacity_gb, args.disk_type)
                if tier is not None:
                    disk_tier_counts[tier.tier] = disk_tier_counts.get(tier.tier, 0) + 1
                    disk_monthly += client.get_disk_price(tier.tier, args.disk_type)

            # Sort disk tiers by numeric size (P2 < P10, not alphabetically)
            def _tier_num(name: str) -> int:
                m = re.search(r"\d+", name)
                return int(m.group()) if m else 0

            disk_tiers_sorted = sorted(disk_tier_counts.items(), key=lambda x: _tier_num(x[0]))

            # VM pricing
            payg_compute_monthly = 0.0
            reserved_compute_monthly: float | None = None

            if sku_match is not None and vm.is_powered_on:
                vm_prices = client.get_vm_prices(
                    sku_match.sku.name,
                    vm.os_type,
                    hybrid_benefit=not args.os_license_included,
                )
                payg_compute_monthly = vm_prices.payg_monthly
                if args.reserved_term == "3-year":
                    reserved_compute_monthly = vm_prices.reserved_3yr_monthly
                else:
                    reserved_compute_monthly = vm_prices.reserved_1yr_monthly

            results.append(
                VMResult(
                    vm=vm,
                    sku=sku_match.sku if sku_match else None,
                    sku_notes=sku_match.notes if sku_match else [],
                    disk_tiers=disk_tiers_sorted,
                    payg_compute_monthly=payg_compute_monthly,
                    reserved_compute_monthly=reserved_compute_monthly,
                    disk_monthly=disk_monthly,
                )
            )

        # --- Build recommendations (always — Reservations sheet is always populated) ---
        recommendations: list[ReservationRec] = build_recommendations(results)

        # --- Realistic mode: identify top N SKUs by VM count to price as reserved ---
        realistic_top_skus: frozenset[str] = frozenset()
        if args.pricing == "realistic" and recommendations:
            sorted_by_count = sorted(recommendations, key=lambda r: r.vm_count, reverse=True)
            realistic_top_skus = frozenset(
                r.sku_name for r in sorted_by_count[:args.realistic_top]
            )
            print(
                f"[INFO] Realistic mode: reserving top {args.realistic_top} SKU(s) by VM count: "
                f"{', '.join(sorted(realistic_top_skus))}",
                file=sys.stderr,
            )

        # --- Output ---
        total_disk_monthly = sum(r.disk_monthly for r in results if r.vm.is_powered_on)

        print_summary(
            results=results,
            recommendations=recommendations,
            pricing_mode=args.pricing,
            currency=args.currency,
            region=args.region,
            disk_type=args.disk_type,
            reserved_term=args.reserved_term,
            hybrid_benefit=not args.os_license_included,
            support_plan=args.support,
            realistic_top_skus=realistic_top_skus,
        )

        if args.pricing == "realistic" and recommendations:
            print("")
            print_recommendations(recommendations, args.currency, total_disk_monthly,
                                  args.reserved_term)

        # --- Excel workbook ---
        print(f"[INFO] Writing Excel workbook to {args.output}...", file=sys.stderr)
        write_workbook(
            results=results,
            recommendations=recommendations,
            output_path=args.output,
            region=args.region,
            currency=args.currency,
            pricing_mode=args.pricing,
            disk_type=args.disk_type,
            rvtools_filename=Path(args.rvtools).name,
            reserved_term=args.reserved_term,
            hybrid_benefit=not args.os_license_included,
            support_plan=args.support,
            realistic_top_skus=realistic_top_skus,
        )
        print(f"[INFO] Workbook saved: {args.output}", file=sys.stderr)

        # --- CSV output ---
        if args.csv:
            write_csv(results, recommendations, args.csv, args.pricing, args.reserved_term)
            print(f"[INFO] CSV written to {args.csv}", file=sys.stderr)

        return 0

    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 1
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
