# Handover

- **Name:** [Candidate Name]
- **Email used for this application:** [Candidate Email]
- **Chosen track:** Track A: Repair the register (Product Engineering)
- **Why this track:** Track A offers a realistic audit and refactoring scenario focused on financial data integrity, regression testing, and resilient user experience.
- **Approximate total time, including setup and handover:** ~3.5 hours

---

## 1. Project Overview & Architecture

ClearLedger is a local invoice and payment tracking application built for a fictional service business. The application is implemented in pure Python 3 using only the standard library (`http.server`, `sqlite3`, `csv`, `json`, `pathlib`) and a vanilla HTML5/CSS/ES6 single-page web interface.

The project was audited, all six seeded defects were repaired, data preservation against the owner's historical register was verified, and an interactive customer filtering improvement was introduced.

---

## 2. Run and Verify

### Prerequisites
* Python 3.10 or newer (tested on Python 3.14).
* Modern web browser.
* No external packages, virtual environments, or credentials required.

### Clean-Start Commands for Evaluation

1. **Restore the Owner's Existing Register:**
   ```bash
   python restore_fixture.py --replace
   ```
   *Note: This command copies `fixtures/existing-register.sqlite3` into `.local/clearledger.sqlite3` with the server stopped. The original fixture in `fixtures/` remains pristine.*

2. **Start the Application Server:**
   ```bash
   python app.py
   ```
   *The server starts on `http://127.0.0.1:8787` (or specify `--port <number>`). Keep the terminal open; press Ctrl+C to stop.*

3. **Run the Complete Test Suite:**
   ```bash
   python -m unittest discover -s tests -v
   ```

### Important Runtime Distinction
The repository supports two database workflows:
* **Fresh Demo Data:** Running `python app.py reset-demo` or launching `python app.py` without an existing database seeds a 6-invoice demo register.
* **Owner's Production Register:** Running `python restore_fixture.py --replace` loads the owner's actual 9-invoice register into the disposable local path `.local/clearledger.sqlite3`.
* The `.local/` folder is a runtime-generated working directory that is strictly ignored by `.gitignore` and excluded from submission.

---

## 3. What I Delivered

### Six Seeded Defects Repaired

1. **Open / Paid Invoice Filter (`ledger/reporting.py`)**
   * *Problem:* The mapping `{'open': 'paid', 'paid': 'paid'}` caused filtering for `status=open` to return paid invoices instead of open ones.
   * *Fix:* Mapped `status` directly to filter `r['status'] == status`. Invoices with positive balance (`balance > 0`) are returned as `open`; zero or negative balances (overpayments) are returned as `paid`.
   * *Tests:* `test_open_filter_returns_only_positive_balance_invoices`, `test_paid_filter_returns_only_zero_or_negative_balance_invoices`, `test_overpaid_invoice_is_classified_as_paid`.

2. **Monetary Precision & CSV Export (`ledger/reporting.py`)**
   * *Problem:* `int(item[key] * 100) / 100:.2f` truncated monetary values due to IEEE 754 floating-point inaccuracies, converting values like `19.99` into `19.98` and disagreeing with the UI.
   * *Fix:* Standardized monetary calculations and formatting to use clean two-decimal rounding (`round(float(...), 2)` and `f"{val:.2f}"`).
   * *Tests:* `test_csv_export_monetary_precision_and_no_truncation`, `test_csv_export_two_decimal_formatting_across_all_rows`, `test_csv_export_agrees_with_reporting_invoices`.

3. **Strict Payment Matching (`ledger/matching.py`)**
   * *Problem:* The system queried `candidates` and matched payments based on amount alone across arbitrary customers before checking invoice references.
   * *Fix:* Removed amount-only matching. Payments now attach strictly when **both** `customer_id` and `invoice_number` match an existing invoice via `invoice_by_key()`. Unmatched payments are retained as unmatched with `invoice_id = None`.
   * *Tests:* `test_payment_matching_with_same_amount_across_customers`, `test_payment_with_unmatched_reference_remains_unmatched`.

4. **Invoice Import Idempotency (`ledger/storage.py`)**
   * *Problem:* Re-importing an invoice always executed an unconditional `INSERT`, duplicating records and inflating balances.
   * *Fix:* Implemented duplicate detection in `insert_invoice()`:
     * Same `(customer_id, invoice_number)` with identical `amount` and `due_date` returns `'skipped'`.
     * Same identity with different `amount` or `due_date` raises a `ValueError` (`'Invoice already exists with different details'`), categorizing the row as `'rejected'` and preserving the original invoice.
     * New invoice identity returns `'imported'`.
   * *Tests:* `test_new_invoice_import`, `test_exact_duplicate_invoice_is_skipped`, `test_conflicting_duplicate_invoice_is_rejected`.

5. **Row-Level CSV Validation (`ledger/importing.py`)**
   * *Problem:* An upfront list comprehension normalized all rows before insertion. A single invalid row raised `ValueError`, failing the entire import with HTTP 400 and discarding valid rows.
   * *Fix:* Replaced the upfront list comprehension with row-by-row iteration. Normalization errors reject only the offending row, reporting its 1-indexed CSV line number and error reason, while remaining valid rows are imported and committed.
   * *Tests:* `test_mixed_csv_imports_valid_rows_and_rejects_invalid_row`, `test_http_import_partial_rejection`.

6. **Browser Import Feedback & Error Handling (`web/app.js`)**
   * *Problem:* The frontend did not inspect `response.ok` or the JSON response body, always unconditionally displaying `"Import complete. Your records are ready."`, masking server errors and row rejections.
   * *Fix:* Updated `submitImport()` to check `response.ok`. On HTTP failure (e.g. HTTP 400 wrong header), it displays `"Import failed: <reason>"`. On success/partial rejection (HTTP 200), `formatImportResult()` displays exact `Imported`, `Skipped`, and `Rejected` counts, along with line-by-line rejection reasons under `"Rejected rows:"`.
   * *Tests:* `test_http_import_success`, `test_http_import_duplicate_skipped`, `test_http_import_partial_rejection`, `test_http_import_wrong_header_fails_with_400`.

### Useful Improvement: Customer Account Filtering
* **Owner Problem Solved:** The owner previously had to scroll through mixed invoices from all clients. Reconciling a single client account before billing or sending payment reminders required manual scanning.
* **Backend:** Added optional `customer_id` parameter to `GET /api/invoices?status=...&customer_id=...` in `ledger/reporting.py` and `ledger/http_app.py`. Added `/api/customers` route and included a `customers` list in `GET /api/overview`. Unknown customer IDs return `[]` cleanly without error.
* **Frontend:** Added a `<select id="customer-filter">` dropdown in `web/index.html` adjacent to the status selector. Options (`HARBOR`, `MAPLE`, `NORTH`) are dynamically populated from `overview.customers`. Changing selection reloads invoices while preserving the active status filter.
* **Tests:** `test_customer_filter_all_returns_baseline_invoices`, `test_customer_filter_by_specific_customer`, `test_customer_filter_and_status_combined`, `test_customer_filter_unknown_customer_returns_empty`, `test_http_invoices_customer_filter`, `test_http_customers_list`.

---

## 4. Evidence and Limits

### Data Preservation Verification
Preservation of the owner's register was verified using `tests/test_preservation.py` against `fixtures/existing-register.sqlite3` and `fixtures/expected-records.json`:
* **Customers:** Exactly 3 (`HARBOR`, `MAPLE`, `NORTH`).
* **Invoices:** Exactly 9 (6 demo + 3 owner invoices).
* **Payments:** Exactly 5.
* **Open Invoices:** Exactly 7.
* **Paid Invoices:** Exactly 2 (`INV-101`, `KEEP-702`).
* **Unmatched Payments:** Exactly 1 (`KEEP-U1` — MAPLE / WAIT-900 / INR 33.33).
* **Total Outstanding Balance:** Exactly **INR 3,698.19**.
* **Integrity Check:** SHA256 hashes of `fixtures/existing-register.sqlite3` and `fixtures/expected-records.json` were verified against the pristine reference copy, confirming zero modifications.

### Changed-Input Test Case (Reproduction Evidence)
* **File:** `samples/invoices-mixed.csv`
  * Line 2: `HARBOR,INV-103,84.00,2026-09-12` (valid)
  * Line 3: `NORTH,INV-302,not-a-number,2026-09-12` (malformed amount)
  * Line 4: `MAPLE,INV-203,100.00,2026-09-13` (valid)
* **Before Fix:** Aborted the entire request with HTTP 400; 0 rows imported. Frontend erroneously reported "Import complete".
* **After Fix:** HTTP 200 OK. Imported: 2, Skipped: 0, Rejected: 1. Error logged: `Line 3 — amount must be a positive decimal with at most two decimal places`. Valid rows `INV-103` and `INV-203` safely committed to the register. UI accurately displays counts and rejection reason.

### Automated Test Results
Total tests: **36 passed, 0 failures, 0 errors** in ~5.1s.
* `tests/test_smoke.py`: 5 starter smoke checks.
* `tests/test_repairs.py`: 25 defect repair and HTTP integration checks.
* `tests/test_preservation.py`: 6 data preservation, restart persistence, and fixture integrity checks.

### Manual Verification Conducted
Manual live HTTP interactions against the running server (`python app.py`) confirmed:
1. Status filter: "Open invoices" displays only positive balances; "Paid invoices" displays zero/negative balances.
2. Customer filter: Selecting "HARBOR" displays only Harbor invoices; combining with "Open" displays only open Harbor invoices. Selecting "All customers" resets the filter.
3. Export CSV: Verified exported amounts match screen display exactly without truncated cents.
4. Duplicate import: Uploading an identical invoice returns `skipped = 1` and does not alter totals.
5. Wrong header: Uploading `samples/wrong-header.csv` returns HTTP 400 and renders `"Import failed: Expected CSV header..."`.
6. Persistence: Register state survives server shutdown and restart.

### Known Limitations & Project Boundaries
* **Out-of-Scope Requirements (`BUSINESS_RULES.md`):** Customer creation, historical payment re-allocations, automatic rematching of unmatched payments on future imports, tax/FX calculations, credit notes, and multi-user concurrency control are out of scope.
* **Testing Scope:** Automated tests cover Python unit, regression, database, and live HTTP API integration. Browser client behavior was verified through manual interactions and DOM script review; no headless browser automation suite (such as Playwright or Selenium) was added, avoiding external dependencies.

---

## 5. Tools and Judgment

AI-assisted development tools were utilized during this assessment for codebase analysis, defect diagnosis, and drafting test coverage:
1. **Idempotency Strategy:** An AI suggestion proposed adding a `UNIQUE(customer_id, invoice_number)` constraint to the SQLite schema. *Decision:* Rejected altering the schema to avoid complex table recreation migrations on the existing database. Instead, implemented the uniqueness and conflict checks in `storage.insert_invoice()`, ensuring 100% backward compatibility with existing fixture data.
2. **Monetary Formatting:** Explored using Python's `decimal.Decimal` module across all layers. *Decision:* Kept the existing float storage structure to prevent breaking API contracts, but eliminated truncation by replacing `int(x * 100) / 100` with standard round-to-cents formatting (`f"{round(float(val), 2):.2f}"`), verified via regex and exact value assertions.
3. **Customer Filter UI:** An AI proposal suggested hardcoding the customer list in JavaScript. *Decision:* Dynamically populated customer options from `overview.customers` (and exposed `/api/customers`), keeping the UI responsive to database state.

All generated code and tests were manually reviewed, executed, and validated.

---

## 6. Submission Checklist

- [ ] Track folder contains only chosen Track A files (`fixtures/`, `ledger/`, `samples/`, `tests/`, `web/`, `app.py`, `restore_fixture.py`, `HANDOVER.md`).
- [ ] No generated `.local/` database, `.venv/`, or `__pycache__/` artifacts included.
- [ ] Fixture files (`fixtures/existing-register.sqlite3`, `fixtures/expected-records.json`) remain intact.
- [ ] Automated tests pass cleanly (`python -m unittest discover -s tests -v`).
- [ ] Public repository or download link prepared.

### Applicant Details Placeholder
* **GitHub Repository URL:** `[Your Repository URL]`
* **GitHub Profile URL:** `[Your GitHub Profile URL]`
* **LinkedIn URL:** `[Your LinkedIn Profile URL]`
* **Resume:** `[Attached / Link]`
