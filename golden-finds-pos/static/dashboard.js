/*
 * Dashboard: the owner's offer picker and expired-stock write-off.
 *
 * Nothing here decides a price. It collects the price the owner picked
 * and sends it to an endpoint that only an owner can reach.
 */

const csrf = document.querySelector('meta[name="csrf-token"]').content;

function money(cents) {
    return 'KSh ' + (cents / 100).toLocaleString('en-KE', {
        minimumFractionDigits: cents % 100 ? 2 : 0,
        maximumFractionDigits: 2,
    });
}

async function postJSON(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(body || {}),
    });
    if (res.status === 401) { window.location.href = '/login'; throw new Error('signed out'); }
    return { ok: res.ok, data: await res.json().catch(() => ({})) };
}

document.querySelectorAll('.expiry-card').forEach(card => {
    const openBtn = card.querySelector('.btn-put-on-offer');
    const picker = card.querySelector('.offer-picker');
    const confirmBtn = card.querySelector('.btn-confirm-offer');
    const customInput = card.querySelector('.offer-custom-price');
    const errorEl = card.querySelector('.offer-error');
    const successEl = card.querySelector('.offer-success');
    const writeOffBtn = card.querySelector('.btn-write-off');

    let chosenCents = null;

    if (openBtn) {
        openBtn.addEventListener('click', () => {
            picker.hidden = !picker.hidden;
            openBtn.textContent = picker.hidden ? 'Put on offer' : 'Cancel';
        });
    }

    card.querySelectorAll('.offer-option').forEach(option => {
        option.addEventListener('click', () => {
            card.querySelectorAll('.offer-option')
                .forEach(o => o.classList.remove('selected'));
            option.classList.add('selected');
            chosenCents = parseInt(option.dataset.price, 10);
            customInput.value = '';
            errorEl.hidden = true;
        });
    });

    if (customInput) {
        customInput.addEventListener('input', () => {
            card.querySelectorAll('.offer-option')
                .forEach(o => o.classList.remove('selected'));
            const value = parseFloat(customInput.value);
            chosenCents = value > 0 ? Math.round(value * 100) : null;
            errorEl.hidden = true;
        });
    }

    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            if (!chosenCents) {
                errorEl.textContent = 'Pick one of the prices, or type your own.';
                errorEl.hidden = false;
                return;
            }

            const retail = parseInt(card.dataset.retailPrice, 10);
            if (chosenCents >= retail) {
                errorEl.textContent =
                    `An offer has to be below the normal price of ${money(retail)}.`;
                errorEl.hidden = false;
                return;
            }

            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Saving...';

            try {
                const { ok, data } = await postJSON('/api/offer', {
                    product_id: parseInt(card.dataset.productId, 10),
                    batch_id: parseInt(card.dataset.batchId, 10),
                    offer_price: (chosenCents / 100).toFixed(2),
                    tier: card.dataset.tier,
                });

                if (!ok) {
                    errorEl.textContent = data.message || 'Could not save the offer.';
                    errorEl.hidden = false;
                    return;
                }

                picker.hidden = true;
                openBtn.hidden = true;
                successEl.textContent =
                    `On offer at ${money(chosenCents)} — cashiers will now charge this.`;
                successEl.hidden = false;
            } catch (err) {
                errorEl.textContent = 'Could not save. Check the connection.';
                errorEl.hidden = false;
            } finally {
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Confirm offer';
            }
        });
    }

    if (writeOffBtn) {
        writeOffBtn.addEventListener('click', async () => {
            if (!confirm('Write this expired stock off? It will be removed from stock.')) return;

            writeOffBtn.disabled = true;
            try {
                const { ok, data } = await postJSON(
                    `/api/batch/${card.dataset.batchId}/write-off`, { note: 'expired' },
                );
                if (ok) {
                    writeOffBtn.hidden = true;
                    successEl.textContent = data.message;
                    successEl.hidden = false;
                } else {
                    writeOffBtn.disabled = false;
                    alert(data.message || 'Could not write it off.');
                }
            } catch (err) {
                writeOffBtn.disabled = false;
            }
        });
    }
});
