from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Token-based column matching helpers
# ---------------------------------------------------------------------------

def _tok(s: str) -> str:
    """Normalise a column header to a lowercase alphanumeric token."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


# Cluster values that indicate a missing/invalid assignment (case-insensitive match)
_INVALID_CLUSTER_VALUES: frozenset[str] = frozenset({"", "none", "nan", "unknown", "n/a"})


# Canonical column definitions: canonical_name → list of candidate strings
_VINFO_COLS: dict[str, list[str]] = {
    "VM":         ["VM", "Name", "VM Name", "Virtual Machine", "vInfoVM"],
    "Powerstate": ["Powerstate", "Power State", "Power", "vInfoPowerstate"],
    "CPUs":       ["CPUs", "CPU", "vCPU", "vCPUs", "Num CPUs", "Number of CPUs", "vInfoCPUs"],
    "Memory":     ["Memory", "Memory MB", "RAM MB", "Memory (MB)", "RAM (MB)", "vInfoMemory"],
    "OS": [
        "OS according to the config file",
        "OS Config",
        "Guest OS",
        "OS",
        "vInfoOS",
    ],
    "Datacenter": ["Datacenter", "Data Center", "DC", "vInfoDatacenter"],
    "Cluster":    ["Cluster", "Cluster Name", "vInfoCluster"],
    "Provisioned MiB": ["Provisioned MiB", "Provisioned", "vInfoProvisioned"],
    "In Use MiB":      ["In Use MiB", "In Use", "vInfoInUse"],
    "Total disk capacity MiB": [
        "Total disk capacity MiB",
        "Total Disk Capacity MiB",
        "vInfoTotalDiskCapacityMiB",
    ],
}

_VDISK_COLS: dict[str, list[str]] = {
    "VM": ["VM", "Name", "VM Name"],
    "Capacity MiB": ["Capacity MiB", "Capacity", "Size MiB", "Disk Capacity MiB"],
}


def _build_token_map(candidates: list[str]) -> set[str]:
    return {_tok(c) for c in candidates}


def _match_column(df_cols: list[str], candidates: list[str]) -> str | None:
    """Return the first df column whose token matches any candidate token."""
    candidate_tokens = _build_token_map(candidates)
    for col in df_cols:
        if _tok(col) in candidate_tokens:
            return col
    return None


def _resolve_columns(
    df: pd.DataFrame,
    canon_map: dict[str, list[str]],
    required: list[str],
    sheet_name: str,
) -> dict[str, str]:
    """
    Resolve canonical column names to actual DataFrame column names.
    Raises ValueError for any required column that cannot be found.
    Returns dict: canonical_name → actual_col_name (only for found columns).
    """
    df_cols = list(df.columns)
    resolved: dict[str, str] = {}
    missing_required: list[str] = []

    for canon, candidates in canon_map.items():
        actual = _match_column(df_cols, candidates)
        if actual is not None:
            resolved[canon] = actual
        elif canon in required:
            missing_required.append(canon)

    if missing_required:
        raise ValueError(
            f"Sheet '{sheet_name}': required columns not found: {missing_required}. "
            f"Available columns: {df_cols}"
        )
    return resolved


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DiskRecord:
    capacity_gb: float


@dataclass
class VMRecord:
    name: str
    vcpus: int           # always >= 1
    ram_gb: float
    powerstate: str      # raw string from RVTools
    os_raw: str          # raw string from RVTools
    os_type: str         # "linux" or "windows"
    datacenter: str = ""
    cluster: str = ""
    disks: list[DiskRecord] = field(default_factory=list)
    provisioned_gb: float = 0.0

    @property
    def is_powered_on(self) -> bool:
        return "on" in self.powerstate.lower()

    @property
    def effective_disks(self) -> list[DiskRecord]:
        """
        Return per-disk records from vDisk if available,
        otherwise fall back to a single aggregate disk from vInfo provisioned_gb.
        """
        if self.disks:
            return self.disks
        if self.provisioned_gb > 0:
            return [DiskRecord(self.provisioned_gb)]
        return []


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------

def _detect_os(os_raw: str) -> str:
    if "windows" in os_raw.lower():
        return "windows"
    return "linux"


# ---------------------------------------------------------------------------
# Sheet finder
# ---------------------------------------------------------------------------

def _find_sheet(all_sheets: list[str], target_token: str) -> str | None:
    """Return the first sheet name whose token equals target_token."""
    for name in all_sheets:
        if _tok(name) == target_token:
            return name
    return None


# ---------------------------------------------------------------------------
# vDisk parser
# ---------------------------------------------------------------------------

def _parse_vdisk(xls: pd.ExcelFile, sheet_name: str) -> dict[str, list[DiskRecord]]:
    """
    Parse the vDisk sheet and return a dict mapping VM name → list[DiskRecord].
    Capacity in MiB → GB (÷1024).
    """
    df = xls.parse(sheet_name, header=0)
    df.columns = [str(c) for c in df.columns]

    required = ["VM", "Capacity MiB"]
    try:
        resolved = _resolve_columns(df, _VDISK_COLS, required, sheet_name)
    except ValueError:
        # vDisk may have no data or unexpected format — return empty
        return {}

    vm_col = resolved["VM"]
    cap_col = resolved["Capacity MiB"]

    result: dict[str, list[DiskRecord]] = {}
    for _, row in df.iterrows():
        vm_name = str(row[vm_col]).strip()
        if not vm_name or vm_name.lower() == "nan":
            continue
        try:
            cap_mib = float(row[cap_col])
        except (ValueError, TypeError):
            cap_mib = 0.0
        cap_gb = cap_mib / 1024.0
        result.setdefault(vm_name, []).append(DiskRecord(cap_gb))

    return result


# ---------------------------------------------------------------------------
# vInfo parser
# ---------------------------------------------------------------------------

def _parse_vinfo(
    xls: pd.ExcelFile,
    sheet_name: str,
    vdisk_map: dict[str, list[DiskRecord]],
    include_powered_off: bool,
) -> list[VMRecord]:
    df = xls.parse(sheet_name, header=0)
    df.columns = [str(c) for c in df.columns]

    required = ["VM", "Powerstate", "CPUs", "Memory"]
    resolved = _resolve_columns(df, _VINFO_COLS, required, sheet_name)

    vm_col  = resolved["VM"]
    ps_col  = resolved["Powerstate"]
    cpu_col = resolved["CPUs"]
    mem_col = resolved["Memory"]
    os_col  = resolved.get("OS")          # optional — defaults to linux if absent
    dc_col  = resolved.get("Datacenter")  # optional
    cl_col  = resolved.get("Cluster")     # optional

    # Optional disk columns — prefer total capacity over provisioned
    prov_col = resolved.get("Total disk capacity MiB") or resolved.get("Provisioned MiB")

    records: list[VMRecord] = []

    for _, row in df.iterrows():
        vm_name = str(row[vm_col]).strip()

        # Skip empty rows
        if not vm_name or vm_name.lower() == "nan":
            continue

        # Filter out vCLS housekeeping VMs
        if vm_name.lower().startswith("vcls-"):
            continue

        powerstate = str(row[ps_col]).strip() if pd.notna(row[ps_col]) else "unknown"

        # Filter powered-off unless flag set
        if not include_powered_off and "on" not in powerstate.lower():
            continue

        # vCPUs — must be >= 1
        try:
            vcpus = max(1, int(float(str(row[cpu_col]))))
        except (ValueError, TypeError):
            vcpus = 1

        # Memory in MB → GB
        try:
            ram_gb = float(str(row[mem_col])) / 1024.0
            if ram_gb != ram_gb:  # NaN check
                ram_gb = 0.0
        except (ValueError, TypeError):
            ram_gb = 0.0

        # OS — use column if found, else default to linux
        os_raw = (
            str(row[os_col]).strip()
            if os_col and pd.notna(row.get(os_col))
            else ""
        )
        os_type = _detect_os(os_raw)

        # Datacenter / Cluster
        datacenter = (
            str(row[dc_col]).strip()
            if dc_col and pd.notna(row.get(dc_col))
            else ""
        )
        cluster = (
            str(row[cl_col]).strip()
            if cl_col and pd.notna(row.get(cl_col))
            else ""
        )
        if datacenter.lower() in _INVALID_CLUSTER_VALUES:
            datacenter = ""
        if cluster.lower() in _INVALID_CLUSTER_VALUES:
            cluster = ""

        # Provisioned disk in MiB → GB (fallback)
        provisioned_gb = 0.0
        if prov_col and pd.notna(row.get(prov_col)):
            try:
                provisioned_gb = float(str(row[prov_col])) / 1024.0
            except (ValueError, TypeError):
                provisioned_gb = 0.0

        # Per-disk records from vDisk sheet
        disks = vdisk_map.get(vm_name, [])

        records.append(
            VMRecord(
                name=vm_name,
                vcpus=vcpus,
                ram_gb=ram_gb,
                powerstate=powerstate,
                os_raw=os_raw,
                os_type=os_type,
                datacenter=datacenter,
                cluster=cluster,
                disks=disks,
                provisioned_gb=provisioned_gb,
            )
        )

    return records


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_rvtools(
    path: str | Path,
    include_powered_off: bool = False,
) -> list[VMRecord]:
    """
    Parse an RVTools XLSX export and return a list of VMRecord objects.

    Parameters
    ----------
    path:
        Path to the RVTools .xlsx file.
    include_powered_off:
        When True, include VMs that are not powered on.

    Raises
    ------
    ValueError
        If the vInfo sheet is not found or required columns are missing.
    """
    path = Path(path)

    xls = pd.ExcelFile(path, engine="openpyxl")
    all_sheets: list[str] = xls.sheet_names  # type: ignore[assignment]

    # Locate vInfo sheet
    vinfo_sheet = _find_sheet(all_sheets, "vinfo")
    if vinfo_sheet is None:
        raise ValueError(
            f"vInfo sheet not found in '{path}'. "
            f"Available sheets: {all_sheets}"
        )

    # Locate vDisk sheet (optional)
    vdisk_sheet = _find_sheet(all_sheets, "vdisk")
    vdisk_map: dict[str, list[DiskRecord]] = {}
    if vdisk_sheet is not None:
        vdisk_map = _parse_vdisk(xls, vdisk_sheet)

    return _parse_vinfo(xls, vinfo_sheet, vdisk_map, include_powered_off)


# ---------------------------------------------------------------------------
# Topology listing and filtering (--list / --datacenter / --cluster)
# ---------------------------------------------------------------------------

def list_topology(path: str | Path) -> None:
    """
    Print all Datacenter → Cluster pairs found in an RVTools file and exit.
    Matches the behaviour of --list in oci-rvtools.
    """
    path = Path(path)
    xls = pd.ExcelFile(path, engine="openpyxl")
    vinfo_sheet = _find_sheet(xls.sheet_names, "vinfo")
    if vinfo_sheet is None:
        print(f"[WARN] No vInfo sheet found in '{path.name}'.")
        return

    df = xls.parse(vinfo_sheet, header=0)
    df.columns = [str(c) for c in df.columns]
    resolved = _resolve_columns(df, _VINFO_COLS, required=[], sheet_name=vinfo_sheet)

    dc_col = resolved.get("Datacenter")
    cl_col = resolved.get("Cluster")

    if not dc_col and not cl_col:
        print("[WARN] No Datacenter or Cluster columns found in vInfo sheet.")
        return

    topology: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        dc = str(row[dc_col]).strip() if dc_col else ""
        cl = str(row[cl_col]).strip() if cl_col else ""
        if dc.lower() in _INVALID_CLUSTER_VALUES:
            dc = ""
        if cl.lower() in _INVALID_CLUSTER_VALUES:
            cl = ""
        dc_label = dc or "(no datacenter)"
        topology.setdefault(dc_label, set()).add(cl or "(no cluster)")

    if not topology:
        print("[WARN] No Datacenter/Cluster data found.")
        return

    print(f"Topology in '{path.name}':")
    for dc in sorted(topology):
        print(f"  Datacenter: {dc}")
        for cl in sorted(topology[dc]):
            print(f"    Cluster: {cl}")


def filter_vms(
    vms: list[VMRecord],
    datacenters: list[str] | None,
    clusters: list[str] | None,
) -> list[VMRecord]:
    """
    Filter a list of VMRecords by Datacenter and/or Cluster name.

    - Matching is case-insensitive.
    - Multiple values within --datacenter or --cluster use OR logic.
    - --datacenter and --cluster together use AND logic (VM must satisfy both).
    - Warns if a requested name matches nothing.
    """
    import sys

    if not datacenters and not clusters:
        return vms

    filtered = vms

    if datacenters:
        dc_lower = [d.lower() for d in datacenters]
        matched = {vm.datacenter.lower() for vm in filtered} & set(dc_lower)
        for d in dc_lower:
            if d not in matched:
                print(
                    f"[WARN] No VMs found for Datacenter '{d}' — check spelling with --list.",
                    file=sys.stderr,
                )
        filtered = [vm for vm in filtered if vm.datacenter.lower() in dc_lower]

    if clusters:
        cl_lower = [c.lower() for c in clusters]
        matched = {vm.cluster.lower() for vm in filtered} & set(cl_lower)
        for c in cl_lower:
            if c not in matched:
                print(
                    f"[WARN] No VMs found for Cluster '{c}' — check spelling with --list.",
                    file=sys.stderr,
                )
        filtered = [vm for vm in filtered if vm.cluster.lower() in cl_lower]

    return filtered
