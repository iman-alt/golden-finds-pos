/*
 * The till.
 *
 * Three steps, left to right: scan, items, payment.
 *
 * The cart holds product ids and quantities and nothing else. Every price
 * on this screen comes back from /api/cart/price, so what the cashier
 * reads out to the customer is what the server will charge.
 */

const csrf = document.querySelector('meta[name="csrf-token"]').content;
const isOwner = document.querySelector('meta[name="user-role"]').content === 'admin';
const $ = (id) => document.getElementById(id);

const scannerInput = $('scanner-input');
const scanMessage = $('scan-message');
const searchResults = $('search-results');
const cartItemsEl = $('cart-items');
const cartTotalEl = $('cart-total');
const itemCountEl = $('item-count');
const checkoutBtn = $('checkout-btn');
const clearCartBtn = $('clear-cart');
const cashRow = $('cash-row');
const mpesaNote = $('mpesa-note');
const amountPaidEl = $('amount-paid');
const changeBox = $('change-box');
const saleDone = $('sale-done');

let cart = [];              // [{ id, quantity }]
let pricedLines = [];       // server's answer, rendered as-is
let subtotalCents = 0;
let paymentMethod = 'cash';
let lastAddedId = null;
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

function money(cents) {
    return 'KSh ' + (cents / 100).toLocaleString('en-KE', {
        minimumFractionDigits: cents % 100 ? 2 : 0,
        maximumFractionDigits: 2,
    });
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    // textContent, never innerHTML: product names are user input.
    if (text !== undefined) node.textContent = text;
    return node;
}

function showMessage(text, type = 'info') {
    scanMessage.textContent = text;
    scanMessage.className = `scan-message ${type}`;
}

// ------------------------------------------------------------- scanning --

let searchDebounce;
scannerInput.addEventListener('input', () => {
    const value = scannerInput.value.trim();
    clearTimeout(searchDebounce);
    // A scanner types the whole barcode in a burst and presses Enter; a
    // person types slowly, so only slow typing gets a search list.
    if (value.length >= 2) {
        searchDebounce = setTimeout(() => runSearch(value), 220);
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

document.addEventListener('keydown', (e) => {
    if (e.key === 'F2' && cart.length) {
        e.preventDefault();
        (paymentMethod === 'cash' ? amountPaidEl : checkoutBtn).focus();
    }
});

// Keep focus in the scanner box, so a physical scan always lands there -
// but never steal it from a real control.
document.addEventListener('click', (e) => {
    if (e.target.closest('button, input, select, a, summary, .modal')) return;
    scannerInput.focus();
});

async function lookupAndAdd(barcode) {
    try {
        const res = await fetch(`/api/product/${encodeURIComponent(barcode)}`);
        if (res.status === 401) { window.location.href = '/login'; return; }
        if (res.status === 404) {
            if (!isOwner) {
                showMessage(
                    `Barcode ${barcode} isn't in the system yet. Set it aside and ask Mum to add it.`,
                    'warning',
                );
                return;
            }
            window.location.href = `/add-product?barcode=${encodeURIComponent(barcode)}`;
            return;
        }
        const product = await res.json();
        if (product.stock_quantity <= 0) {
            showMessage(`${product.name} shows no stock. Check the shelf.`, 'warning');
        } else {
            scanMessage.textContent = '';
        }
        spotlight(product);
        addToCart(product.id);
    } catch (err) {
        showMessage('Could not look that up. Check the connection.', 'error');
    }
}

function spotlight(product) {
    const price = product.offer_price_cents ?? product.retail_price_cents;
    $('spot-icon').textContent = product.icon || '📦';
    $('spot-name').textContent = product.name;
    $('spot-meta').textContent = product.offer_price_cents
        ? `On offer · ${product.stock_quantity} in stock`
        : `${product.stock_quantity} in stock`;
    $('spot-price').textContent = money(price);

    const card = $('spotlight');
    card.hidden = false;
    // Restart the pop animation on every scan, even of the same item.
    card.style.animation = 'none';
    void card.offsetWidth;
    card.style.animation = '';
    $('scan-idle').hidden = true;
}

async function runSearch(query) {
    try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        if (!res.ok) return;
        const results = await res.json();

        searchResults.replaceChildren();
        results.forEach((product, index) => {
            const item = el('button', 'search-result-item');
            item.type = 'button';
            item.style.animationDelay = `${index * 30}ms`;
            item.append(
                el('span', 'search-result-icon', product.icon || '📦'),
                el('span', '', product.name),
                el('span', 'search-result-meta',
                   `${product.retail_price_display} · ${product.stock_quantity} left`),
            );
            item.addEventListener('click', () => {
                scannerInput.value = '';
                searchResults.replaceChildren();
                lookupAndAdd(product.barcode);
                scannerInput.focus();
            });
            searchResults.appendChild(item);
        });
    } catch (err) { /* a failed search is not worth interrupting a sale */ }
}

// ------------------------------------------------------------------ cart --

function addToCart(productId) {
    const existing = cart.find(i => i.id === productId);
    if (existing) existing.quantity += 1;
    else cart.push({ id: productId, quantity: 1 });
    lastAddedId = productId;
    repriceAndRender();
}

function changeQuantity(productId, delta) {
    const item = cart.find(i => i.id === productId);
    if (!item) return;
    item.quantity += delta;
    if (item.quantity <= 0) cart = cart.filter(i => i.id !== productId);
    lastAddedId = null;
    repriceAndRender();
}

function removeFromCart(productId) {
    cart = cart.filter(i => i.id !== productId);
    lastAddedId = null;
    repriceAndRender();
}

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
        const empty = el('div', 'cart-empty');
        empty.append(el('span', '', '🛒'), el('strong', '', 'The list is empty'),
                     document.createTextNode('Scan an item and it appears here with its price.'));
        cartItemsEl.appendChild(empty);
        setTotal(0);
        itemCountEl.textContent = 'No items yet';
        checkoutBtn.disabled = true;
        $('spotlight').hidden = true;
        $('scan-idle').hidden = false;
        updateChange();
        refreshPairings();
        return;
    }

    let units = 0;
    pricedLines.forEach(line => {
        units += line.quantity;
        const row = el('div', 'line' + (line.product_id === lastAddedId ? ' line-new' : ''));

        const body = el('div');
        const name = el('div', 'line-name', line.name);
        if (line.price_basis !== 'retail') {
            name.appendChild(el('span', `price-tag price-tag-${line.price_basis}`,
                                line.price_basis === 'offer' ? 'Offer' : 'Wholesale'));
        }
        body.append(name, el('div', 'line-detail',
                             `${line.quantity} × ${line.unit_price_display}`));
        if (line.quantity > line.stock_quantity) {
            body.appendChild(el('div', 'line-warning',
                                `Only ${line.stock_quantity} recorded in stock`));
        }

        const qty = el('div', 'qty');
        qty.append(
            button('−', 'Reduce quantity', () => changeQuantity(line.product_id, -1)),
            el('span', '', String(line.quantity)),
            button('+', 'Increase quantity', () => changeQuantity(line.product_id, 1)),
            button('×', 'Remove', () => removeFromCart(line.product_id), 'qty-remove'),
        );

        row.append(el('span', 'line-ico', line.icon || '📦'), body, qty,
                   el('span', 'line-total', line.line_total_display));
        cartItemsEl.appendChild(row);
    });

    // Keep the newest line in view.
    cartItemsEl.lastElementChild?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

    setTotal(subtotalCents);
    itemCountEl.textContent =
        `${pricedLines.length} product${pricedLines.length === 1 ? '' : 's'} · ${units} item${units === 1 ? '' : 's'}`;
    updateChange();
    refreshPairings();
}

function setTotal(cents) {
    const text = money(cents);
    if (cartTotalEl.textContent !== text) {
        cartTotalEl.textContent = text;
        cartTotalEl.classList.remove('bump');
        void cartTotalEl.offsetWidth;
        cartTotalEl.classList.add('bump');
    }
}

function button(label, title, onClick, extraClass = '') {
    const node = el('button', extraClass, label);
    node.type = 'button';
    node.title = title;
    node.setAttribute('aria-label', title);
    node.addEventListener('click', onClick);
    return node;
}

clearCartBtn.addEventListener('click', () => {
    if (cart.length > 1 && !confirm('Clear every item from the list?')) return;
    cart = [];
    repriceAndRender();
    scannerInput.focus();
});

// ------------------------------------------------------------- pairings --

const pairingStrip = $('pairing-strip');
const pairingList = $('pairing-list');

function pairingsHidden() {
    try { return localStorage.getItem('hidePairings') === '1'; }
    catch (err) { return false; }
}

$('pairing-hide').addEventListener('click', () => {
    try { localStorage.setItem('hidePairings', '1'); } catch (err) { /* stays hidden this visit */ }
    pairingStrip.hidden = true;
});

async function refreshPairings() {
    if (pairingsHidden() || cart.length === 0) {
        pairingStrip.hidden = true;
        return;
    }
    const last = cart[cart.length - 1].id;
    try {
        const res = await fetch(`/api/pairings/${last}`);
        if (!res.ok) return;
        const suggestions = (await res.json()).filter(s => !cart.some(i => i.id === s.id));
        if (suggestions.length === 0) { pairingStrip.hidden = true; return; }

        pairingList.replaceChildren();
        suggestions.forEach(s => {
            const chip = el('button', 'pairing-chip');
            chip.type = 'button';
            chip.append(el('span', 'pairing-chip-icon', s.icon || '📦'),
                        el('span', '', s.name),
                        el('span', 'pairing-chip-price', s.price_display));
            chip.addEventListener('click', () => { lastAddedId = s.id; addToCart(s.id); });
            pairingList.appendChild(chip);
        });
        pairingStrip.hidden = false;
    } catch (err) { /* not worth interrupting a sale over */ }
}

// --------------------------------------------------------------- payment --

document.querySelectorAll('.pay-option').forEach(option => {
    option.addEventListener('click', () => {
        document.querySelectorAll('.pay-option').forEach(o => o.classList.remove('selected'));
        option.classList.add('selected');
        paymentMethod = option.dataset.method;
        cashRow.hidden = paymentMethod !== 'cash';
        mpesaNote.hidden = paymentMethod !== 'mpesa';
        updateChange();
    });
});

// Taps add up: a 500 and a 100 is 600.
document.querySelectorAll('.denom[data-add]').forEach(btn => {
    btn.addEventListener('click', () => {
        const current = parseInt(amountPaidEl.value || '0', 10) || 0;
        amountPaidEl.value = current + parseInt(btn.dataset.add, 10);
        updateChange();
    });
});

$('exact-btn').addEventListener('click', () => {
    amountPaidEl.value = Math.round(subtotalCents / 100);
    updateChange();
});

$('clear-tender').addEventListener('click', () => {
    amountPaidEl.value = '';
    updateChange();
    amountPaidEl.focus();
});

amountPaidEl.addEventListener('input', updateChange);

function receivedCents() {
    return Math.round((parseFloat(amountPaidEl.value || '0') || 0) * 100);
}

function updateChange() {
    const received = receivedCents();
    const short = paymentMethod === 'cash' && received > 0 && received < subtotalCents;

    checkoutBtn.disabled = busy || subtotalCents === 0 || short;

    if (subtotalCents === 0 || paymentMethod !== 'cash' || !received) {
        changeBox.hidden = true;
        return;
    }

    changeBox.hidden = false;
    $('change-total').textContent = money(subtotalCents);
    $('change-received').textContent = money(received);

    const resultRow = $('change-result-row');
    if (short) {
        $('change-label').textContent = 'Still owing';
        $('change-amount').textContent = money(subtotalCents - received);
        resultRow.className = 'balance-row balance-result short';
    } else {
        $('change-label').textContent = 'Balance to give';
        $('change-amount').textContent = money(received - subtotalCents);
        resultRow.className = 'balance-row balance-result';
    }
}

// -------------------------------------------------------------- checkout --

checkoutBtn.addEventListener('click', async () => {
    if (cart.length === 0 || busy) return;

    busy = true;
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = 'Saving…';

    try {
        const { ok, data } = await postJSON('/api/checkout', {
            items: cart.map(i => ({ product_id: i.id, quantity: i.quantity })),
            payment_method: paymentMethod,
            amount_paid: paymentMethod === 'cash' ? (amountPaidEl.value || null) : null,
        });
        if (!ok) {
            showMessage(data.message || 'The sale was not saved.', 'error');
            return;
        }
        showSaleDone(data);
    } catch (err) {
        showMessage('Lost connection. Check the sales list before ringing this up again.', 'error');
    } finally {
        busy = false;
        checkoutBtn.textContent = 'Complete sale';
        updateChange();
    }
});

function showSaleDone(result) {
    $('done-receipt').textContent = `Receipt ${result.receipt_number} · ${result.total_display}`;

    const changeEl = $('done-change');
    if (result.change_cents > 0) {
        changeEl.replaceChildren(document.createTextNode('Give the customer'),
                                 el('strong', '', result.change_display));
        changeEl.hidden = false;
    } else {
        changeEl.hidden = true;
    }

    $('done-print').href = `${result.receipt_url}?print=1`;
    saleDone.hidden = false;
    $('done-next').focus();
    celebrate();

    cart = [];
    amountPaidEl.value = '';
    lastAddedId = null;
    repriceAndRender();
}

function celebrate() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const colours = ['#f6c95c', '#e9a92b', '#e0725a', '#4f8f73', '#5b8cc2', '#8a6a9e'];
    for (let i = 0; i < 70; i++) {
        const bit = el('span', 'confetti');
        bit.style.left = `${Math.random() * 100}vw`;
        bit.style.background = colours[i % colours.length];
        bit.style.setProperty('--dx', `${(Math.random() - .5) * 200}px`);
        bit.style.setProperty('--spin', `${Math.random() * 720 - 360}deg`);
        bit.style.setProperty('--dur', `${1.4 + Math.random() * 1.2}s`);
        bit.style.animationDelay = `${Math.random() * .3}s`;
        if (i % 3 === 0) bit.style.borderRadius = '50%';
        document.body.appendChild(bit);
        setTimeout(() => bit.remove(), 3200);
    }
}

function closeDone() {
    saleDone.hidden = true;
    scanMessage.textContent = '';
    scannerInput.focus();
}

$('done-next').addEventListener('click', closeDone);
document.addEventListener('keydown', (e) => {
    if (!saleDone.hidden && (e.key === 'Escape' || e.key === 'Enter')) {
        e.preventDefault();
        closeDone();
    }
});

render();
