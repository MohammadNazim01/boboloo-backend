# Boboloo Factory Provisioning — Failure Runbook

This document covers every error you may see from `factory_provision.py`
and the exact steps to take for each one.

**When in doubt, stop and call your Boboloo contact before continuing.**

---

## Quick reference

| What you see | What to do |
|---|---|
| `'requests' is not installed` | Run `pip install requests` |
| `BOBOLOO_API_URL is not set` | Check `.env.factory` file |
| `BOBOLOO_FACTORY_SECRET is not set` | Check `.env.factory` file |
| `N line(s) failed validation` | Fix `device_ids.txt`, re-run |
| `Could not reach the Boboloo backend` | Check internet, call Boboloo |
| `Factory secret rejected (HTTP 403)` | Call Boboloo immediately |
| `connection error, retrying…` | Wait — the script retries automatically |
| `FAILED (validation)` | Check the error detail, fix IDs, re-run |
| `FAILED` with no detail | Call Boboloo, send the JSON report |

---

## Errors before the API is called

---

### `'requests' is not installed`

```
ERROR: 'requests' is not installed.
       Run:  pip install requests
```

**What happened:** The Python `requests` library is missing.

**Fix:**
```
pip install requests
```
Then re-run the script.

---

### `BOBOLOO_API_URL is not set` or `BOBOLOO_FACTORY_SECRET is not set`

```
ERROR: BOBOLOO_API_URL is not set.
       Add it to .env.factory or set it as an environment variable.
```

**What happened:** The credentials file was not loaded or is missing a value.

**Fix:**
1. Check that `.env.factory` exists in the same folder as `factory_provision.py`
2. Open `.env.factory` and confirm both lines are present and not blank:
   ```
   BOBOLOO_API_URL=https://api.boboloo.com
   BOBOLOO_FACTORY_SECRET=<your key>
   ```
3. Make sure there are no extra spaces around the `=` sign
4. Re-run the script

If the file exists and values look correct, try setting them manually before running:
```
# Mac / Linux
export BOBOLOO_API_URL=https://api.boboloo.com
export BOBOLOO_FACTORY_SECRET=your-key-here
python factory_provision.py ...

# Windows Command Prompt
set BOBOLOO_API_URL=https://api.boboloo.com
set BOBOLOO_FACTORY_SECRET=your-key-here
python factory_provision.py ...
```

---

### `ID file not found`

```
ERROR: ID file not found: device_ids.txt
       Create a file with one factory_device_id per line.
```

**What happened:** The `device_ids.txt` file does not exist or is in a different folder.

**Fix:**
1. Create `device_ids.txt` in the same folder as `factory_provision.py`
2. Add one device ID per line — no headers, no spaces
3. Or specify the file path explicitly: `--ids /path/to/your_ids.txt`

---

### `N line(s) in the ID file failed validation`

```
  WARNING: 3 line(s) in the ID file failed validation and will be skipped:
    "BB L001" — contains invalid characters (allowed: A-Z, 0-9, hyphen)
    "AB" — too short (2 chars, minimum 4)
    "BBL-TOOLONG-DEVICE-ID-EXCEEDS-MAXIMUM" — too long (38 chars, maximum 32)
```

**What happened:** Some device IDs in the file do not match the required format.
Valid IDs must be 4–32 characters, containing only letters (A–Z), digits (0–9),
and hyphens.

**Fix:**
1. **Say N** when asked "Continue with the valid IDs only?"
2. Open `device_ids.txt` and find the flagged lines
3. Common causes:
   - Space in the middle of an ID → BLE scan picked up extra characters
   - ID too short → scan only captured part of the ID
   - Special characters → scan corruption or manual typo
4. Re-scan the affected toys via BLE to get the correct IDs
5. Fix the file and re-run with `--dry-run` first to confirm all IDs pass
6. Then re-run without `--dry-run`

If the BLE scan consistently returns a malformed ID for a specific toy,
that toy may have a firmware defect. Set it aside and report it to your
firmware engineer.

---

### `No valid device IDs found in the file`

```
ERROR: No valid device IDs found in the file.
```

**What happened:** `device_ids.txt` exists but is empty or contains only
blank lines and comments.

**Fix:** Add at least one valid device ID to the file.

---

## Errors during provisioning

---

### `Could not reach the Boboloo backend`

```
ERROR: Could not reach the Boboloo backend at https://api.boboloo.com
       Check your internet connection and BOBOLOO_API_URL.
```

**What happened:** The laptop cannot connect to the Boboloo backend.
The health check failed before any provisioning started. No toys were provisioned.

**Fix:**
1. Check that the laptop has internet access — try opening `https://api.boboloo.com/health`
   in a browser. You should see `{"status": "ok"}`
2. If the browser cannot reach it either, the backend may be down — call your Boboloo contact
3. If the browser reaches it but the script can't, check for a VPN or proxy blocking outbound connections
4. Verify `BOBOLOO_API_URL` does not have a typo and starts with `https://`

**This error is safe to retry** — nothing was provisioned yet.

---

### `Factory secret rejected (HTTP 403)`

```
ERROR: Factory secret rejected (HTTP 403).
       Check BOBOLOO_FACTORY_SECRET in .env.factory.
```

**What happened:** Your factory secret was rejected by the backend.
The script exits immediately without provisioning anything.

**What to do:**
1. **Stop immediately** — do not retry with guessed values
2. Check `.env.factory` for a typo in `BOBOLOO_FACTORY_SECRET`
3. If the value looks correct, call your Boboloo contact — the key may have been rotated
4. Do not share the secret with anyone else on the factory floor

---

### `connection error, retrying…`

```
  Chunk 1/3 (100 devices)... (connection error, retrying in 2s — attempt 1/3)
  (connection error, retrying in 5s — attempt 2/3)
  (connection error, retrying in 10s — attempt 3/3)
  FAILED
```

**What happened:** The script lost connection to the backend mid-request.
It retried automatically 3 times. If all retries failed, the chunk is
marked as failed.

**What to do:**
1. Check internet connection
2. Re-run the script — it is safe to re-run (`provision-batch` is idempotent)
3. Devices from successful chunks are already provisioned and will appear as
   "already existed" on re-run — that is expected and correct
4. If repeated connection failures occur, call your Boboloo contact

---

### `Chunk N/M ... FAILED (validation)`

```
  Chunk 2/3 (100 devices)... FAILED (validation)
         Validation error from backend: Invalid device_id format: ['BBL-???-BROKEN', ...]
```

**What happened:** The backend rejected one or more IDs in this chunk as
invalid format. This can happen if an ID passed local validation but the
backend's rules are slightly stricter (this should not normally happen).

**What to do:**
1. Look at the device IDs listed in the error message
2. Re-scan those specific toys via BLE to confirm their IDs
3. Correct `device_ids.txt` and re-run

**Re-running is safe** — already-provisioned chunks are not re-provisioned.

---

### `Chunk N/M ... FAILED` (generic)

```
  Chunk 2/3 (100 devices)... FAILED
         Failed after 3 attempts. Last error: ...
```

**What happened:** An unexpected server error occurred. The script retried
3 times and all failed.

**What to do:**
1. Do not continue provisioning
2. Note which chunk number failed (the output shows chunk N of M)
3. Call your Boboloo contact and send them:
   - The full terminal output (screenshot or copy-paste)
   - The `report_*.json` file — it records which IDs succeeded and which failed
4. After the issue is resolved, re-run — it is safe to re-run the full batch

---

## End-of-run failures

---

### `ACTION REQUIRED` at the end

```
  FAILED IDs (12):
    BBL-0045
    BBL-0046
    ...

  ACTION REQUIRED — see failure-runbook.md for next steps.
  Email the JSON report to your Boboloo contact before continuing.
```

**What happened:** Some device IDs could not be provisioned. The rest of
the batch succeeded.

**What to do:**
1. Email the `report_*.json` file to your Boboloo contact immediately
2. Do not ship the failed toys
3. Set the failed toys in a "hold" tray with the printed list of their IDs
4. After your Boboloo contact confirms the issue, re-run with only the failed IDs:
   - Create a new `device_ids.txt` with only the failed IDs
   - Re-run with the same `--batch-id`, `--firmware`, and `--hw` values
5. If the same IDs fail repeatedly, those toys may have a BLE firmware defect

---

## Hardware and BLE issues

---

### BLE scan returns no result

**What happened:** The toy is powered on but the BLE scanner app shows
no advertisement from it.

**Fix:**
1. Power cycle the toy and wait 5 seconds
2. Try scanning again
3. If still no result after 2 attempts, set the toy in a "BLE fail" tray
4. Do not attempt to provision it with a manually invented ID
5. Report the count of BLE failures to your firmware engineer at end of shift

**Never guess or invent a device ID.** The ID burned into the firmware must
match what you register in the backend exactly. A manually entered wrong ID
creates an orphaned backend record and the toy will never be able to identify itself.

---

### BLE scan returns a corrupted or partial ID

**What happened:** The scanner shows an ID like `BBL-` or `BBL-00` — clearly
incomplete, or contains garbage characters.

**Fix:**
1. Power cycle the toy and re-scan
2. If the partial ID repeats consistently, the toy has a firmware defect
3. Set it in the "BLE fail" tray and report to firmware engineer

---

### Provisioning succeeds but firmware flash fails (post-provisioning)

**What happened:** The toy was provisioned in the backend (it now exists in the
system with status `PROVISIONED`), but the subsequent firmware flash step failed.

**Options:**
- **Attempt reflash:** If the reflash succeeds, the toy is already provisioned
  and can proceed to packaging normally.
- **Toy cannot be recovered (hardware failure):** Call your Boboloo contact.
  They will disable the toy in the backend using its `device_id`. Do not ship it.

**Do not provision a replacement PCB with the same `device_id`.** If a new
PCB is substituted, it will have its own `factory_device_id` burned in by
the chip programmer — provision that new ID normally.

---

## Summary: safe to re-run

Re-running `factory_provision.py` with the same `--batch-id` and the same
`device_ids.txt` is always safe. The backend will return already-provisioned
toys as "already existed" with no side effects.

The only time you need human intervention (your Boboloo contact) is when
you see a **403**, a **repeated generic FAILED** that retries cannot clear,
or a **hardware defect** that prevents correct BLE reads.
