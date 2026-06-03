<div align="center">
  <img src="images/logo_gh.png" width="345" height="93" alt="Logo"/>
  <h4 align="center">Turn VMware RVTools exports into an Azure IaaS monthly cost estimate</h4>
</div>

<div align="center">
  <a href="https://kimtholstorf.github.io/azure-rvtools-cost-estimator" target="_blank" rel="noopener">
    <img alt="Web App" src="https://img.shields.io/badge/web_app-GitHub_Pages-brightgreen">
  </a>
  <a href="https://pypi.org/project/azure-rvtools/" target="_blank" rel="noopener">
    <img alt="GitHub Actions PyPi Status" src="https://img.shields.io/github/actions/workflow/status/KimTholstorf/azure-rvtools-cost-estimator/pypi-publish.yml?label=pypi&cacheSeconds=0">
  </a>
  <a href="https://pypi.org/project/azure-rvtools/" target="_blank" rel="noopener">
    <img alt="PyPI version" src="https://img.shields.io/pypi/v/azure-rvtools">
  </a>
</div>

<br>

This utility ingests one or more RVTools `vInfo` sheets, pulls the latest Azure Retail Prices, and generates an Excel workbook with per-VM cost estimates — priced individually against the closest matching Azure SKU.

Unlike aggregate-style estimators, `azure-rvtools` maps each VM to a specific Azure VM SKU (Dsv5 or Esv5 series), prices it individually, and rolls everything up into a workbook that mirrors the format of the official Azure Calculator export.

---

## 🚀 Features

- **Browser-based web app** – no install required. Drop in your RVTools export on [this website](https://kimtholstorf.github.io/azure-rvtools-cost-estimator), pick a region and currency, and download the estimate instantly. Runs 100% browser-local via WebAssembly — nothing is uploaded, nothing leaves your device.
- **Per-VM SKU matching** – maps each VM to the closest Azure Dsv5 or Esv5 SKU based on vCPU and RAM, with notes when an exact match isn't found.
- **Realistic pricing mode** – the default `--pricing realistic` reserves the top N SKUs by VM count (configurable via `--realistic-top`) and prices the remainder at PAYG — a practical reflection of how customers actually buy reservations.
- **1-year and 3-year reservations** – choose your commitment term with `--reserved-term` (default: `3-year`).
- **Azure Hybrid Benefit** – enabled by default, applying Linux compute rates to Windows VMs. Disable with `--os-license-included` to use Windows-included pricing.
- **Multi-currency support** – USD, EUR, GBP, DKK, SEK, NOK via `--currency`.
- **Disk capacity source** – price managed disks based on provisioned capacity (default) or actual in-use capacity via `--disk-source`.
- **Support plan pricing** – add an Azure support plan cost to the estimate with `--support` (`basic`, `developer`, `standard`, `professional-direct`).
- **Datacenter and Cluster filtering** – list all Datacenters and Clusters in the input, then scope the estimate to a specific subset.
- **Powered-off VM handling** – counted and reported separately; excluded from costs unless `--include-powered-off` is set.
- **Live pricing lookup** – fetches list prices from the [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) at runtime, with a local cache to speed up repeat runs.
- **Polished Excel output** – writes an `.xlsx` workbook with four sheets (Estimate, VM Detail, Reservations, Summary) designed to mirror the official Azure Calculator export format.
- **CSV export** – optionally write a machine-readable CSV alongside the Excel workbook.
- **Direct RVTools ingestion** – reads raw RVTools `.xlsx` exports and automatically ignores housekeeping VMs (`vCLS-*`).

---

## ⚡ Quick start

### Online (no install)

Visit **[kimtholstorf.github.io/azure-rvtools-cost-estimator](https://kimtholstorf.github.io/azure-rvtools-cost-estimator)** — drop in your RVTools `.xlsx`, choose your region, currency, and options, and download the estimate. Everything runs in your browser via WebAssembly.

### CLI

```bash
# Install from PyPI
pip install azure-rvtools

# Run the estimator
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope
```

The tool contacts the Azure Retail Prices API at runtime. Ensure the machine has outbound internet access.

---

## 🏗️ Installation options

### PyPI, pipx or uv

```bash
# pip — installs into your active environment
pip install azure-rvtools

# pipx — isolated install, command available system-wide
pipx install azure-rvtools

# uv tool — one-off run without a permanent install
uvx azure-rvtools --rvtools ./customer/RVTools_export_all.xlsx --region westeurope

# uv run — from within the repo
uv run azure-rvtools --rvtools ./customer/RVTools_export_all.xlsx --region westeurope
```

### From source

```bash
git clone https://github.com/KimTholstorf/azure-rvtools-cost-estimator.git
cd azure-rvtools-cost-estimator
python3 -m venv .venv
source .venv/bin/activate
pip install .

azure-rvtools --rvtools ./customer/RVTools_export_all.xlsx --region westeurope
```

---

## 📥 Input expectations

- RVTools workbook(s) in `.xlsx` format containing the `vInfo` sheet (default `RVTools_export_all.xlsx`).
- The `vDisk` sheet is used for per-disk storage pricing if present; otherwise falls back to the provisioned disk total from `vInfo`.
- Powered-off VMs are counted and reported but excluded from cost calculations by default.

---

## 📤 Output workbook

The generated Excel workbook (default: `azure_estimate.xlsx`) contains four sheets:

1. **Estimate** – Azure Calculator-style summary with per-SKU rows, disk, support, and a total. Reflects the chosen `--pricing` mode.
2. **VM Detail** – Per-VM breakdown with SKU match, PAYG and reserved monthly costs, disk tiers, OS type, and any match notes.
3. **Reservations** – SKU-level reservation recommendations showing PAYG vs. reserved compute cost and monthly savings. Always populated regardless of pricing mode.
4. **Summary** – Aggregate statistics: VM counts, vCPU/RAM totals, effective monthly cost, and savings vs. PAYG.

---

## 🛠️ CLI reference

| Argument | Description |
| --- | --- |
| `--rvtools PATH` | RVTools `.xlsx` export file. Required. |
| `--region REGION` | Azure region slug (e.g. `westeurope`, `eastus`). Required unless `--list` is used. |
| `--pricing MODE` | Pricing mode: `realistic` (default), `all-payg`, or `all-reserved`. |
| `--realistic-top N` | Number of top SKUs by VM count to price as reserved in realistic mode. Default: `3`. |
| `--reserved-term TERM` | Reservation commitment term: `1-year` or `3-year` (default). |
| `--disk-type TYPE` | Managed disk type: `premium-ssd` (default), `standard-ssd`, or `standard-hdd`. |
| `--disk-source SOURCE` | Disk capacity source: `provisioned` (default) or `in-use`. |
| `--currency CODE` | Currency for pricing and output: `USD` (default), `EUR`, `GBP`, `DKK`, `SEK`, `NOK`. |
| `--os-license-included` | Use Windows-included pricing for Windows VMs. Disables Azure Hybrid Benefit (on by default). |
| `--support PLAN` | Azure support plan: `basic` (free, default), `developer`, `standard`, `professional-direct`. |
| `--include-powered-off` | Include powered-off VMs in cost calculations. |
| `--list` | Print all Datacenters and Clusters found in the input file and exit. |
| `--datacenter NAME [NAME ...]` | Only include VMs from the given Datacenter(s). |
| `--cluster NAME [NAME ...]` | Only include VMs from the given Cluster(s). |
| `--output PATH` | Excel workbook output path. Default: `azure_estimate.xlsx`. |
| `--csv PATH` | Also write a CSV to this path. |
| `--no-cache` | Bypass the local pricing cache and fetch fresh prices. |
| `--version` | Print the version and exit. |

When both `--datacenter` and `--cluster` are specified, a VM must match both (AND logic). Multiple values within each flag are matched with OR logic.

---

## 📈 Examples

```bash
# Baseline run — realistic pricing, 3-year reserved top 3 SKUs, Hybrid Benefit on
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope

# List Datacenters and Clusters in the input file
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --list

# Filter to a specific datacenter and cluster
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --datacenter "DC-West" --cluster "Prod-Cluster-01"

# All-PAYG pricing (no reservations)
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --pricing all-payg

# All-reserved pricing, 1-year term
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --pricing all-reserved \
  --reserved-term 1-year

# Realistic mode reserving top 5 SKUs
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --realistic-top 5

# Estimate in Euros using in-use disk capacity
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --currency EUR \
  --disk-source in-use

# Windows VMs with OS license included (no Hybrid Benefit) and Standard support
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --os-license-included \
  --support standard

# Include powered-off VMs and write a CSV alongside the Excel
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --include-powered-off \
  --csv ./customer/azure_estimate.csv \
  --output ./customer/azure_estimate.xlsx

# Force fresh pricing (bypass cache)
azure-rvtools \
  --rvtools ./customer/RVTools_export_all.xlsx \
  --region westeurope \
  --no-cache
```

---

## 💡 Pricing modes explained

| Mode | What gets reserved | Best for |
| --- | --- | --- |
| `realistic` (default) | Top N SKUs by VM count | Realistic customer scenario — commit only on your dominant shapes |
| `all-reserved` | Every VM | Best-case committed cost |
| `all-payg` | None | Worst-case / baseline comparison |

The **Reservations sheet** is always populated regardless of mode, so you can see the full savings opportunity even in `all-payg` mode.

---

## 🖥️ VM series selection

The tool maps VMs to **Dsv5** (general purpose) or **Esv5** (memory optimised) series — both v5 generation, Intel-based, and available across ~58 Azure regions, making them the safest default for estimates targeting any region. The web app uses the same mapping.

The D/E split uses a RAM/vCPU ratio threshold of 8 GB/vCPU — matching the architectural difference between the two families:
- **Dsv5** — ratio ≤ 8 GB/vCPU (e.g. Standard_D4s_v5 = 4 vCPUs / 16 GB)
- **Esv5** — ratio > 8 GB/vCPU (e.g. Standard_E4s_v5 = 4 vCPUs / 32 GB)

The newer v6 series (Dsv6/Esv6, GA February 2025) is 15–30% faster per dollar but only available in ~13 regions today. v5 remains the recommended default for broad regional coverage and is still listed by Microsoft as a current migration target alongside v6.

---

## 🔄 Web pricing data

The web app cannot call the Azure Retail Prices API directly (blocked by browser CORS policy), so pricing data is pre-fetched and stored as static JSON files in `docs/prices/` — one file per region, with all supported currencies embedded.

These files are refreshed automatically every Monday by a GitHub Actions workflow. To refresh them manually:

```bash
# Requires the [tools] optional dependency
pip install azure-rvtools[tools]

python scripts/fetch_web_prices.py
```

The script fetches all 42 regions × 6 currencies in parallel (~3 minutes) and displays a live progress board in the terminal.

---

## ⚠️ Notes

- The CLI relies on real-time pricing from the Azure Retail Prices API. Pricing data is cached locally (in `~/.cache/azure-rvtools/`) to speed up repeat runs for the same region and currency. Use `--no-cache` to force a refresh.
- The web app uses pre-fetched pricing data updated weekly — no live API calls from the browser.
- Azure Hybrid Benefit is enabled by default. If your Windows VMs are covered by existing on-premises licenses migrating to Azure, this reflects your actual compute cost. Use `--os-license-included` if licences are not being brought across.
- Windows reserved pricing is derived as `linux_reserved + (windows_payg − linux_payg)` since the Azure Retail Prices API only publishes Linux reservation products.
- Generated Excel workbooks contain formulas and formatting. Excel recalculates automatically when opened.

---

Happy estimating! Contributions and pull requests are welcome.

---

MIT License — © 2026 Kim Tholstorf
