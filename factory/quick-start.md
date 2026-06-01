# Boboloo Factory Quick-Start

## What this does

Registers each physical toy in the Boboloo backend and produces:
- A **manifest CSV** for box label printing (one row per toy)
- A **JSON audit report** to email to Boboloo

This must be done for every toy before it is shipped.

---

## Requirements

- Python 3.8 or newer
- Internet access from the provisioning laptop
- The `.env.factory` credentials file (provided by Boboloo)

---

## One-time setup

**1. Install the only dependency**

```
pip install requests
```

**2. Place credentials**

Copy `env.factory.template` to `.env.factory` in the same folder as `factory_provision.py`,
then fill in the two values Boboloo gave you:

```
BOBOLOO_API_URL=https://api.boboloo.com
BOBOLOO_FACTORY_SECRET=<your key>
```

**3. Verify the tool starts**

```
python factory_provision.py --help
```

You should see usage information. If you see an error, see the
[Failure Runbook](failure-runbook.md).

---

## Before each batch

Get from your Boboloo contact:

| Value | Example | Where to use |
|---|---|---|
| Batch ID | `BATCH-2026-001` | `--batch-id` |
| Firmware version | `1.2.0` | `--firmware` |
| Hardware revision | `A1` | `--hw` |

---

## Step-by-step

### Step 1 — Build `device_ids.txt`

For each toy in the batch, read its `factory_device_id` via BLE scan and add it to
`device_ids.txt` (one ID per line, in the same folder as the script):

```
BBL-0001
BBL-0002
BBL-0003
```

Lines starting with `#` and blank lines are ignored.

### Step 2 — Dry run (optional but recommended)

Validate the ID file without touching the backend:

```
python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.2.0 --hw A1 --dry-run
```

Fix any reported errors before continuing.

### Step 3 — Provision

```
python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.2.0 --hw A1
```

What you should see when it works:

```
Boboloo Factory Provisioning Tool v1.0.0
=======================================================
  Batch ID    : BATCH-2026-001
  Firmware    : 1.2.0
  Hardware    : A1
  ID file     : /path/to/device_ids.txt
  Device IDs  : 100 valid

  Connecting to https://api.boboloo.com ... OK

  Provisioning 100 device(s) in 1 chunk(s)...

  Chunk 1/1 (100 devices)... OK  (100 new)

=======================================================
  Total requested : 100
  New             : 100
  Already existed : 0
  Failed          : 0
  Manifest CSV    : manifest_BATCH-2026-001_20260601-093045.csv
  Audit report    : report_BATCH-2026-001_20260601-093045.json
=======================================================

  All devices provisioned successfully.
  Email the CSV and JSON report to your Boboloo contact now.
```

### Step 4 — Send files to Boboloo

Email both output files to your Boboloo contact:
- `manifest_BATCH-2026-001_*.csv`
- `report_BATCH-2026-001_*.json`

Keep copies on the factory laptop for 90 days.

### Step 5 — Print box labels

Use the manifest CSV to print a label for each box.
The `device_id` column is the code the customer types during app setup.

---

## Re-running is safe

If the script is interrupted or fails partway through, re-run the same command.
Devices that were already provisioned will be counted as "already existed" —
no duplicates are created.

---

## Something went wrong?

See [failure-runbook.md](failure-runbook.md) or call your Boboloo contact.

**Do not attempt to fix errors yourself** — do not edit the backend database
or API directly. Report the exact error text and the JSON report file.
