import csv
import io


def customers(db):
    return [dict(r) for r in db.execute('SELECT customer_id, name FROM customers ORDER BY customer_id')]


def invoices(db, status='all', customer_id=None):
    if status not in ('all', 'open', 'paid'):
        raise ValueError('status must be all, open or paid')
    query = '''
        SELECT i.id, i.customer_id, c.name AS customer_name, i.invoice_number,
               i.amount, i.due_date, COALESCE(SUM(p.amount), 0) AS paid
        FROM invoices i JOIN customers c ON c.customer_id=i.customer_id
        LEFT JOIN payments p ON p.invoice_id=i.id
    '''
    params = []
    if customer_id:
        query += ' WHERE i.customer_id = ?'
        params.append(customer_id)
    query += ' GROUP BY i.id ORDER BY i.id'
    data = db.execute(query, params).fetchall()
    result = []
    for row in data:
        item = dict(row)
        item['amount'] = round(float(item['amount']), 2)
        item['paid'] = round(float(item['paid']), 2)
        balance = round(item['amount'] - item['paid'], 2)
        if balance == 0.0:
            balance = 0.0
        item['balance'] = balance
        item['status'] = 'paid' if balance <= 0 else 'open'
        result.append(item)
    if status != 'all':
        result = [r for r in result if r['status'] == status]
    return result


def overview(db):
    rows = invoices(db)
    unmatched = [dict(r) for r in db.execute('''SELECT payment_id, customer_id,
        invoice_number, amount FROM payments WHERE invoice_id IS NULL ORDER BY payment_id''')]
    return {'invoices': rows, 'unmatched_payments': unmatched, 'customers': customers(db), 'summary': {
        'invoice_count': len(rows),
        'open_count': sum(r['status'] == 'open' for r in rows),
        'outstanding': round(sum(max(0, r['balance']) for r in rows), 2),
    }}


def export_csv(db):
    output = io.StringIO(newline='')
    fields = ['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in invoices(db):
        item = {k: row[k] for k in fields}
        for key in ('amount', 'paid', 'balance'):
            val = round(float(item[key]), 2)
            if val == 0.0:
                val = 0.0
            item[key] = f"{val:.2f}"
        writer.writerow(item)
    return output.getvalue()
