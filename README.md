# VX7 KHATA PRO

Offline customer ledger (credit / debit) manager for Windows. Python + PySide6 + SQLite.
Scope is the customer ledger only: no inventory, stock or billing/POS.

## Features

**Ledger core**
* Unlimited customers, each with a separate ledger; search by name or phone; per-customer and overall totals.
* Item-wise entries (item, quantity, rate, amount, notes). Quantity x Rate is calculated automatically.
* **Quick payment (Ctrl+J).** Only a payment? Pick the customer, type the amount, press Enter – no item needed. Available on the Dashboard, Customers, New Entry and Ledger pages.
* Entry types are shown as **Debit (Dr)** (customer took goods/money on credit, so the customer owes you) and **Credit (Cr)** (customer paid), the usual accounting convention for a customer's account. The words live in `vx7khata/labels.py` – edit them there to rename them everywhere (saved data is unaffected).
* Back-dated entries for any past date (date picker or DD-MM-YYYY). Running balances are always recomputed from the saved entries, so adding, editing or deleting an old entry can never leave a wrong balance.
* Ledger with date range, item search, opening / closing balance, edit and delete (with confirmation).
* PDF and Excel statements, registers and balance summaries (only the data you select is exported).
* Backup and restore with validation and an automatic safety copy.

**Added for business use**
* **Credit limit per customer.** Saving a Debit (Dr) entry, or editing an entry so that it takes a customer above their limit, asks for confirmation first. The Customers table, ledger and entry form show how much of the limit is used.
* **Due dates.** A Debit (Dr) entry can carry a promised payment date; overdue items are highlighted in the ledger.
* **Recovery page.** Aging report (0-30 / 31-60 / 61-90 / 90+ days) using FIFO (payments settle the oldest unpaid debit first; an advance offsets the next debit), overdue payments, and customers over their credit limit. Aging and overdue lists export to PDF and Excel.
* **Payment reminders.** Editable message template (English / Hinglish default). Copy it, or open it in WhatsApp (needs internet and WhatsApp on that PC; the app itself never sends anything).
* **Dashboard.** KPI cards, 12-month given-vs-received chart, top debtors and a "needs attention" panel.
* **Activity log.** Every customer / entry add, edit, delete and restore is recorded with before-and-after values. The app has no way to edit or remove these records. It is not tamper-proof: anyone with database tools can alter the file.
* **PIN lock** with a one-time recovery code, lock-out after repeated wrong PINs (the lock-out survives closing and reopening the app), and "Lock now" (Ctrl+L). This locks the app window; it does **not** encrypt the data file.
* **Automatic daily backup** (keeps the latest 14) to a folder you choose, plus manual backup.
* **Daily Entry page:** type many entries (type, customer, product, date, quantity, amount, automatic total, notes) in one grid. Rows added here do **not** go into the ledger straight away: they wait in a list below the grid and are saved into the customer's ledger **automatically 24 hours later** (checked every minute while the app is open, and at start-up for the time it was closed). Until then a row can be **deleted** (e.g. the customer brought the goods back; nothing reaches the ledger) or saved early with **Save now**. Waiting rows are not part of any balance, ledger, report or credit-limit balance until they are saved (the credit-limit warning does count them when you add a new row). The activity log records adding, deleting and saving them.
* **Print / PDF / Excel in one click:** select a customer on the Customers, New Entry or Ledger page and use the **Print**, **PDF** and **Excel** buttons for that customer's statement.
* **Light and dark themes**, business name and address printed on statements, keyboard shortcuts (Ctrl+1..9 pages, Ctrl+N new entry, Ctrl+J quick payment, F5 refresh).

## What is in the box

| Area | Where |
|---|---|
| Database, schema (v4), migrations, FK/indexes | `vx7khata/db.py` |
| Customers, entries, ledger, balances, dashboard, activity log | `vx7khata/service.py` |
| Aging, overdue, trends, reminders | `vx7khata/insights.py` |
| PIN lock | `vx7khata/security.py` |
| Backup / validation / restore | `vx7khata/backup.py` (+ `KhataService.restore_backup`) |
| PDF + Excel exports | `vx7khata/exports.py` |
| Desktop UI | `vx7khata/ui/` (`main_window.py`, `dialogs.py`, `widgets.py`, `charts.py`, `theme.py`) |
| Tests | `tests/` |
| Windows build (PyInstaller, Inno Setup, CI) | `packaging/`, `.github/workflows/` |

## Run from source

```
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Data is stored in `%APPDATA%\VX7 KHATA PRO\khata.db`. Set `VX7_DATA_DIR` to use another folder (e.g. a USB stick).

## Design decisions worth knowing

* **Money is integer paise.** No floating point anywhere in totals. Amounts accept at most 2 decimals; quantities at most 3.
* **Balances are never stored.** Every total and running balance is computed from the saved rows.
* **Two timestamps.** `txn_date` is the date the transaction happened (may be years ago). `created_at` is when the row was really entered and is never changed, not even by an edit (`updated_at` records edits).
* **Ordering.** Ledger order is transaction date, then entry order, so same-day entries are stable.
* **Item search keeps true balances.** When you search by item, the running balance column still shows the real account balance.
* **Dates.** Entered/shown as DD-MM-YYYY; future dates are rejected for transactions (due dates may be in the future); earliest year 1990.
* **Dashboard.** "Today's transactions" = entries dated today. "Historical transactions" = entries dated before today.
* **Negative balance** means the customer has paid in advance. Shown in green; a positive balance (customer owes you) in red.
* **Duplicate protection.** The Save button is disabled while saving, and the service rejects an identical entry (same customer, date, type, item, amount, notes) saved within 10 seconds; the UI then asks whether it is a genuine second entry.
* **Deleting.** Every entry delete needs confirmation. A customer that has entries cannot be deleted.
* **Backup.** Uses SQLite's online-backup API, then re-opens and checks the copy. **Restore** validates the file (SQLite header, integrity check, schema, foreign keys, entry sanity, not from a newer app version), writes a safety copy to `safety_backups\`, then swaps the file in. Settings (including the PIN and theme) come from the restored backup. Older-schema backups are upgraded on restore.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest -q tests          # or: python -m unittest discover -s tests -t .
```

`tests/test_acceptance.py` maps one-to-one to acceptance tests A-F (E is also run across two separate Python processes).
`tests/test_daily_pending.py` covers the 24-hour Daily Entry rows (add, delete, auto-save, save now, backup/restore, upgrade).
`tests/test_premium.py`, `test_export_premium.py`, `test_viewmodel.py` cover credit limits, due dates, aging, reminders, PIN, audit log, auto-backup and the new exports.
`tests/test_ui_smoke.py` drives the real Qt window headlessly (14 scenarios); it is skipped automatically if PySide6 is not installed.

## Build the Windows executable and installer

Requirements: Windows 10/11 (64-bit), Python 3.10+ from python.org, and optionally [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the installer.

```
packaging\build_windows.bat
```

It creates a venv, installs dependencies, **runs the tests (and stops if they fail)**, deletes old `build`/`dist` folders, builds `dist\VX7 KHATA PRO\VX7 KHATA PRO.exe` with PyInstaller, and builds `dist\installer\VX7_KHATA_PRO_Setup_1.0.0.exe` if Inno Setup is installed.

Manual steps if you prefer:

```
pip install -r requirements-dev.txt
pyinstaller packaging\vx7_khata_pro.spec --noconfirm --clean
ISCC packaging\installer.iss
```

`.github/workflows/windows-build.yml` does the same on GitHub Actions (`windows-latest`) and uploads the exe and installer as artifacts.

Notes: the installer is unsigned, so Windows SmartScreen may warn on first run. Uninstalling does not delete your data in `%APPDATA%\VX7 KHATA PRO`.

## Known limitations

* **PDF text rendering** uses the bundled DejaVu Sans font. English text and the rupee sign are fine. ReportLab does not do complex-script shaping, so Hindi (Devanagari) item names will not shape correctly in PDFs. They are stored, shown in the app and exported to Excel correctly.
* One customer name must be unique (case-insensitive). Add a locality to tell two people apart ("Rahul (Market Road)").
* Single user, single machine. Two people editing one data file over a network share is not supported.
* The PIN is a screen lock, not encryption (see above).
