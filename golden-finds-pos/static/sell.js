/*
 * The till.
 *
 * The cart holds product ids and quantities and nothing else. Every price
 * on this screen comes back from /api/cart/price, so what the cashier
 * reads out to the customer is what the server will charge. The old
 * version priced the cart in the browser, which is how it ended up
 * quoting an offer price while recording retail.
 */

const csrf = document.querySelector('meta[name="csrf-token"]').content;

const scannerInput = document.getElementById('scanner-input');
const scanMessage = document.getElementById('scan-message');
const searchResults = document.getElementById('search-results');
const cartItemsEl = document.getElementById('cart-items');
const cartTotalEl = document.getElementById('cart-total');
const checkoutBtn = document.getElementById('checkout-btn');
const clearCartBtn = document.getElementById('clear-cart');
const paymentMethodEl = document.getElementById('payment-method');
const cashRow = document.getElementById('cash-row');
const amountPaidEl = document.getElementById('amount-paid');
const changeDueEl = document.getElementById('change-due');
const customerRow = document.getElementById('customer-row');
const customerSearchEl = document.getElementById('customer-search');
const customerResults = document.getElementById('customer-results');
const customerChosenEl = document.getElementById('customer-chosen');
const saleDone = document.getElementById('sale-done');

let cart = [];              // [{ id, quantity }]
let pricedLines = [];       // server's answer, rendered as-is
let subtotalCents = 0;
let selectedCustomer = null;
let busy = false;

// ---------------------------------------------------------------- utils --

async function postJSON(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(body),
    });
    if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('signed out');
    }
    return { ok: res.ok, data: await res.json().catch(() => ({})) };
}

function showMessage(text, type = 'info') {
    scanMessage.textContent = text;
    scanMessage.className = `scan-message ${type}`;
}

function money(cents) {
    return 'KSh ' + (cents / 100).toLocaleString('en-KE', {
        minimumFractionDigits: cents % 100 ? 2 : 0,
        maximumFractionDigits: 2,
    });
}

// ------------------------------------------------------------- scanning --

let searchDebounce;
scannerInput.addEventListener('input', () => {
    const value = scannerInput.value.trim();
    clearTimeout(searchDebounce);

    // A scanner delivers a barcode in one burst and presses Enter itself.
    // A person types slowly, so only typed text triggers a search.
    if (value.length >= 2) {
        searchDebounce = setTimeout(() => runSearch(value), 200);
    } else {
        searchResults.replaceChildren();
    }
});

scannerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        const value = scannerInput.value.trim();
        clearTimeout(searchDebounce);
        if (value) lookupAndAdd(value);
        scannerInput.value = '';
        searchResults.replaceChildren();
    } else if (e.key === 'Escape') {
        scannerInput.value = '';
        searchResults.replaceChildren();
    }
});

// F2 jumps to payment without reaching for the mouse.
document.addEventListener('keydown', (e) => {
    if (e.key === 'F2' && cart.length) {
        e.preventDefault();
        (paymentMethodEl.value === 'cash' ? amountPaidEl : checkoutBtn).focus();
    }
});

// Keep focus in the scanner box wherever the page is clicked, so a scan
// always lands somewhere useful - but never steal it from a real control.
document.addEventListener('click', (e) => {
    if (e.target.closest('button, input, select, a, .modal')) return;
    scannerInput.focus();
});

async function lookupAndAdd(barcode) {
    try {
        const res = await fetch(`/api/product/${encodeURIComponent(barcode)}`);
        if (res.status === 401) { window.location.href = '/login'; return; }
        if (res.status === 404) {
            // Unknown barcode: straight to registration with it prefilled.
            window.location.href = `/add-product?barcode=${encodeURIComponent(barcode)}`;
            return;
        }
        const product = await res.json();

        if (product.stock_quantity <= 0) {
            showMessage(`${product.name} shows no stock. Check the shelf.`, 'warning');
        }
        addToCart(product.id);
        showMessage(`Added ${product.name}`, 'success');
    } catch (err) {
        showMessage('Could not look that up. Check the connection.', 'error');
    }
}

async function runSearch(query) {
    try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        if (!res.ok) return;
        const results = await res.json();

        searchResults.replaceChildren();
        results.forEach(product => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'search-result-item';

            // textContent, never innerHTML - a product name is user input
            // and must never be able to inject markup into this page.
            const name = document.createElement('span');
            name.textContent = product.name;

            const meta = document.createElement('span');
            meta.className = 'search-result-meta';
            meta.textContent = `${product.retail_price_display} · ${product.stock_quantity} in stock`;

            item.append(name, meta);
            item.addEventListener('click', () => {
                addToCart(product.id);
                scannerInput.value = '';
                searchResults.replaceChildren();
                scannerInput.focus();
            });
            searchResults.appendChild(item);
        });
    } catch (err) {
        /* a failed search is not worth interrupting the cashier over */
    }
}

// ------------------------------------------------------------------ cart --

function addToCart(productId) {
    const existing = cart.find(i => i.id === productId);
    if (existing) existing.quantity += 1;
    else cart.push({ id: productId, quantity: 1 });
    repriceAndRender();
}

function changeQuantity(productId, delta) {
    const item = cart.find(i => i.id === productId);
    if (!item) return;
    item.quantity += delta;
    if (item.quantity <= 0) cart = cart.filter(i => i.id !== productId);
    repriceAndRender();
}

function removeFromCart(productId) {
    cart = cart.filter(i => i.id !== productId);
    repriceAndRender();
}

/* Asks the server what this cart costs, then draws its answer. */
async function repriceAndRender() {
    if (cart.length === 0) {
        pricedLines = [];
        subtotalCents = 0;
        render();
        return;
    }

    try {
        const { ok, data } = await postJSON('/api/cart/price', {
            items: cart.map(i => ({ product_id: i.id, quantity: i.quantity })),
        });
        if (!ok) {
            showMessage(data.message || 'Could not price the cart.', 'error');
            return;
        }
        pricedLines = data.lines;
        subtotalCents = data.subtotal_cents;
        render();
    } catch (err) {
        showMessage('Lost connection while pricing. Try again.', 'error');
    }
}

function render() {
    cartItemsEl.replaceChildren();
    clearCartBtn.hidden = cart.length === 0;

    if (pricedLines.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'empty-cart-msg';
        empty.textContent = 'Nothing yet - scan something.';
        cartItemsEl.appendChild(empty);
        cartTotalEl.textContent = money(0);
        checkoutBtn.disabled = true;
        updateChange();
        return;
    }

    pricedLines.forEach(line => {
        const row = document.createElement('div');
        row.className = 'cart-item';

        const left = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'cart-item-name';
        name.textContent = line.name;

        if (line.price_basis !== 'retail') {
            const tag = document.createElement('span');
            tag.className = `price-tag price-tag-${line.price_basis}`;
            tag.textContent = line.price_basis === 'offer' ? 'Offer' : 'Wholesale';
            name.appendChild(tag);
        }

        const detail = document.createElement('div');
        detail.className = 'cart-item-detail';
        detail.textContent =
            `${line.quantity} × ${line.unit_price_display} = ${line.line_total_display}`;

        // Warn rather than block: the shelf is the source of truth, and a
        // cashier with the item in hand should still be able to sell it.
        if (line.quantity > line.stock_quantity) {
            const warn = document.createElement('div');
            warn.className = 'cart-item-warning';
            warn.textContent = `Only ${line.stock_quantity} recorded in stock`;
            left.appendChild(warn);
        }

        left.prepend(name, detail);

        const controls = document.createElement('div');
        controls.className = 'cart-item-qty-controls';
        controls.append(
            button('−', 'Reduce quantity', () => changeQuantity(line.product_id, -1)),
            span(line.quantity),
            button('+', 'Increase quantity', () => changeQuantity(line.product_id, 1)),
            button('×', 'Remove', () => removeFromCart(line.product_id), 'cart-item-remove'),
        );

        row.append(left, controls);
        cartItemsEl.appendChild(row);
    });

    cartTotalEl.textContent = money(subtotalCents);
    checkoutBtn.disabled = busy;
    updateChange();
}

function button(label, title, onClick, extraClass = '') {
    const el = document.createElement('button');
    el.type = 'button';
    el.textContent = label;
    el.title = title;
    el.setAttribute('aria-label', title);
    if (extraClass) el.className = extraClass;
    el.addEventListener('click', onClick);
    return el;
}

function span(text) {
    const el = document.createElement('span');
    el.textContent = text;
    return el;
}

clearCartBtn.addEventListener('click', () => {
    cart = [];
    repriceAndRender();
    scannerInput.focus();
});

// --------------------------------------------------------------- payment --

paymentMethodEl.addEventListener('change', () => {
    const method = paymentMethodEl.value;
    cashRow.hidden = method !== 'cash';
    customerRow.hidden = method !== 'credit';
    if (method !== 'credit') {
        selectedCustomer = null;
        customerChosenEl.hidden = true;
    }
    updateChange();
});

amountPaidEl.addEventListener('input', updateChange);

function updateChange() {
    const paid = Math.round(parseFloat(amountPaidEl.value || '0') * 100);
    if (!paid || subtotalCents === 0 || paymentMethodEl.value !== 'cash') {
        changeDueEl.hidden = true;
        return;
    }
    changeDueEl.hidden = false;
    if (paid < subtotalCents) {
        changeDueEl.textContent = `Short by ${money(subtotalCents - paid)}`;
        changeDueEl.className = 'change-due short';
    } else {
        changeDueEl.textContent = `Change: ${money(paid - subtotalCents)}`;
        changeDueEl.className = 'change-due';
    }
}

let customerDebounce;
customerSearchEl.addEventListener('input', () => {
    clearTimeout(customerDebounce);
    const term = customerSearchEl.value.trim();
    if (term.length < 2) { customerResults.replaceChildren(); return; }

    customerDebounce = setTimeout(async () => {
        const res = await fetch(`/api/customers/search?q=${encodeURIComponent(term)}`);
        if (!res.ok) return;
        const people = await res.json();

        customerResults.replaceChildren();
        people.forEach(person => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'search-result-item';

            const name = document.createElement('span');
            name.textContent = person.name;
            const meta = document.createElement('span');
            meta.className = 'search-result-meta';
            meta.textContent = person.credit_balance_cents
                ? `owes ${person.credit_balance_display}` : 'no balance';

            item.append(name, meta);
            item.addEventListener('click', () => {
                selectedCustomer = person;
                customerChosenEl.textContent = `Charging to ${person.name}`;
                customerChosenEl.hidden = false;
                customerSearchEl.value = '';
                customerResults.replaceChildren();
            });
            customerResults.appendChild(item);
        });
    }, 200);
});

// -------------------------------------------------------------- checkout --

checkoutBtn.addEventListener('click', async () => {
    if (cart.length === 0 || busy) return;

    if (paymentMethodEl.value === 'credit' && !selectedCustomer) {
        showMessage('Choose the customer whose account this goes to.', 'error');
        customerSearchEl.focus();
        return;
    }

    busy = true;
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = 'Saving...';

    try {
        const { ok, data } = await postJSON('/api/checkout', {
            items: cart.map(i => ({ product_id: i.id, quantity: i.quantity })),
            payment_method: paymentMethodEl.value,
            amount_paid: amountPaidEl.value || null,
            customer_id: selectedCustomer ? selectedCustomer.id : null,
        });

        if (!ok) {
            showMessage(data.message || 'The sale was not saved.', 'error');
            return;
        }
        showSaleDone(data);
    } catch (err) {
        // Deliberately vague about whether it saved: the cashier should
        // check the sales list rather than ring it up a second time.
        showMessage(
            'Lost connection. Check the sales list before re-ringing this sale.',
            'error',
        );
    } finally {
        busy = false;
        checkoutBtn.disabled = cart.length === 0;
        checkoutBtn.textContent = 'Complete sale';
    }
});

function showSaleDone(result) {
    document.getElementById('done-receipt').textContent = `Receipt ${result.receipt_number} · ${result.total_display}`;

    const changeEl = document.getElementById('done-change');
    if (result.change_cents > 0) {
        changeEl.textContent = `Give ${result.change_display} change`;
        changeEl.hidden = false;
    } else {
        changeEl.hidden = true;
    }

    document.getElementById('done-print').href = `${result.receipt_url}?print=1`;
    saleDone.hidden = false;
    document.getElementById('done-next').focus();

    cart = [];
    selectedCustomer = null;
    amountPaidEl.value = '';
    customerChosenEl.hidden = true;
    repriceAndRender();
}

document.getElementById('done-next').addEventListener('click', () => {
    saleDone.hidden = true;
    scanMessage.textContent = '';
    scannerInput.focus();
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !saleDone.hidden) {
        saleDone.hidden = true;
        scannerInput.focus();
    }
});

render();
