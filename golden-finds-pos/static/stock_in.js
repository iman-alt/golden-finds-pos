/*
 * Stock in: scan one pack, say how many came, add the expiry date, save.
 * Everything saved shows up in "Received today", so the owner can see
 * at a glance that it was logged.
 */

const csrf = document.querySelector('meta[name="csrf-token"]').content;
const $ = (id) => document.getElementById(id);

const scannerInput = $('scanner-input');
const scanMessage = $('scan-message');
const stockInForm = $('stock-in-form');
const quantityEl = $('quantity');
const costPriceEl = $('cost-price');
const costHintEl = $('cost-hint');
const expiryField = $('expiry-field');
const expiryDateEl = $('expiry-date');
const batchNumberEl = $('batch-number');
const submitBtn = $('submit-stock-in');
const logEl = $('received-log');

let currentProduct = null;
let justSaved = false;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function money(cents) {
    return 'KSh ' + (cents / 100).toLocaleString('en-KE', { maximumFractionDigits: 2 });
}

function showMessage(text, type = 'info') {
    scanMessage.textContent = text;
    scanMessage.className = `scan-message ${type}`;
}

document.addEventListener('click', (e) => {
    if (e.target.closest('button, input, select, a')) return;
    if (stockInForm.hidden) scannerInput.focus();
});

scannerInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const barcode = scannerInput.value.trim();
    scannerInput.value = '';
    if (barcode) handleScan(barcode);
});

async function handleScan(barcode) {
    try {
        const res = await fetch(`/api/product/${encodeURIComponent(barcode)}`);
        if (res.status === 401) { window.location.href = '/login'; return; }
        if (res.status === 404) {
            window.location.href = `/add-product?barcode=${encodeURIComponent(barcode)}`;
            return;
        }
        currentProduct = await res.json();
        showProductForm(currentProduct);
    } catch (err) {
        showMessage('Could not look that up. Check the connection.', 'error');
    }
}

function showProductForm(product) {
    $('product-icon').textContent = product.icon || '📦';
    $('product-name').textContent = product.name;

    const meta = $('product-meta');
    meta.replaceChildren(
        el('span', 'badge-ok', `${product.stock_quantity} on the shelf now`),
        el('span', '', `sells at ${money(product.retail_price_cents)}`),
    );
    if (product.track_expiry) meta.appendChild(el('span', 'badge-empty', '📅 has an expiry date'));

    stockInForm.hidden = false;
    $('stock-idle').hidden = true;

    // Restart the arrival animation for every scan.
    const card = document.querySelector('.stock-product');
    card.style.animation = 'none';
    void card.offsetWidth;
    card.style.animation = '';

    expiryField.hidden = !product.track_expiry;
    expiryDateEl.required = product.track_expiry;
    $('no-expiry-hint').hidden = product.track_expiry;
    $('edit-product-link').href = `/products/${product.id}/edit`;

    quantityEl.value = '';
    costPriceEl.value = '';
    expiryDateEl.value = '';
    batchNumberEl.value = '';
    costHintEl.textContent = '';
    scanMessage.textContent = '';
    quantityEl.focus();
}

document.querySelectorAll('.quick-qty button').forEach(btn => {
    btn.addEventListener('click', () => {
        quantityEl.value = btn.dataset.qty;
        costPriceEl.focus();
    });
});

costPriceEl.addEventListener('input', () => {
    if (!currentProduct) return;
    const entered = Math.round(parseFloat(costPriceEl.value || '0') * 100);
    if (!entered) { costHintEl.textContent = ''; return; }

    const retail = currentProduct.retail_price_cents;
    if (entered >= retail) {
        costHintEl.textContent = 'That is more than it sells for, so every sale would lose money.';
        costHintEl.className = 'field-hint negative';
    } else {
        costHintEl.textContent =
            `You make ${money(retail - entered)} on each one (${Math.round((retail - entered) / retail * 100)}%).`;
        costHintEl.className = 'field-hint';
    }
});

[quantityEl, costPriceEl, expiryDateEl, batchNumberEl].forEach((field, index, all) => {
    field.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const next = all.slice(index + 1).find(c => !c.closest('[hidden]'));
        (next || submitBtn).focus();
    });
});

function resetForm() {
    stockInForm.hidden = true;
    $('stock-idle').hidden = false;
    currentProduct = null;
    scannerInput.focus();
}

$('cancel-stock-in').addEventListener('click', () => {
    scanMessage.textContent = '';
    resetForm();
});

submitBtn.addEventListener('click', async () => {
    if (!currentProduct) return;

    const quantity = parseInt(quantityEl.value, 10);
    const costPrice = costPriceEl.value;
    const expiryDate = currentProduct.track_expiry ? expiryDateEl.value : null;

    if (!quantity || quantity <= 0) {
        showMessage('How many arrived?', 'error'); quantityEl.focus(); return;
    }
    if (!costPrice || parseFloat(costPrice) <= 0) {
        showMessage('What did you pay for each one?', 'error'); costPriceEl.focus(); return;
    }
    if (currentProduct.track_expiry && !expiryDate) {
        showMessage('Add the expiry date from the pack.', 'error'); expiryDateEl.focus(); return;
    }
    if (expiryDate && expiryDate < new Date().toISOString().slice(0, 10)) {
        if (!confirm('That date has already passed. Save it anyway?')) return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving…';

    try {
        const res = await fetch('/api/stock-in', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({
                product_id: currentProduct.id,
                quantity,
                cost_price: costPrice,
                expiry_date: expiryDate,
                batch_number: batchNumberEl.value || null,
            }),
        });
        if (res.status === 401) { window.location.href = '/login'; return; }
        const result = await res.json();

        if (result.success) {
            showMessage(`✅ ${result.message} Now ${result.stock_quantity} on the shelf.`, 'success');
            justSaved = true;
            resetForm();
            loadLog();
        } else {
            showMessage(result.message, 'error');
        }
    } catch (err) {
        showMessage('Could not save. Check the connection and try again.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = '✅ Save delivery';
    }
});

async function loadLog() {
    try {
        const res = await fetch('/api/stock-in/recent');
        if (!res.ok) return;
        const rows = await res.json();

        logEl.replaceChildren();
        if (rows.length === 0) {
            logEl.appendChild(el('p', 'empty-msg', '📭 Nothing received yet today.'));
            return;
        }
        rows.forEach((row, index) => {
            // Highlight the newest row right after a save.
            const item = el('div', 'received-row' + (index === 0 && justSaved ? ' fresh' : ''));
            const body = el('div');
            body.append(
                el('div', 'received-name', row.product_name),
                el('div', 'received-meta',
                   [row.time, row.user_name, row.expiry_date ? `expires ${row.expiry_date}` : null]
                       .filter(Boolean).join(' · ')),
            );
            item.append(el('span', 'line-ico', row.icon || '📦'), body,
                        el('span', 'received-qty', `+${row.quantity}`));
            logEl.appendChild(item);
        });
        justSaved = false;
    } catch (err) { /* the log is a convenience; the save already happened */ }
}

loadLog();

// Arriving here straight from adding a new product: scan it for them.
if (scannerInput.value.trim()) {
    handleScan(scannerInput.value.trim());
    scannerInput.value = '';
}
