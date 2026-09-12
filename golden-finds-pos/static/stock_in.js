const scannerInput = document.getElementById('scanner-input');
const scanMessage = document.getElementById('scan-message');
const stockInForm = document.getElementById('stock-in-form');
const productNameEl = document.getElementById('product-name');
const quantityEl = document.getElementById('quantity');
const costPriceEl = document.getElementById('cost-price');
const expiryField = document.getElementById('expiry-field');
const expiryDateEl = document.getElementById('expiry-date');
const submitBtn = document.getElementById('submit-stock-in');

let currentProduct = null;

document.addEventListener('click', (e) => {
    if (e.target.closest('#stock-in-form')) return;
    scannerInput.focus();
});

scannerInput.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
        const barcode = scannerInput.value.trim();
        scannerInput.value = '';
        if (barcode) await handleScan(barcode);
    }
});

async function handleScan(barcode) {
    try {
        const res = await fetch(`/api/product/${encodeURIComponent(barcode)}`);
        if (res.status === 404) {
            // Unknown barcode - go register it first, same as the sell screen does
            window.location.href = `/add-product?barcode=${encodeURIComponent(barcode)}`;
            return;
        }
        currentProduct = await res.json();
        showProductForm(currentProduct);
    } catch (err) {
        showMessage('Something went wrong looking that up.', 'error');
    }
}

function showProductForm(product) {
    productNameEl.textContent = `${product.name} (currently ${product.stock_quantity} in stock)`;
    stockInForm.style.display = 'block';
    expiryField.style.display = product.track_expiry ? 'block' : 'none';
    quantityEl.value = '';
    costPriceEl.value = '';
    expiryDateEl.value = '';
    scanMessage.textContent = '';
    quantityEl.focus();
}

function showMessage(text, type) {
    scanMessage.textContent = text;
    scanMessage.className = `scan-message ${type}`;
}

submitBtn.addEventListener('click', async () => {
    if (!currentProduct) return;

    const quantity = parseInt(quantityEl.value, 10);
    const costPrice = parseFloat(costPriceEl.value);
    const expiryDate = currentProduct.track_expiry ? expiryDateEl.value : null;

    if (!quantity || quantity <= 0) {
        showMessage('Enter a valid quantity.', 'error');
        return;
    }
    if (!costPrice || costPrice <= 0) {
        showMessage('Enter a valid cost price.', 'error');
        return;
    }
    if (currentProduct.track_expiry && !expiryDate) {
        showMessage('This product needs an expiry date.', 'error');
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    try {
        const res = await fetch('/api/stock-in', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_id: currentProduct.id,
                quantity: quantity,
                cost_price: costPrice,
                expiry_date: expiryDate
            })
        });
        const result = await res.json();

        if (result.success) {
            showMessage(`Added ${quantity} units of ${currentProduct.name}.`, 'success');
            stockInForm.style.display = 'none';
            currentProduct = null;
            scannerInput.focus();
        } else {
            showMessage(result.message, 'error');
        }
    } catch (err) {
        showMessage('Stock-in failed - check your connection.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Add Stock';
    }
});
