/*
 * Stock in: scan, then record what arrived.
 */

const csrf = document.querySelector('meta[name="csrf-token"]').content;

const scannerInput = document.getElementById('scanner-input');
const scanMessage = document.getElementById('scan-message');
const stockInForm = document.getElementById('stock-in-form');
const productNameEl = document.getElementById('product-name');
const quantityEl = document.getElementById('quantity');
const costPriceEl = document.getElementById('cost-price');
const costHintEl = document.getElementById('cost-hint');
const expiryField = document.getElementById('expiry-field');
const expiryDateEl = document.getElementById('expiry-date');
const batchNumberEl = document.getElementById('batch-number');
const submitBtn = document.getElementById('submit-stock-in');

let currentProduct = null;

function showMessage(text, type = 'info') {
    scanMessage.textContent = text;
    scanMessage.className = `scan-message ${type}`;
}

document.addEventListener('click', (e) => {
    if (e.target.closest('button, input, select, a')) return;
    scannerInput.focus();
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
    productNameEl.textContent =
        `${product.name} — ${product.stock_quantity} in stock now`;
    stockInForm.hidden = false;
    expiryField.hidden = !product.track_expiry;
    expiryDateEl.required = product.track_expiry;

    quantityEl.value = '';
    costPriceEl.value = '';
    expiryDateEl.value = '';
    batchNumberEl.value = '';
    costHintEl.textContent = '';
    scanMessage.textContent = '';
    quantityEl.focus();
}

// Flag a cost that has moved sharply since last time - usually a typo,
// occasionally a real price rise the owner should notice either way.
costPriceEl.addEventListener('input', () => {
    if (!currentProduct) return;
    const entered = Math.round(parseFloat(costPriceEl.value || '0') * 100);
    if (!entered) { costHintEl.textContent = ''; return; }

    const retail = currentProduct.retail_price_cents;
    if (entered >= retail) {
        costHintEl.textContent =
            'That cost is at or above the retail price - every sale would lose money.';
        costHintEl.className = 'field-hint negative';
    } else {
        const margin = ((retail - entered) / retail * 100).toFixed(0);
        costHintEl.textContent = `Margin at the current retail price: ${margin}%`;
        costHintEl.className = 'field-hint';
    }
});

// Enter moves through the form rather than submitting it half-filled.
[quantityEl, costPriceEl, expiryDateEl, batchNumberEl].forEach((el, index, all) => {
    el.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const next = all.slice(index + 1).find(candidate => !candidate.closest('[hidden]'));
        (next || submitBtn).focus();
    });
});

submitBtn.addEventListener('click', async () => {
    if (!currentProduct) return;

    const quantity = parseInt(quantityEl.value, 10);
    const costPrice = costPriceEl.value;
    const expiryDate = currentProduct.track_expiry ? expiryDateEl.value : null;

    if (!quantity || quantity <= 0) {
        showMessage('Enter how many arrived.', 'error');
        quantityEl.focus();
        return;
    }
    if (!costPrice || parseFloat(costPrice) <= 0) {
        showMessage('Enter what each unit cost you.', 'error');
        costPriceEl.focus();
        return;
    }
    if (currentProduct.track_expiry && !expiryDate) {
        showMessage('This product needs an expiry date.', 'error');
        expiryDateEl.focus();
        return;
    }
    if (expiryDate && expiryDate < new Date().toISOString().slice(0, 10)) {
        if (!confirm('That expiry date is in the past. Add it anyway?')) return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

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
            showMessage(result.message, 'success');
            stockInForm.hidden = true;
            currentProduct = null;
            scannerInput.focus();
        } else {
            showMessage(result.message, 'error');
        }
    } catch (err) {
        showMessage('Could not save. Check the connection and try again.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Add stock';
    }
});

// Arriving here straight from registering a new product: scan it for them.
if (scannerInput.value.trim()) {
    handleScan(scannerInput.value.trim());
    scannerInput.value = '';
}
