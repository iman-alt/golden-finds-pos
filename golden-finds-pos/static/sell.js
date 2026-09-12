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
const isOwner = document.querySelector('meta[name="user-role"]').content === 'admin';

const scannerInput = document.getElementById('scanner-input');
const scanMessage = document.getElementById('scan-message');
const searchResults = document.getElementById('search-results');
const cartItemsEl = document.getElementById('cart-items');
const cartTotalEl = document.getElementById('cart-total');
const checkoutBtn = document.getElementById('checkout-btn');
const clearCartBtn = document.getElementById('clear-cart');
const cashRow = document.getElementById('cash-row');
const amountPaidEl = document.getElementById('amount-paid');
const clearTenderBtn = document.getElementById('clear-tender');
const exactBtn = document.getElementById('exact-btn');
const changeBox = document.getElementById('change-box');
const changeTotalEl = document.getElementById('change-total');
const changeReceivedEl = document.getElementById('change-received');
const changeLabelEl = document.getElementById('change-label');
const changeAmountEl = document.getElementById('change-amount');
const changeResultRow = document.getElementById('change-result-row');
const saleDone = document.getElementById('sale-done');

let cart = [];              // [{ id, quantity }]
let pricedLines = [];       // server's answer, rendered as-is
let subtotalCents = 0;
let paymentMethod = 'cash';
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
        (paymentMethod === 'cash' ? amountPaidEl : checkoutBtn).focus();
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
            if (!isOwner) {
                // A shopkeeper cannot add products, so sending them to a
                // form they are not allowed to submit would just be a
                // dead end. Tell them what to do instead.
                showMessage(
                    `Barcode ${barcode} isn't in the system yet. ` +
                    `Set it aside and ask Mum to add it.`,
                    'warning',
                );
                return;
            }
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

            const icon = document.createElement('span');
            icon.className = 'search-result-icon';
            icon.textContent = product.icon || '📦';
            icon.setAttribute('aria-hidden', 'true');

            const meta = document.createElement('span');
            meta.className = 'search-result-meta';
            meta.textContent = `${product.retail_price_display} · ${product.stock_quantity} in stock`;

            item.append(icon, name, meta);
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

        // The icon comes from the server so that the cart, the search
        // list and the pairing prompt all show the same one.
        const icon = document.createElement('span');
        icon.className = 'cart-item-icon';
        icon.textContent = line.icon || '📦';
        icon.setAttribute('aria-hidden', 'true');

        const left = document.createElement('div');
        left.className = 'cart-item-body';
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

        row.append(icon, left, controls);
        cartItemsEl.appendChild(row);
    });

    cartTotalEl.textContent = money(subtotalCents);
    checkoutBtn.disabled = busy;
    updateChange();
    refreshPairings();
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

// ------------------------------------------------------------- pairings --

/*
 * "Goes well with" - what customers who bought the last item usually took
 * with it, from the shop's own receipts. A prompt to offer something, not
 * an instruction: one tap adds it, ignoring it costs nothing.
 *
 * The person serving can hide this strip for good, and the choice is
 * remembered on their machine.
 */
const pairingStrip = document.getElementById('pairing-strip');
const pairingList = document.getElementById('pairing-list');
const pairingHide = document.getElementById('pairing-hide');

function pairingsHidden() {
    try {
        return localStorage.getItem('hidePairings') === '1';
    } catch (err) {
        return false;  // private window or blocked storage: just show them
    }
}

if (pairingHide) {
    pairingHide.addEventListener('click', () => {
        try {
            localStorage.setItem('hidePairings', '1');
        } catch (err) { /* nothing to do; it stays hidden for this visit */ }
        pairingStrip.hidden = true;
    });
}

async function refreshPairings() {
    if (!pairingStrip || pairingsHidden()) return;

    if (cart.length === 0) {
        pairingStrip.hidden = true;
        return;
    }

    // Suggest against the item added most recently - that is what the
    // customer is holding, and what the prompt should follow.
    const last = cart[cart.length - 1].id;
    try {
        const res = await fetch(`/api/pairings/${last}`);
        if (!res.ok) return;
        const suggestions = (await res.json())
            .filter(s => !cart.some(item => item.id === s.id));

        if (suggestions.length === 0) {
            pairingStrip.hidden = true;
            return;
        }

        pairingList.replaceChildren();
        suggestions.forEach(s => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'pairing-chip';
            chip.append(
                Object.assign(document.createElement('span'), {
                    className: 'pairing-chip-icon', textContent: s.icon || '📦',
                }),
                Object.assign(document.createElement('span'), {
                    textContent: s.name,
                }),
                Object.assign(document.createElement('span'), {
                    className: 'pairing-chip-price', textContent: s.price_display,
                }),
            );
            chip.addEventListener('click', () => addToCart(s.id));
            pairingList.appendChild(chip);
        });
        pairingStrip.hidden = false;
    } catch (err) {
        /* a missing suggestion is not worth interrupting a sale over */
    }
}

clearCartBtn.addEventListener('click', () => {
    cart = [];
    repriceAndRender();
    scannerInput.focus();
});

// --------------------------------------------------------------- payment --

document.querySelectorAll('.pay-option').forEach(option => {
    option.addEventListener('click', () => {
        document.querySelectorAll('.pay-option')
            .forEach(o => o.classList.remove('selected'));
        option.classList.add('selected');
        paymentMethod = option.dataset.method;

        // M-Pesa is paid on the phone, so there is no change to work out.
        cashRow.hidden = paymentMethod !== 'cash';
        updateChange();
    });
});

/*
 * The denomination buttons add up, because that is how money arrives at
 * a counter - a 500 and two 100s, not "seven hundred". Tapping is also
 * faster and harder to fat-finger than typing.
 */
document.querySelectorAll('.denom[data-add]').forEach(button => {
    button.addEventListener('click', () => {
        const current = parseInt(amountPaidEl.value || '0', 10) || 0;
        amountPaidEl.value = current + parseInt(button.dataset.add, 10);
        updateChange();
    });
});

exactBtn.addEventListener('click', () => {
    amountPaidEl.value = Math.round(subtotalCents / 100);
    updateChange();
});

clearTenderBtn.addEventListener('click', () => {
    amountPaidEl.value = '';
    updateChange();
    amountPaidEl.focus();
});

amountPaidEl.addEventListener('input', updateChange);

function updateChange() {
    const received = Math.round(parseFloat(amountPaidEl.value || '0') * 100);

    if (subtotalCents === 0 || paymentMethod !== 'cash' || !received) {
        changeBox.hidden = true;
        return;
    }

    changeBox.hidden = false;
    changeTotalEl.textContent = money(subtotalCents);
    changeReceivedEl.textContent = money(received);

    if (received < subtotalCents) {
        changeLabelEl.textContent = 'Still owing';
        changeAmountEl.textContent = money(subtotalCents - received);
        changeResultRow.className = 'change-line change-line-result short';
    } else {
        changeLabelEl.textContent = 'Balance to give';
        changeAmountEl.textContent = money(received - subtotalCents);
        changeResultRow.className = 'change-line change-line-result';
    }
}

// -------------------------------------------------------------- checkout --

checkoutBtn.addEventListener('click', async () => {
    if (cart.length === 0 || busy) return;

    busy = true;
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = 'Saving...';

    try {
        const { ok, data } = await postJSON('/api/checkout', {
            items: cart.map(i => ({ product_id: i.id, quantity: i.quantity })),
            payment_method: paymentMethod,
            amount_paid: amountPaidEl.value || null,
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
    amountPaidEl.value = '';
    updateChange();
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
