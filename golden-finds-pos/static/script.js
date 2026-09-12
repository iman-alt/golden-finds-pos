const scannerInput = document.getElementById('scanner-input');
const scanMessage = document.getElementById('scan-message');
const searchResults = document.getElementById('search-results');
const cartItemsEl = document.getElementById('cart-items');
const cartTotalEl = document.getElementById('cart-total');
const checkoutBtn = document.getElementById('checkout-btn');
const paymentMethodEl = document.getElementById('payment-method');

// Cart is kept as a simple in-memory list here - each entry tracks
// everything needed to render and to send to the server on checkout.
let cart = [];

// Keep the scanner input focused no matter where the page is clicked,
// so a physical scan always lands in the right place.
document.addEventListener('click', (e) => {
    if (e.target.closest('.cart-item-qty-controls') || e.target.closest('.cart-item-remove')) return;
    scannerInput.focus();
});

let searchDebounce;
scannerInput.addEventListener('input', () => {
    const value = scannerInput.value.trim();
    clearTimeout(searchDebounce);

    // Barcodes are numeric and scanners send them fast; typed searches
    // are usually letters. We debounce typed search so we don't fire
    // a request on every keystroke.
    if (value.length >= 2 && !/^\d+$/.test(value)) {
        searchDebounce = setTimeout(() => runSearch(value), 250);
    } else {
        searchResults.innerHTML = '';
    }
});

scannerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        const value = scannerInput.value.trim();
        if (value) handleBarcodeScan(value);
        scannerInput.value = '';
        searchResults.innerHTML = '';
    }
});

async function handleBarcodeScan(barcode) {
    try {
        const res = await fetch(`/api/product/${encodeURIComponent(barcode)}`);
        if (res.status === 404) {
            // Unknown barcode - send straight to the add-product form,
            // prefilled, so nothing needs retyping.
            window.location.href = `/add-product?barcode=${encodeURIComponent(barcode)}`;
            return;
        }
        const product = await res.json();
        addToCart(product);
        showMessage(`Added: ${product.name}`, 'success');
    } catch (err) {
        showMessage('Something went wrong looking that up.', 'error');
    }
}

async function runSearch(query) {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const results = await res.json();

    searchResults.innerHTML = '';
    results.forEach(product => {
        const item = document.createElement('div');
        item.className = 'search-result-item';
        item.innerHTML = `<span>${product.name}</span><span>KSh ${product.retail_price}</span>`;
        item.addEventListener('click', async () => {
            // Reuse the same lookup as a scan so pricing/offer logic stays consistent
            await handleBarcodeScan(product.barcode);
            scannerInput.value = '';
            searchResults.innerHTML = '';
        });
        searchResults.appendChild(item);
    });
}

function addToCart(product) {
    const existing = cart.find(item => item.id === product.id);
    if (existing) {
        existing.quantity += 1;
    } else {
        cart.push({
            id: product.id,
            name: product.name,
            retail_price: product.retail_price,
            wholesale_price: product.wholesale_price,
            wholesale_min_qty: product.wholesale_min_qty,
            offer_price: product.offer_price,
            quantity: 1
        });
    }
    renderCart();
}

function changeQuantity(productId, delta) {
    const item = cart.find(i => i.id === productId);
    if (!item) return;
    item.quantity += delta;
    if (item.quantity <= 0) {
        cart = cart.filter(i => i.id !== productId);
    }
    renderCart();
}

function removeFromCart(productId) {
    cart = cart.filter(i => i.id !== productId);
    renderCart();
}

function priceForItem(item) {
    // Offer price always wins if one is active. Otherwise wholesale
    // kicks in automatically once quantity crosses the threshold.
    if (item.offer_price !== null && item.offer_price !== undefined) {
        return item.offer_price;
    }
    if (item.quantity >= item.wholesale_min_qty) {
        return item.wholesale_price;
    }
    return item.retail_price;
}

function renderCart() {
    if (cart.length === 0) {
        cartItemsEl.innerHTML = '<p class="empty-cart-msg">No items yet - scan something!</p>';
        cartTotalEl.textContent = 'KSh 0';
        checkoutBtn.disabled = true;
        return;
    }

    checkoutBtn.disabled = false;
    let total = 0;
    cartItemsEl.innerHTML = '';

    cart.forEach(item => {
        const unitPrice = priceForItem(item);
        const lineTotal = unitPrice * item.quantity;
        total += lineTotal;

        const row = document.createElement('div');
        row.className = 'cart-item';

        const offerTag = (item.offer_price !== null && item.offer_price !== undefined)
            ? `<span class="cart-item-offer">Offer price</span>` : '';

        row.innerHTML = `
            <div>
                <div class="cart-item-name">${item.name} ${offerTag}</div>
                <div class="cart-item-detail">${item.quantity} x KSh ${unitPrice} = KSh ${lineTotal}</div>
            </div>
            <div class="cart-item-qty-controls">
                <button data-action="minus">-</button>
                <span>${item.quantity}</span>
                <button data-action="plus">+</button>
                <button class="cart-item-remove" data-action="remove">&times;</button>
            </div>
        `;

        row.querySelector('[data-action="minus"]').addEventListener('click', () => changeQuantity(item.id, -1));
        row.querySelector('[data-action="plus"]').addEventListener('click', () => changeQuantity(item.id, 1));
        row.querySelector('[data-action="remove"]').addEventListener('click', () => removeFromCart(item.id));

        cartItemsEl.appendChild(row);
    });

    cartTotalEl.textContent = `KSh ${total.toLocaleString()}`;
}

function showMessage(text, type) {
    scanMessage.textContent = text;
    scanMessage.className = `scan-message ${type}`;
}

checkoutBtn.addEventListener('click', async () => {
    if (cart.length === 0) return;

    checkoutBtn.disabled = true;
    checkoutBtn.textContent = 'Processing...';

    const payload = {
        items: cart.map(item => ({ product_id: item.id, quantity: item.quantity })),
        payment_method: paymentMethodEl.value
    };

    try {
        const res = await fetch('/api/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();

        if (result.success) {
            showMessage(`Sale complete! Sale #${result.sale_id}`, 'success');
            cart = [];
            renderCart();
        } else {
            showMessage(result.message, 'error');
        }
    } catch (err) {
        showMessage('Checkout failed - check your connection.', 'error');
    } finally {
        checkoutBtn.disabled = false;
        checkoutBtn.textContent = 'Complete Sale';
        scannerInput.focus();
    }
});

renderCart();
