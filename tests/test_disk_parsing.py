"""
Regression tests for RVTools disk parsing.

Guards two defects that shipped under-counted provisioned storage to a
customer (also found in the sibling oci-rvtools-cost-estimator):

1. Provisioned storage was read from "Total disk capacity MiB" (guest-visible)
   instead of "Provisioned MiB" (VMDK-allocated), under-counting by up to 2.5x.
   The tell-tale symptom is used > provisioned, which is physically impossible.
2. Per-disk vDisk records were used unscaled, so the guest-visible total leaked
   through even when "Provisioned MiB" was read correctly.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from azure_rvtools.rvtools import parse_rvtools

VINFO_HEADERS = [
    "VM", "Powerstate", "CPUs", "Memory",
    "Provisioned MiB", "In Use MiB", "Total disk capacity MiB",
]
VDISK_HEADERS = ["VM", "Capacity MiB"]


def _build(tmp_path: Path, vinfo_rows, vdisk_rows=None) -> Path:
    """Write a minimal RVTools workbook and return its path."""
    wb = Workbook()
    ws = wb.active
    ws.title = "vInfo"
    ws.append(VINFO_HEADERS)
    for row in vinfo_rows:
        ws.append(row)

    if vdisk_rows is not None:
        wd = wb.create_sheet("vDisk")
        wd.append(VDISK_HEADERS)
        for row in vdisk_rows:
            wd.append(row)

    path = tmp_path / "rvtools.xlsx"
    wb.save(path)
    return path


def _totals(path: Path) -> tuple[float, float]:
    """Return (provisioned_gib, used_gib) as the tool computes them."""
    vms = [v for v in parse_rvtools(path, include_powered_off=True) if v.is_powered_on]
    provisioned = sum(d.capacity_gb for v in vms for d in v.effective_disks)
    used = sum(d.capacity_gb for v in vms for d in v.in_use_disks)
    return provisioned, used


# --- (i) used must never exceed provisioned -------------------------------

def test_used_never_exceeds_provisioned_with_vdisk(tmp_path):
    """
    Mirrors the real customer export: In Use (900 GiB) sits between the
    guest-visible capacity (600 GiB) and the provisioned size (1500 GiB).

    Reading the guest-visible column made used > provisioned.
    """
    vinfo = [["vm1", "poweredOn", 4, 8192, 1_536_000, 921_600, 614_400]]
    vdisk = [["vm1", 409_600], ["vm1", 204_800]]  # 400 + 200 GiB guest-visible

    provisioned, used = _totals(_build(tmp_path, vinfo, vdisk))

    assert used <= provisioned, (
        f"used ({used:,.1f} GiB) exceeds provisioned ({provisioned:,.1f} GiB)"
    )
    assert provisioned == pytest.approx(1500.0)
    assert used == pytest.approx(900.0)


def test_used_never_exceeds_provisioned_without_vdisk(tmp_path):
    """Same invariant on the vInfo-only fallback path (no vDisk sheet)."""
    vinfo = [["vm1", "poweredOn", 4, 8192, 1_536_000, 921_600, 614_400]]

    provisioned, used = _totals(_build(tmp_path, vinfo, vdisk_rows=None))

    assert used <= provisioned
    assert provisioned == pytest.approx(1500.0)


# --- (ii) smaller guest capacity must not override larger provisioned -----

def test_total_disk_capacity_does_not_override_provisioned(tmp_path):
    """A smaller 'Total disk capacity MiB' must not win over 'Provisioned MiB'."""
    vinfo = [["vm1", "poweredOn", 2, 4096, 1_048_576, 0, 102_400]]  # 1024 vs 100 GiB

    provisioned, _ = _totals(_build(tmp_path, vinfo, vdisk_rows=None))

    assert provisioned == pytest.approx(1024.0), (
        "guest-visible capacity overrode the larger provisioned figure"
    )


def test_vdisk_records_scaled_to_provisioned_total(tmp_path):
    """
    vDisk capacities are guest-visible and must be scaled up to the provisioned
    total, while preserving disk count and relative sizes for Azure tier pricing.
    """
    vinfo = [["vm1", "poweredOn", 2, 4096, 1_048_576, 0, 262_144]]
    vdisk = [["vm1", 196_608], ["vm1", 65_536]]  # 192 + 64 = 256 GiB guest → 4x scale

    vms = [v for v in parse_rvtools(_build(tmp_path, vinfo, vdisk),
                                    include_powered_off=True) if v.is_powered_on]
    disks = sorted(d.capacity_gb for d in vms[0].effective_disks)

    assert len(disks) == 2, "per-disk breakdown must be preserved for tier pricing"
    assert sum(disks) == pytest.approx(1024.0)
    assert disks == pytest.approx([256.0, 768.0])  # 3:1 ratio preserved


# --- fallback behaviour ---------------------------------------------------

def test_falls_back_to_total_capacity_when_provisioned_missing(tmp_path):
    """Per-row fallback: blank/zero Provisioned falls back to guest capacity."""
    vinfo = [
        ["vm_zero",  "poweredOn", 2, 4096, 0,       0, 102_400],
        ["vm_blank", "poweredOn", 2, 4096, None,    0, 204_800],
    ]

    provisioned, _ = _totals(_build(tmp_path, vinfo, vdisk_rows=None))

    assert provisioned == pytest.approx(300.0)  # 100 + 200 GiB


def test_blank_capacities_do_not_produce_nan(tmp_path):
    """Missing disk cells must yield 0.0, never NaN leaking into totals."""
    vinfo = [["vm1", "poweredOn", 2, 4096, None, None, None]]
    vdisk = [["vm1", None]]

    provisioned, used = _totals(_build(tmp_path, vinfo, vdisk))

    assert provisioned == provisioned, "provisioned is NaN"
    assert used == used, "used is NaN"
    assert provisioned == 0.0
