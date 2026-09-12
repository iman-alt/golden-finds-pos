document.querySelectorAll('.expiry-card').forEach(card => {
    const putOnOfferBtn = card.querySelector('.btn-put-on-offer');
    const picker = card.querySelector('.offer-picker');
    const optionsContainer = card.querySelector('.offer-options');
    const customPriceInput = card.querySelector('.offer-custom-price');
    const errorEl = card.querySelector('.offer-error');
    const confirmBtn = card.querySelector('.btn-confirm-offer');
    const successEl = card.querySelector('.offer-success');

    const retailPrice = parseFloat(card.dataset.retailPrice);
    const productId = card.dataset.productId;
    const batchId = card.dataset.batchId;
    const tier = card.dataset.tier;

    putOnOfferBtn.addEventListener('click', () => {
        // Suggest a few discount levels off the current retail price -
        // the admin still has to pick one, nothing is auto-applied.
        const discounts = [10, 20, 30];
        optionsContainer.innerHTML = '';

        discounts.forEach(pct => {
            const price = Math.round(retailPrice * (1 - pct / 100));
            const label = document.createElement('label');
            label.className = 'offer-option';
            label.innerHTML = `
                <input type="radio" name="offer-${productId}-${batchId}" value="${price}">
                KSh ${price} <span class="offer-option-pct">(${pct}% off)</span>
            `;
            optionsContainer.appendChild(label);
        });

        picker.style.display = 'block';
        putOnOfferBtn.style.display = 'none';
    });

    confirmBtn.addEventListener('click', async () => {
        const selectedRadio = card.querySelector(`input[name="offer-${productId}-${batchId}"]:checked`);
        const customValue = parseFloat(customPriceInput.value);

        let offerPrice = null;
        if (selectedRadio) {
            offerPrice = parseFloat(selectedRadio.value);
        } else if (customValue && customValue > 0) {
            offerPrice = customValue;
        }

        if (!offerPrice) {
            errorEl.style.display = 'block';
            return;
        }
        errorEl.style.display = 'none';

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Saving...';

        try {
            const res = await fetch('/api/offer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    product_id: parseInt(productId, 10),
                    batch_id: batchId ? parseInt(batchId, 10) : null,
                    offer_price: offerPrice,
                    tier: tier
                })
            });
            const result = await res.json();

            if (result.success) {
                picker.style.display = 'none';
                successEl.style.display = 'block';
            } else {
                errorEl.textContent = result.message;
                errorEl.style.display = 'block';
            }
        } catch (err) {
            errorEl.textContent = 'Something went wrong - check your connection.';
            errorEl.style.display = 'block';
        } finally {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Confirm offer';
        }
    });
});
