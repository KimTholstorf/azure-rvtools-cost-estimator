"""
Regression tests for disk-to-tier mapping.

Guards the case where a disk exceeds the largest Azure managed-disk tier.
Previously find_disk_tier returned None and the caller skipped the disk, so an
oversized disk silently priced at zero — a 45 TiB disk on a real customer
export contributed nothing to the estimate.
"""
from __future__ import annotations

import pytest

from azure_rvtools.sku_mapper import _DISK_TIERS, split_disk_into_tiers

DISK_TYPES = ["premium-ssd", "standard-ssd", "standard-hdd"]


def _largest(disk_type: str):
    return _DISK_TIERS[disk_type][-1]


# --- normal-sized disks map to exactly one tier ---------------------------

@pytest.mark.parametrize("disk_type", DISK_TYPES)
def test_ordinary_disk_maps_to_single_tier(disk_type):
    tiers = split_disk_into_tiers(100.0, disk_type)
    assert len(tiers) == 1
    assert tiers[0].max_gb >= 100.0


@pytest.mark.parametrize("disk_type", DISK_TYPES)
def test_disk_exactly_at_largest_tier_is_not_split(disk_type):
    largest = _largest(disk_type)
    tiers = split_disk_into_tiers(float(largest.max_gb), disk_type)
    assert tiers == [largest]


# --- oversized disks are split, never dropped -----------------------------

@pytest.mark.parametrize("disk_type", DISK_TYPES)
def test_oversized_disk_is_never_silently_dropped(disk_type):
    """The original defect: capacity above the top tier priced at zero."""
    largest = _largest(disk_type)
    tiers = split_disk_into_tiers(largest.max_gb * 1.5, disk_type)
    assert tiers, "oversized disk produced no tiers and would price at $0"


@pytest.mark.parametrize("disk_type", DISK_TYPES)
def test_split_covers_full_capacity(disk_type):
    """Combined tier capacity must cover the whole disk, never less."""
    largest = _largest(disk_type)
    for multiplier in (1.01, 1.5, 2.0, 3.7, 8.0):
        capacity = largest.max_gb * multiplier
        tiers = split_disk_into_tiers(capacity, disk_type)
        covered = sum(t.max_gb for t in tiers)
        assert covered >= capacity, (
            f"{disk_type} {capacity:,.0f} GiB covered by only {covered:,.0f} GiB"
        )


@pytest.mark.parametrize("disk_type", DISK_TYPES)
def test_split_produces_equal_disks(disk_type):
    """Striping needs matching disk sizes, so every slice gets the same tier."""
    largest = _largest(disk_type)
    tiers = split_disk_into_tiers(largest.max_gb * 2.5, disk_type)
    assert len(set(t.tier for t in tiers)) == 1


def test_split_count_matches_real_customer_disk():
    """45.1 TiB disk from the customer export -> 2 equal P80s, not $0."""
    tiers = split_disk_into_tiers(46_174.0, "premium-ssd")
    assert [t.tier for t in tiers] == ["P80", "P80"]


def test_minimum_number_of_disks_used():
    """Splitting must not over-provision: 1.5x the top tier needs just 2 disks."""
    largest = _largest("premium-ssd")
    assert len(split_disk_into_tiers(largest.max_gb * 1.5, "premium-ssd")) == 2


# --- edge cases -----------------------------------------------------------

@pytest.mark.parametrize("capacity", [0.0, -1.0])
def test_non_positive_capacity_yields_no_tiers(capacity):
    assert split_disk_into_tiers(capacity, "premium-ssd") == []


def test_unknown_disk_type_raises():
    with pytest.raises(ValueError, match="Unknown disk type"):
        split_disk_into_tiers(100.0, "not-a-disk-type")
