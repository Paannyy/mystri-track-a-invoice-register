"""Data preservation and register integrity verification against the owner's existing fixture."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger import storage, reporting, importing


class RegisterPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture_db_path = Path(__file__).resolve().parent.parent / 'fixtures' / 'existing-register.sqlite3'
        self.expected_json_path = Path(__file__).resolve().parent.parent / 'fixtures' / 'expected-records.json'

        # Create an isolated working copy in temporary directory
        self.db_path = Path(self.tmp.name) / 'preserved_register.sqlite3'
        shutil.copy2(self.fixture_db_path, self.db_path)

        # Connect and run startup seeding (must be idempotent and not alter existing fixture data)
        self.db = storage.connect(self.db_path)
        storage.seed(self.db)

        with open(self.expected_json_path, 'r', encoding='utf-8') as f:
            self.expected = json.load(f)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_baseline_fixture_counts_and_expected_records(self):
        """Verify the pristine fixture matches expected-records.json and starting totals exactly."""
        # Customers
        customers = self.db.execute('SELECT customer_id, name FROM customers ORDER BY customer_id').fetchall()
        self.assertEqual(len(customers), 3, "Fixture must contain exactly 3 customers")
        for expected_cust, actual_cust in zip(sorted(self.expected['customers'], key=lambda c: c['customer_id']), customers):
            self.assertEqual(actual_cust['customer_id'], expected_cust['customer_id'])
            self.assertEqual(actual_cust['name'], expected_cust['name'])

        # Invoices
        invoices = reporting.invoices(self.db, status='all')
        self.assertEqual(len(invoices), 9, "Fixture must contain exactly 9 invoices")
        for expected_inv, actual_inv in zip(self.expected['invoices'], invoices):
            self.assertEqual(actual_inv['id'], expected_inv['id'])
            self.assertEqual(actual_inv['customer_id'], expected_inv['customer_id'])
            self.assertEqual(actual_inv['invoice_number'], expected_inv['invoice_number'])
            self.assertEqual(f"{actual_inv['amount']:.2f}", expected_inv['amount'])
            self.assertEqual(actual_inv['due_date'], expected_inv['due_date'])

        # Payments
        payments = self.db.execute('SELECT payment_id, customer_id, invoice_number, amount, invoice_id FROM payments ORDER BY payment_id').fetchall()
        self.assertEqual(len(payments), 5, "Fixture must contain exactly 5 payments")
        for expected_pmt, actual_pmt in zip(sorted(self.expected['payments'], key=lambda p: p['payment_id']), payments):
            self.assertEqual(actual_pmt['payment_id'], expected_pmt['payment_id'])
            self.assertEqual(actual_pmt['customer_id'], expected_pmt['customer_id'])
            self.assertEqual(actual_pmt['invoice_number'], expected_pmt['invoice_number'])
            self.assertEqual(f"{actual_pmt['amount']:.2f}", expected_pmt['amount'])
            self.assertEqual(actual_pmt['invoice_id'], expected_pmt['invoice_id'])

        # Overview summary
        overview = reporting.overview(self.db)
        summary = overview['summary']
        self.assertEqual(summary['invoice_count'], 9, "Total invoice count must be 9")
        self.assertEqual(summary['open_count'], 7, "Open invoice count must be 7")
        self.assertEqual(summary['outstanding'], 3698.19, "Outstanding amount must be exactly 3698.19")

        # Open and paid breakdown
        open_invoices = reporting.invoices(self.db, status='open')
        self.assertEqual(len(open_invoices), 7, "Must have exactly 7 open invoices")
        paid_invoices = reporting.invoices(self.db, status='paid')
        self.assertEqual(len(paid_invoices), 2, "Must have exactly 2 paid invoices")

        # Unmatched payments
        unmatched = overview['unmatched_payments']
        self.assertEqual(len(unmatched), 1, "Must have exactly 1 unmatched payment")
        self.assertEqual(unmatched[0]['payment_id'], 'KEEP-U1')
        self.assertEqual(unmatched[0]['customer_id'], 'MAPLE')
        self.assertEqual(unmatched[0]['invoice_number'], 'WAIT-900')
        self.assertEqual(round(unmatched[0]['amount'], 2), 33.33)

    def test_records_preserved_after_import_workflow(self):
        """Verify that importing valid new records preserves existing fixture records and allocations."""
        csv_inv = "customer_id,invoice_number,amount,due_date\nHARBOR,NEW-999,50.00,2026-09-30\n"
        res_inv = importing.import_csv(self.db, csv_inv, 'invoices')
        self.assertEqual(res_inv['imported'], 1)

        csv_pmt = "payment_id,customer_id,invoice_number,amount\nPAY-999,HARBOR,NEW-999,50.00\n"
        res_pmt = importing.import_csv(self.db, csv_pmt, 'payments')
        self.assertEqual(res_pmt['imported'], 1)

        # Verify all 9 original invoices still exist and retain identical details
        current_invoices = {r['id']: r for r in reporting.invoices(self.db, status='all')}
        self.assertEqual(len(current_invoices), 10)  # 9 original + 1 new
        for exp in self.expected['invoices']:
            curr = current_invoices[exp['id']]
            self.assertEqual(curr['customer_id'], exp['customer_id'])
            self.assertEqual(curr['invoice_number'], exp['invoice_number'])
            self.assertEqual(f"{curr['amount']:.2f}", exp['amount'])
            self.assertEqual(curr['due_date'], exp['due_date'])

        # Verify all 5 original payments still exist and retain allocations
        current_payments = {r['payment_id']: r for r in self.db.execute('SELECT * FROM payments').fetchall()}
        self.assertEqual(len(current_payments), 6)  # 5 original + 1 new
        for exp in self.expected['payments']:
            curr = current_payments[exp['payment_id']]
            self.assertEqual(curr['customer_id'], exp['customer_id'])
            self.assertEqual(curr['invoice_number'], exp['invoice_number'])
            self.assertEqual(f"{curr['amount']:.2f}", exp['amount'])
            self.assertEqual(curr['invoice_id'], exp['invoice_id'])

    def test_duplicate_imports_do_not_create_records(self):
        """Exact duplicates of existing fixture records must be skipped without changing totals."""
        csv_inv = "customer_id,invoice_number,amount,due_date\nHARBOR,KEEP-700,456.78,2026-09-09\n"
        res_inv = importing.import_csv(self.db, csv_inv, 'invoices')
        self.assertEqual(res_inv['imported'], 0)
        self.assertEqual(res_inv['skipped'], 1)
        self.assertEqual(res_inv['rejected'], 0)

        csv_pmt = "payment_id,customer_id,invoice_number,amount\nKEEP-P1,HARBOR,KEEP-700,56.78\n"
        res_pmt = importing.import_csv(self.db, csv_pmt, 'payments')
        self.assertEqual(res_pmt['imported'], 0)
        self.assertEqual(res_pmt['skipped'], 1)
        self.assertEqual(res_pmt['rejected'], 0)

        summary = reporting.overview(self.db)['summary']
        self.assertEqual(summary['invoice_count'], 9)
        self.assertEqual(summary['open_count'], 7)
        self.assertEqual(summary['outstanding'], 3698.19)

    def test_conflicting_duplicate_does_not_overwrite(self):
        """Conflicting duplicates must be rejected and preserve original records."""
        csv_conflict = "customer_id,invoice_number,amount,due_date\nHARBOR,KEEP-700,9999.00,2026-09-09\n"
        res = importing.import_csv(self.db, csv_conflict, 'invoices')
        self.assertEqual(res['imported'], 0)
        self.assertEqual(res['skipped'], 0)
        self.assertEqual(res['rejected'], 1)

        # Original KEEP-700 must remain unchanged
        keep_700 = next(r for r in reporting.invoices(self.db, status='all')
                        if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'KEEP-700')
        self.assertEqual(keep_700['amount'], 456.78)
        self.assertEqual(keep_700['paid'], 56.78)
        self.assertEqual(keep_700['balance'], 400.00)
        self.assertEqual(keep_700['status'], 'open')

    def test_invalid_rows_do_not_alter_existing_records(self):
        """Invalid CSV rows must be rejected and not corrupt existing database records."""
        csv_invalid = (
            "customer_id,invoice_number,amount,due_date\n"
            "HARBOR,INV-999,not-a-number,2026-09-12\n"
            "UNKNOWN_CUST,INV-888,100.00,2026-09-12\n"
        )
        res = importing.import_csv(self.db, csv_invalid, 'invoices')
        self.assertEqual(res['imported'], 0)
        self.assertEqual(res['rejected'], 2)

        summary = reporting.overview(self.db)['summary']
        self.assertEqual(summary['invoice_count'], 9)
        self.assertEqual(summary['open_count'], 7)
        self.assertEqual(summary['outstanding'], 3698.19)

    def test_persistence_after_db_reconnect_and_restart(self):
        """Verify records survive database reconnect and simulated app restart."""
        # Add new record
        importing.import_csv(self.db, "customer_id,invoice_number,amount,due_date\nNORTH,PERSIST-1,75.00,2026-09-25\n", 'invoices')

        # Close connection
        self.db.close()

        # Reconnect (simulating server restart)
        self.db = storage.connect(self.db_path)
        storage.seed(self.db)

        # Check total count is 10 (9 original + 1 new)
        invoices = reporting.invoices(self.db, status='all')
        self.assertEqual(len(invoices), 10)
        self.assertIn('PERSIST-1', {r['invoice_number'] for r in invoices})
        self.assertIn('KEEP-700', {r['invoice_number'] for r in invoices})


if __name__ == '__main__':
    unittest.main()
