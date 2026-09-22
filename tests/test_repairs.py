"""Regression tests for the three repaired backend defects:
1. Open/paid invoice filtering
2. Monetary formatting and CSV export
3. Strict payment matching by customer_id and invoice_number
"""
import csv
import io
import re
import tempfile
import unittest
from pathlib import Path

from ledger import storage, reporting, importing


class BackendRepairsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'repairs.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # --- Defect 1: Open / Paid Invoice Filter ---

    def test_open_filter_returns_only_positive_balance_invoices(self):
        open_invoices = reporting.invoices(self.db, status='open')
        self.assertGreater(len(open_invoices), 0, "Should have open invoices in seeded data")
        for inv in open_invoices:
            self.assertEqual(inv['status'], 'open')
            self.assertGreater(inv['balance'], 0, f"Invoice {inv['invoice_number']} should have positive balance")

    def test_paid_filter_returns_only_zero_or_negative_balance_invoices(self):
        paid_invoices = reporting.invoices(self.db, status='paid')
        self.assertGreater(len(paid_invoices), 0, "Should have paid invoices in seeded data")
        for inv in paid_invoices:
            self.assertEqual(inv['status'], 'paid')
            self.assertLessEqual(inv['balance'], 0, f"Invoice {inv['invoice_number']} should have <= 0 balance")

    def test_filter_partition_and_invalid_status(self):
        all_invoices = reporting.invoices(self.db, status='all')
        open_invoices = reporting.invoices(self.db, status='open')
        paid_invoices = reporting.invoices(self.db, status='paid')

        self.assertEqual(len(all_invoices), len(open_invoices) + len(paid_invoices))
        all_ids = {r['id'] for r in all_invoices}
        open_ids = {r['id'] for r in open_invoices}
        paid_ids = {r['id'] for r in paid_invoices}
        self.assertEqual(open_ids & paid_ids, set(), "Open and paid sets must be disjoint")
        self.assertEqual(open_ids | paid_ids, all_ids, "Open and paid sets must cover all invoices")

        with self.assertRaises(ValueError):
            reporting.invoices(self.db, status='unknown_status')

    def test_overpaid_invoice_is_classified_as_paid(self):
        # Apply an overpayment to an invoice and verify it is returned under 'paid', not 'open'
        csv_data = "payment_id,customer_id,invoice_number,amount\nOVER-1,NORTH,INV-301,150.00\n"
        importing.import_csv(self.db, csv_data, 'payments')
        
        target = next(r for r in reporting.invoices(self.db, status='all') if r['invoice_number'] == 'INV-301')
        self.assertEqual(target['amount'], 100.00)
        self.assertEqual(target['paid'], 150.00)
        self.assertEqual(target['balance'], -50.00)
        self.assertEqual(target['status'], 'paid')

        paid_invoices = reporting.invoices(self.db, status='paid')
        self.assertIn(target['id'], {r['id'] for r in paid_invoices})

        open_invoices = reporting.invoices(self.db, status='open')
        self.assertNotIn(target['id'], {r['id'] for r in open_invoices})

    # --- Defect 2: Monetary Formatting & CSV Export ---

    def test_csv_export_monetary_precision_and_no_truncation(self):
        # INV-300 has amount 19.99 in seeded data; ensure it does not truncate to 19.98
        csv_text = reporting.export_csv(self.db)
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        inv_300 = next((r for r in rows if r['invoice_number'] == 'INV-300'), None)
        self.assertIsNotNone(inv_300, "INV-300 must exist in export")
        self.assertEqual(inv_300['amount'], '19.99', "Amount 19.99 must not truncate to 19.98")
        self.assertEqual(inv_300['paid'], '10.00')
        self.assertEqual(inv_300['balance'], '9.99')

    def test_csv_export_two_decimal_formatting_across_all_rows(self):
        csv_text = reporting.export_csv(self.db)
        reader = csv.DictReader(io.StringIO(csv_text))
        decimal_pattern = re.compile(r'^-?\d+\.\d{2}$')

        for row in reader:
            for field in ('amount', 'paid', 'balance'):
                self.assertRegex(
                    row[field],
                    decimal_pattern,
                    f"Field {field} value '{row[field]}' in row {row['invoice_number']} must have exactly 2 decimal places"
                )

    def test_csv_export_agrees_with_reporting_invoices(self):
        csv_text = reporting.export_csv(self.db)
        reader = list(csv.DictReader(io.StringIO(csv_text)))
        api_rows = reporting.invoices(self.db, status='all')

        self.assertEqual(len(reader), len(api_rows))
        for exp_row, api_row in zip(reader, api_rows):
            self.assertEqual(exp_row['customer_id'], api_row['customer_id'])
            self.assertEqual(exp_row['invoice_number'], api_row['invoice_number'])
            self.assertEqual(exp_row['amount'], f"{api_row['amount']:.2f}")
            self.assertEqual(exp_row['paid'], f"{api_row['paid']:.2f}")
            self.assertEqual(exp_row['balance'], f"{api_row['balance']:.2f}")
            self.assertEqual(exp_row['status'], api_row['status'])

    # --- Defect 3: Payment Matching ---

    def test_payment_matching_with_same_amount_across_customers(self):
        # In seeded data:
        # HARBOR / INV-100 has amount 1250.00 (id 1, paid 0.00)
        # MAPLE  / INV-200 has amount 1250.00 (id 2, paid 0.00)
        # A payment intended for MAPLE / INV-200 must NOT match HARBOR / INV-100 by amount alone.
        csv_data = "payment_id,customer_id,invoice_number,amount\nPAY-M200,MAPLE,INV-200,1250.00\n"
        result = importing.import_csv(self.db, csv_data, 'payments')
        self.assertEqual(result['imported'], 1)

        inv_maple = next(r for r in reporting.invoices(self.db) if r['customer_id'] == 'MAPLE' and r['invoice_number'] == 'INV-200')
        inv_harbor = next(r for r in reporting.invoices(self.db) if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'INV-100')

        self.assertEqual(inv_maple['paid'], 1250.00, "Payment must attach to MAPLE INV-200")
        self.assertEqual(inv_maple['balance'], 0.00)
        self.assertEqual(inv_maple['status'], 'paid')

        self.assertEqual(inv_harbor['paid'], 0.00, "HARBOR INV-100 must remain unchanged")
        self.assertEqual(inv_harbor['balance'], 1250.00)
        self.assertEqual(inv_harbor['status'], 'open')

    def test_payment_with_unmatched_reference_remains_unmatched(self):
        # Payment for non-existent invoice must remain unmatched and not change any balances
        csv_data = "payment_id,customer_id,invoice_number,amount\nPAY-NONE,NORTH,INV-999,50.00\n"
        result = importing.import_csv(self.db, csv_data, 'payments')
        self.assertEqual(result['imported'], 1)

        unmatched = reporting.overview(self.db)['unmatched_payments']
        unmatched_ids = [p['payment_id'] for p in unmatched]
        self.assertIn('PAY-NONE', unmatched_ids)

        target_payment = next(p for p in unmatched if p['payment_id'] == 'PAY-NONE')
        self.assertEqual(target_payment['customer_id'], 'NORTH')
        self.assertEqual(target_payment['invoice_number'], 'INV-999')
        self.assertEqual(target_payment['amount'], 50.00)

    # --- Defect 4: Invoice Import Idempotency ---

    def test_new_invoice_import(self):
        initial_invoices = reporting.invoices(self.db)
        initial_count = len(initial_invoices)
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,NEW-100,50.00,2026-09-20\n"
        result = importing.import_csv(self.db, csv_data, 'invoices')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['rejected'], 0)
        self.assertEqual(len(reporting.invoices(self.db)), initial_count + 1)

    def test_exact_duplicate_invoice_is_skipped(self):
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,NEW-200,75.00,2026-09-21\n"
        res1 = importing.import_csv(self.db, csv_data, 'invoices')
        self.assertEqual(res1['imported'], 1)
        count_after_first = len(reporting.invoices(self.db))

        # Re-import identical row
        res2 = importing.import_csv(self.db, csv_data, 'invoices')
        self.assertEqual(res2['imported'], 0)
        self.assertEqual(res2['skipped'], 1)
        self.assertEqual(res2['rejected'], 0)
        self.assertEqual(len(reporting.invoices(self.db)), count_after_first, "Invoice count must not increase on duplicate")

    def test_conflicting_duplicate_invoice_is_rejected(self):
        # Initial invoice
        csv_orig = "customer_id,invoice_number,amount,due_date\nHARBOR,CONFLICT-1,100.00,2026-09-25\n"
        res1 = importing.import_csv(self.db, csv_orig, 'invoices')
        self.assertEqual(res1['imported'], 1)
        count_after_orig = len(reporting.invoices(self.db))

        # Conflicting amount
        csv_conflict_amount = "customer_id,invoice_number,amount,due_date\nHARBOR,CONFLICT-1,200.00,2026-09-25\n"
        res2 = importing.import_csv(self.db, csv_conflict_amount, 'invoices')
        self.assertEqual(res2['imported'], 0)
        self.assertEqual(res2['skipped'], 0)
        self.assertEqual(res2['rejected'], 1)
        self.assertEqual(res2['errors'][0]['line'], 2)
        self.assertIn('already exists with different details', res2['errors'][0]['reason'])

        # Conflicting due_date
        csv_conflict_date = "customer_id,invoice_number,amount,due_date\nHARBOR,CONFLICT-1,100.00,2026-09-30\n"
        res3 = importing.import_csv(self.db, csv_conflict_date, 'invoices')
        self.assertEqual(res3['imported'], 0)
        self.assertEqual(res3['skipped'], 0)
        self.assertEqual(res3['rejected'], 1)
        self.assertEqual(res3['errors'][0]['line'], 2)

        # Original invoice must remain unchanged
        inv = next(r for r in reporting.invoices(self.db) if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'CONFLICT-1')
        self.assertEqual(inv['amount'], 100.00)
        self.assertEqual(inv['due_date'], '2026-09-25')
        self.assertEqual(len(reporting.invoices(self.db)), count_after_orig)

    # --- Defect 5: Row-Level Validation ---

    def test_mixed_csv_imports_valid_rows_and_rejects_invalid_row(self):
        csv_data = (
            "customer_id,invoice_number,amount,due_date\n"
            "HARBOR,INV-103,84.00,2026-09-12\n"
            "NORTH,INV-302,not-a-number,2026-09-12\n"
            "MAPLE,INV-203,100.00,2026-09-13\n"
        )
        result = importing.import_csv(self.db, csv_data, 'invoices')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['errors'][0]['line'], 3)
        self.assertIn('amount must be a positive decimal', result['errors'][0]['reason'])

        # Verify valid rows were actually stored
        inv_numbers = {r['invoice_number'] for r in reporting.invoices(self.db)}
        self.assertIn('INV-103', inv_numbers)
        self.assertIn('INV-203', inv_numbers)
        self.assertNotIn('INV-302', inv_numbers)

    def test_existing_records_preserved_after_mixed_and_duplicate_imports(self):
        # Capture original seeded records
        orig_invoices = reporting.invoices(self.db)
        orig_overview = reporting.overview(self.db)

        # Run duplicate and conflicting imports
        csv_dup = (
            "customer_id,invoice_number,amount,due_date\n"
            "HARBOR,INV-100,1250.00,2026-09-01\n"  # identical to seed
            "MAPLE,INV-200,9999.00,2026-09-02\n"   # conflict with seed
            "NORTH,INV-999,invalid,2026-09-03\n"    # invalid format
        )
        res = importing.import_csv(self.db, csv_dup, 'invoices')
        self.assertEqual(res['imported'], 0)
        self.assertEqual(res['skipped'], 1)
        self.assertEqual(res['rejected'], 2)

        # Check that original 6 seed invoices are completely preserved
        curr_invoices = reporting.invoices(self.db)
        self.assertEqual(len(curr_invoices), len(orig_invoices))
        for orig in orig_invoices:
            curr = next(r for r in curr_invoices if r['id'] == orig['id'])
            self.assertEqual(curr['customer_id'], orig['customer_id'])
            self.assertEqual(curr['invoice_number'], orig['invoice_number'])
            self.assertEqual(curr['amount'], orig['amount'])
            self.assertEqual(curr['due_date'], orig['due_date'])
            self.assertEqual(curr['paid'], orig['paid'])
            self.assertEqual(curr['balance'], orig['balance'])
            self.assertEqual(curr['status'], orig['status'])

        curr_overview = reporting.overview(self.db)
        self.assertEqual(curr_overview['summary']['outstanding'], orig_overview['summary']['outstanding'])
        self.assertEqual(curr_overview['summary']['invoice_count'], orig_overview['summary']['invoice_count'])

    # --- Improvement: Customer Filtering ---

    def test_customer_filter_all_returns_baseline_invoices(self):
        all_inv = reporting.invoices(self.db, status='all')
        filtered_none = reporting.invoices(self.db, status='all', customer_id=None)
        filtered_empty = reporting.invoices(self.db, status='all', customer_id='')
        self.assertEqual(len(all_inv), 6)
        self.assertEqual(len(filtered_none), 6)
        self.assertEqual(len(filtered_empty), 6)

    def test_customer_filter_by_specific_customer(self):
        harbor_inv = reporting.invoices(self.db, status='all', customer_id='HARBOR')
        self.assertEqual(len(harbor_inv), 2)
        self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in harbor_inv))

        maple_inv = reporting.invoices(self.db, status='all', customer_id='MAPLE')
        self.assertEqual(len(maple_inv), 2)
        self.assertTrue(all(r['customer_id'] == 'MAPLE' for r in maple_inv))

        north_inv = reporting.invoices(self.db, status='all', customer_id='NORTH')
        self.assertEqual(len(north_inv), 2)
        self.assertTrue(all(r['customer_id'] == 'NORTH' for r in north_inv))

    def test_customer_filter_and_status_combined(self):
        # In seeded demo:
        # HARBOR has INV-100 (open, 1250) and INV-101 (paid, 300)
        open_harbor = reporting.invoices(self.db, status='open', customer_id='HARBOR')
        self.assertEqual(len(open_harbor), 1)
        self.assertEqual(open_harbor[0]['invoice_number'], 'INV-100')
        self.assertEqual(open_harbor[0]['status'], 'open')

        paid_harbor = reporting.invoices(self.db, status='paid', customer_id='HARBOR')
        self.assertEqual(len(paid_harbor), 1)
        self.assertEqual(paid_harbor[0]['invoice_number'], 'INV-101')
        self.assertEqual(paid_harbor[0]['status'], 'paid')

    def test_customer_filter_unknown_customer_returns_empty(self):
        unknown_inv = reporting.invoices(self.db, status='all', customer_id='UNKNOWN_CUST')
        self.assertEqual(unknown_inv, [])


class HttpApiImportTests(unittest.TestCase):
    def setUp(self):
        import json
        import threading
        from ledger.http_app import make_server

        self.json = json
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'http_test.sqlite3'
        self.web_dir = Path(__file__).resolve().parent.parent / 'web'
        db = storage.connect(self.db_path)
        storage.seed(db)
        db.close()

        self.server = make_server(self.db_path, self.web_dir, 0)
        self.port = self.server.server_port
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _post_csv(self, kind, csv_content, origin=None):
        import urllib.request
        import urllib.error

        url = f'http://127.0.0.1:{self.port}/api/import?kind={kind}'
        req = urllib.request.Request(
            url,
            data=csv_content.encode('utf-8'),
            headers={
                'Content-Type': 'text/csv',
                'Origin': origin or f'http://127.0.0.1:{self.port}',
            },
            method='POST'
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                body = self.json.loads(resp.read().decode('utf-8'))
                return status, body
        except urllib.error.HTTPError as err:
            with err:
                status = err.code
                body = self.json.loads(err.read().decode('utf-8'))
                return status, body

    def test_http_import_success(self):
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,HTTP-1,50.00,2026-09-20\n"
        status, body = self._post_csv('invoices', csv_data)
        self.assertEqual(status, 200)
        self.assertEqual(body['imported'], 1)
        self.assertEqual(body['skipped'], 0)
        self.assertEqual(body['rejected'], 0)
        self.assertEqual(body['errors'], [])

    def test_http_import_duplicate_skipped(self):
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,HTTP-2,60.00,2026-09-20\n"
        status1, body1 = self._post_csv('invoices', csv_data)
        self.assertEqual(status1, 200)
        self.assertEqual(body1['imported'], 1)

        status2, body2 = self._post_csv('invoices', csv_data)
        self.assertEqual(status2, 200)
        self.assertEqual(body2['imported'], 0)
        self.assertEqual(body2['skipped'], 1)
        self.assertEqual(body2['rejected'], 0)

    def test_http_import_partial_rejection(self):
        # 2 valid rows, 1 malformed row
        csv_data = (
            "customer_id,invoice_number,amount,due_date\n"
            "HARBOR,HTTP-103,84.00,2026-09-12\n"
            "NORTH,HTTP-302,not-a-number,2026-09-12\n"
            "MAPLE,HTTP-203,100.00,2026-09-13\n"
        )
        status, body = self._post_csv('invoices', csv_data)
        self.assertEqual(status, 200)
        self.assertEqual(body['imported'], 2)
        self.assertEqual(body['skipped'], 0)
        self.assertEqual(body['rejected'], 1)
        self.assertEqual(len(body['errors']), 1)
        self.assertEqual(body['errors'][0]['line'], 3)
        self.assertIn('amount must be a positive decimal', body['errors'][0]['reason'])

    def test_http_import_wrong_header_fails_with_400(self):
        csv_data = "customer,invoice,value\nHARBOR,INV-110,50.00\n"
        status, body = self._post_csv('invoices', csv_data)
        self.assertEqual(status, 400)
        self.assertIn('Expected CSV header', body['error'])

    def test_http_import_forbidden_origin(self):
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,HTTP-403,50.00,2026-09-20\n"
        status, body = self._post_csv('invoices', csv_data, origin='http://malicious-site.com')
        self.assertEqual(status, 403)
        self.assertIn('error', body)

    def test_http_invoices_customer_filter(self):
        import urllib.request
        # HARBOR filter
        url = f'http://127.0.0.1:{self.port}/api/invoices?customer_id=HARBOR'
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            rows = self.json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in rows))

        # Combined status=open & customer_id=HARBOR
        url_combined = f'http://127.0.0.1:{self.port}/api/invoices?status=open&customer_id=HARBOR'
        with urllib.request.urlopen(url_combined) as resp:
            self.assertEqual(resp.status, 200)
            rows = self.json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['invoice_number'], 'INV-100')

        # Unknown customer
        url_unknown = f'http://127.0.0.1:{self.port}/api/invoices?customer_id=UNKNOWN'
        with urllib.request.urlopen(url_unknown) as resp:
            self.assertEqual(resp.status, 200)
            rows = self.json.loads(resp.read().decode('utf-8'))
            self.assertEqual(rows, [])

    def test_http_customers_list(self):
        import urllib.request
        url = f'http://127.0.0.1:{self.port}/api/customers'
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            custs = self.json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(custs), 3)
            self.assertEqual([c['customer_id'] for c in custs], ['HARBOR', 'MAPLE', 'NORTH'])


if __name__ == '__main__':
    unittest.main()
