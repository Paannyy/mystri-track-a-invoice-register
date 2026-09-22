const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
const money = n => currency.format(n);
const text = (tag, value, className = '') => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};

async function refresh() {
  const status = document.querySelector('#status').value;
  const customerFilter = document.querySelector('#customer-filter');
  const customerId = customerFilter ? customerFilter.value : '';
  const invoiceUrl = customerId
    ? `/api/invoices?status=${status}&customer_id=${encodeURIComponent(customerId)}`
    : `/api/invoices?status=${status}`;

  const responses = await Promise.all([fetch('/api/overview'), fetch(invoiceUrl)]);
  if (responses.some(r => !r.ok)) throw new Error('Could not refresh the register.');
  const [data, rows] = await Promise.all(responses.map(r => r.json()));

  if (customerFilter && data.customers && customerFilter.options.length <= 1) {
    data.customers.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.customer_id;
      opt.textContent = `${c.name} (${c.customer_id})`;
      customerFilter.append(opt);
    });
    if (customerId) {
      customerFilter.value = customerId;
    }
  }

  document.querySelector('#invoice-count').textContent = data.summary.invoice_count;
  document.querySelector('#open-count').textContent = data.summary.open_count;
  document.querySelector('#outstanding').textContent = money(data.summary.outstanding);
  const body = document.querySelector('#invoices');
  body.replaceChildren();
  rows.forEach(r => {
    const row = document.createElement('tr');
    [r.customer_name, r.invoice_number, r.due_date].forEach(v => row.append(text('td', v)));
    [r.amount, r.paid, r.balance].forEach(v => row.append(text('td', money(v), 'number')));
    row.append(text('td', r.status));
    body.append(row);
  });
  const unmatched = document.querySelector('#unmatched');
  unmatched.replaceChildren(...data.unmatched_payments.map(p => text('li', `${p.payment_id} · ${p.customer_id} / ${p.invoice_number} · ${money(p.amount)}`)));
  if (!data.unmatched_payments.length) unmatched.append(text('li', 'No unmatched payments.'));
  document.querySelector('#page-error').textContent = '';
}

function formatImportResult(data) {
  const statusHeader = data.rejected > 0
    ? 'Import completed with rejected rows.'
    : 'Import completed.';
  let message = `${statusHeader}\nImported: ${data.imported}\nSkipped: ${data.skipped}\nRejected: ${data.rejected}`;
  if (data.errors && data.errors.length > 0) {
    message += '\n\nRejected rows:';
    data.errors.forEach(err => {
      message += `\nLine ${err.line} — ${err.reason}`;
    });
  }
  return message;
}

async function submitImport(form) {
  const feedback = form.querySelector('.feedback');
  const button = form.querySelector('button');
  const fileInput = form.querySelector('input');
  if (!fileInput.files || !fileInput.files[0]) {
    feedback.textContent = 'Please choose a CSV file to import.';
    return;
  }
  button.disabled = true;
  feedback.textContent = 'Importing…';
  try {
    const csv = await fileInput.files[0].text();
    const response = await fetch(`/api/import?kind=${form.dataset.kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'text/csv' },
      body: csv,
    });

    let data;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      const errorMsg = (data && data.error) ? data.error : `HTTP ${response.status} ${response.statusText}`;
      feedback.textContent = `Import failed: ${errorMsg}`;
      return;
    }

    feedback.textContent = formatImportResult(data);
    await refresh();
  } catch (error) {
    feedback.textContent = `Import failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

document.querySelector('#status').addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
const customerSelect = document.querySelector('#customer-filter');
if (customerSelect) {
  customerSelect.addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
}
document.querySelectorAll('form[data-kind]').forEach(form => form.addEventListener('submit', e => { e.preventDefault(); submitImport(form); }));
refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; });
