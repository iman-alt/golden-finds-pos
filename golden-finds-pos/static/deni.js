/*
 * Record deni: find the person by phone, pick the item, see the total.
 * The server decides the price; this page only shows what it will charge.
 */

const csrf = document.querySelector('meta[name="csrf-token"]').content;
const $ = (id) => document.getElementById(id);

const phoneEl = $('deni-phone');
const nameEl = $('deni-name');
const knownEl = $('deni-known');
const searchEl = $('deni-item-search');
const resultsEl = $('deni-item-results');
const chosenEl = $('deni-item-chosen');
const productIdEl = $('deni-product-id');
const qtyEl = $('deni-qty');
const totalBox = $('deni-total-box');
const debtors = JSON.parse($('debtor-data').textContent);

let product = null;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function pic(className, imageUrl, icon) {
    const node = el('span', className);
    if (imageUrl) {
        const img = document.createElement('img');
        img.src = imageUrl;
        img.alt = '';
        img.onerror = () => { node.replaceChildren(); node.textContent = icon || '📦'; };
        node.appendChild(img);
    } else {
        node.textContent = icon || '📦';
    }
    return node;
}

// ----------------------------------------------------- the person -- //

function normalise(raw) {
    let digits = (raw || '').replace(/\D/g, '');
    if (digits.startsWith('254') && digits.length === 12) digits = '0' + digits.slice(3);
    else if (digits.length === 9 && /^[17]/.test(digits)) digits = '0' + digits;
    return digits;
}

// Someone who already owes is recognised by their number, so their name
// fills itself in and the book stays under one person.
phoneEl.addEventListener('input', () => {
    const match = debtors.find(d => d.phone === normalise(phoneEl.value));
    if (match) {
        if (!nameEl.value.trim()) nameEl.value = match.name;
        knownEl.textContent = `${match.name} already owes ${match.owed}.`;
        knownEl.hidden = false;
    } else {
        knownEl.hidden = true;
    }
});

// ------------------------------------------------------- the item -- //

let debounce;
searchEl.addEventListener('input', () => {
    clearTimeout(debounce);
    const query = searchEl.value.trim();
    if (query.length < 2) { resultsEl.replaceChildren(); return; }
    debounce = setTimeout(() => search(query), 220);
});

searchEl.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();          // never submit the form from this box
    const query = searchEl.value.trim();
    if (!query) return;
    // A scanned barcode goes straight to that item.
    const res = await fetch(`/api/product/${encodeURIComponent(query)}`);
    if (res.ok) choose(await res.json());
});

async function search(query) {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    if (!res.ok) return;
    const items = await res.json();
    resultsEl.replaceChildren();
    items.forEach(item => {
        const row = el('button', 'search-result-item');
        row.type = 'button';
        row.append(pic('search-result-icon', item.image_url, item.icon),
                   el('span', '', item.name),
                   el('span', 'search-result-meta',
                      `${item.retail_price_display} · ${item.stock_quantity} left`));
        row.addEventListener('click', () => choose(item));
        resultsEl.appendChild(row);
    });
}

function choose(item) {
    product = item;
    productIdEl.value = item.id;
    resultsEl.replaceChildren();
    searchEl.value = '';
    searchEl.hidden = true;

    const change = el('button', 'btn-link', 'Change');
    change.type = 'button';
    change.addEventListener('click', () => {
        product = null;
        productIdEl.value = '';
        chosenEl.hidden = true;
        totalBox.hidden = true;
        searchEl.hidden = false;
        searchEl.focus();
    });

    chosenEl.replaceChildren(
        pic('line-ico', item.image_url, item.icon),
        (() => {
            const body = el('div');
            body.append(el('div', 'line-name', item.name),
                        el('div', 'line-detail', `${item.stock_quantity} on the shelf`));
            return body;
        })(),
        change,
    );
    chosenEl.hidden = false;
    qtyEl.focus();
    reprice();
}

document.querySelectorAll('.qty-field button').forEach(btn => {
    btn.addEventListener('click', () => {
        const next = Math.max(1, (parseInt(qtyEl.value, 10) || 1) + parseInt(btn.dataset.step, 10));
        qtyEl.value = next;
        reprice();
    });
});
qtyEl.addEventListener('input', reprice);

async function reprice() {
    const quantity = parseInt(qtyEl.value, 10);
    if (!product || !quantity || quantity < 1) { totalBox.hidden = true; return; }
    try {
        const res = await fetch('/api/cart/price', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({ items: [{ product_id: product.id, quantity }] }),
        });
        const data = await res.json();
        if (!res.ok || !data.lines) { totalBox.hidden = true; return; }
        const line = data.lines[0];
        const basis = line.price_basis === 'retail' ? '' :
            (line.price_basis === 'offer' ? ' (offer price)' : ' (wholesale price)');
        $('deni-total-detail').textContent = `${quantity} × ${line.unit_price_display}${basis}`;
        $('deni-total').textContent = line.line_total_display;
        totalBox.hidden = false;
    } catch (err) {
        totalBox.hidden = true;
    }
}

$('deni-form').addEventListener('submit', (e) => {
    if (!productIdEl.value) {
        e.preventDefault();
        searchEl.focus();
        searchEl.placeholder = 'Choose the item first…';
    }
});

// Coming back after an error with an item already chosen: show it again.
if (productIdEl.value) {
    fetch('/api/cart/price', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ items: [{ product_id: Number(productIdEl.value), quantity: 1 }] }),
    }).then(r => r.json()).then(data => {
        if (!data.lines) return;
        const line = data.lines[0];
        choose({ id: line.product_id, name: line.name, icon: line.icon,
                 image_url: line.image_url, stock_quantity: line.stock_quantity });
    }).catch(() => {});
}
phoneEl.dispatchEvent(new Event('input'));
