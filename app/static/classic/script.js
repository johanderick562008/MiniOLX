// ---------- API helper ----------
// Every call to our JSON API goes through apiRequest(), so the CSRF header
// and error handling are written once.

function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
}

function errorMessage(data, status) {
    if (data && typeof data.detail === "string") return data.detail;      // our HTTPException messages
    if (data && Array.isArray(data.detail)) {                              // Pydantic 422 validation errors
        return data.detail.map((err) => {
            const msg = err.msg.replace(/^Value error, /, "");
            const field = err.loc && err.loc.length > 1 ? err.loc[err.loc.length - 1] : null;
            return field ? `${field.replace(/_/g, " ")}: ${msg}` : msg;
        }).join(" ");
    }
    return `Something went wrong (error ${status}). Try again.`;
}

async function apiRequest(url, { method = "GET", body, signal } = {}) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();

    // FormData (forms with files) is sent as multipart; the browser sets the
    // Content-Type itself. Everything else is sent as JSON.
    let payload;
    if (body instanceof FormData) {
        payload = body;
    } else if (body !== undefined) {
        headers["Content-Type"] = "application/json";
        payload = JSON.stringify(body);
    }

    const response = await fetch(url, {
        method,
        headers,
        body: payload,
        credentials: "same-origin",   // send the session cookie
        signal,                       // lets a newer search cancel this one
    });

    let data = null;
    if (response.status !== 204) {
        try { data = await response.json(); } catch { /* no JSON body */ }
    }
    if (!response.ok) throw new Error(errorMessage(data, response.status));
    return data;
}

// textContent (not innerHTML) so server text is never interpreted as HTML.
function showMessage(element, text, type = "error") {
    element.textContent = text;
    element.className = `message ${type}`;
    element.hidden = false;
}

function formToObject(form) {
    return Object.fromEntries(new FormData(form).entries());
}

// Wires up a form: sends it to `url`, then runs onSuccess or shows the error.
function handleForm(formId, url, onSuccess) {
    const form = document.getElementById(formId);
    if (!form) return;
    const messageBox = document.getElementById("form-message");
    const button = form.querySelector('button[type="submit"]');

    form.addEventListener("submit", async (event) => {
        event.preventDefault();          // stop the normal page-reload submit
        messageBox.hidden = true;
        button.disabled = true;
        try {
            const data = await apiRequest(url, { method: "POST", body: formToObject(form) });
            onSuccess(data);
        } catch (error) {
            showMessage(messageBox, error.message);
            button.disabled = false;
        }
    });
}

// ---------- page wiring ----------

handleForm("register-form", "/api/auth/register", () => {
    window.location.href = "/login?registered=1";
});

handleForm("login-form", "/api/auth/login", () => {
    window.location.href = "/dashboard";
});

const logoutButton = document.getElementById("logout-btn");
if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
        logoutButton.disabled = true;
        try {
            await apiRequest("/api/auth/logout", { method: "POST" });
        } finally {
            window.location.href = "/";
        }
    });
}

// ---------- products (Phase 3) ----------

const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const productForm = document.getElementById("product-form");

if (productForm) {
    const messageBox = document.getElementById("form-message");
    const imageInput = productForm.querySelector('input[name="image"]');
    const preview = document.getElementById("image-preview");
    const button = productForm.querySelector('button[type="submit"]');

    // Quick checks for a better experience. The server re-checks everything.
    imageInput.addEventListener("change", () => {
        const file = imageInput.files[0];
        if (!file) return;
        if (file.size > MAX_IMAGE_BYTES) {
            showMessage(messageBox, "Image must be 2 MB or smaller.");
            imageInput.value = "";
            return;
        }
        messageBox.hidden = true;
        preview.src = URL.createObjectURL(file);
        preview.hidden = false;
    });

    productForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        messageBox.hidden = true;
        button.disabled = true;

        const formData = new FormData(productForm);
        if (!imageInput.files.length) formData.delete("image");   // editing without a new photo

        try {
            const product = await apiRequest(productForm.dataset.url, {
                method: productForm.dataset.method,
                body: formData,
            });
            window.location.href = `/product/${product.id}`;
        } catch (error) {
            showMessage(messageBox, error.message);
            button.disabled = false;
        }
    });
}

const deleteButton = document.getElementById("delete-product-btn");
if (deleteButton) {
    deleteButton.addEventListener("click", async () => {
        if (!confirm("Delete this listing? This can't be undone.")) return;
        deleteButton.disabled = true;
        try {
            await apiRequest(`/api/products/${deleteButton.dataset.productId}`, { method: "DELETE" });
            window.location.href = "/dashboard";
        } catch (error) {
            showMessage(document.getElementById("form-message"), error.message);
            deleteButton.disabled = false;
        }
    });
}

// ---------- live search (Phase 4) ----------
// The filter form already works on its own (a normal GET request that
// reloads the page). This upgrades it: results update as you type, using
// GET /api/products and building the cards from the JSON.

const filterForm = document.getElementById("filter-form");

if (filterForm) {
    const grid = document.getElementById("product-grid");
    const countText = document.getElementById("result-count");
    const emptyState = document.getElementById("empty-state");
    const filterMessage = document.getElementById("filter-message");
    const priceFormat = new Intl.NumberFormat("en-IN", {
        style: "currency", currency: "INR", minimumFractionDigits: 0, maximumFractionDigits: 2,
    });
    let debounceTimer;
    let inFlight;   // the AbortController of the request currently running

    // Only non-empty fields go into the URL: ?search=keyboard&max_price=3000
    function queryFromForm() {
        const params = new URLSearchParams();
        for (const [key, value] of new FormData(filterForm)) {
            if (value.trim() !== "" && !(key === "sort" && value === "newest")) params.set(key, value.trim());
        }
        return params;
    }

    // Built with createElement + textContent: a product named
    // "<img src=x onerror=alert(1)>" is displayed as plain text.
    function buildCard(product) {
        const card = document.createElement("a");
        card.className = "product-card";
        card.href = `/product/${product.id}`;

        const img = document.createElement("img");
        img.src = product.image_url;
        img.alt = product.name;
        img.loading = "lazy";

        const body = document.createElement("div");
        body.className = "product-card-body";

        const title = document.createElement("h3");
        title.textContent = product.name;
        const price = document.createElement("p");
        price.className = "price";
        price.textContent = priceFormat.format(Number(product.price));
        const meta = document.createElement("p");
        meta.className = "meta";
        meta.textContent = `${product.condition_label} · Seller: ${product.seller_username}`;
        const view = document.createElement("span");
        view.className = "view-link";
        view.textContent = "View product";

        body.append(title, price, meta, view);
        card.append(img, body);
        return card;
    }

    async function refreshResults() {
        const params = queryFromForm();
        // Keep the address bar in sync, so refresh/share/back show the same results.
        history.replaceState(null, "", params.toString() ? `/?${params}` : "/");

        // If the user is still typing, cancel the previous request so an old,
        // slower response can't overwrite newer results.
        if (inFlight) inFlight.abort();
        inFlight = new AbortController();
        grid.classList.add("loading");

        try {
            const products = await apiRequest(`/api/products?${params}`, { signal: inFlight.signal });
            filterMessage.hidden = true;
            grid.replaceChildren(...products.map(buildCard));
            const n = products.length;
            countText.textContent = `${n} item${n === 1 ? "" : "s"}${params.toString() ? " found" : ""}`;
            emptyState.hidden = n > 0;
            if (n === 0) {
                emptyState.replaceChildren();
                const p = document.createElement("p");
                p.textContent = params.toString() ? "No items match these filters." : "Nothing is listed yet.";
                emptyState.append(p);
            }
        } catch (error) {
            if (error.name === "AbortError") return;   // replaced by a newer search
            showMessage(filterMessage, error.message);
        } finally {
            grid.classList.remove("loading");
        }
    }

    // Typing: wait until the user pauses for 300 ms instead of one request per key.
    filterForm.addEventListener("input", (event) => {
        if (event.target.tagName === "SELECT") return;   // handled by "change"
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(refreshResults, 300);
    });
    filterForm.addEventListener("change", (event) => {
        if (event.target.tagName === "SELECT") refreshResults();
    });
    filterForm.addEventListener("submit", (event) => {
        event.preventDefault();
        clearTimeout(debounceTimer);
        refreshResults();
    });
}

// ---------- cart (Phase 5) ----------

const rupees = new Intl.NumberFormat("en-IN", {
    style: "currency", currency: "INR", minimumFractionDigits: 0, maximumFractionDigits: 2,
});
function formatINR(value) { return rupees.format(Number(value)); }

function setCartCount(count) {
    const badge = document.getElementById("cart-count");
    if (badge) badge.textContent = count;
}

// Product page: Add to cart / Buy now
const addToCartForm = document.getElementById("add-to-cart-form");
if (addToCartForm) {
    const messageBox = document.getElementById("form-message");
    const buyNowButton = document.getElementById("buy-now-btn");
    const productId = Number(addToCartForm.dataset.productId);

    async function addToCart(buyNow) {
        messageBox.hidden = true;
        const quantity = Number(addToCartForm.quantity.value);
        const cart = await apiRequest("/api/cart/items", {
            method: "POST",
            body: { product_id: productId, quantity, buy_now: buyNow },
        });
        setCartCount(cart.item_count);
        return cart;
    }

    addToCartForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            await addToCart(false);
            showMessage(messageBox, "Added to your cart.", "success");
        } catch (error) {
            showMessage(messageBox, error.message);
        }
    });

    buyNowButton.addEventListener("click", async () => {
        try {
            await addToCart(true);
            window.location.href = "/cart";
        } catch (error) {
            showMessage(messageBox, error.message);
        }
    });
}

// Cart page: change quantities, remove items
const cartList = document.getElementById("cart-items");
if (cartList) {
    const messageBox = document.getElementById("cart-message");

    // Same structure as templates/_cart_row.html, built safely with textContent.
    function buildCartRow(item) {
        const row = document.createElement("li");
        row.className = "cart-row";
        row.dataset.itemId = item.id;

        const img = document.createElement("img");
        img.src = item.image_url;
        img.alt = "";

        const info = document.createElement("div");
        info.className = "cart-row-info";
        const name = document.createElement("a");
        name.className = "name";
        name.href = `/product/${item.product_id}`;
        name.textContent = item.name;
        const each = document.createElement("p");
        each.className = "meta";
        each.textContent = `${formatINR(item.unit_price)} each`;
        info.append(name, each);
        if (item.problem) {
            const problem = document.createElement("p");
            problem.className = "problem";
            problem.textContent = item.problem;
            info.append(problem);
        }

        const qty = document.createElement("div");
        qty.className = "qty-control";
        const minus = document.createElement("button");
        minus.type = "button";
        minus.dataset.action = "decrease";
        minus.setAttribute("aria-label", "Decrease quantity");
        minus.textContent = "−";
        minus.disabled = item.quantity <= 1;
        const input = document.createElement("input");
        input.type = "number";
        input.min = 1;
        input.max = Math.max(item.available, 1);
        input.value = item.quantity;
        input.setAttribute("aria-label", "Quantity");
        const plus = document.createElement("button");
        plus.type = "button";
        plus.dataset.action = "increase";
        plus.setAttribute("aria-label", "Increase quantity");
        plus.textContent = "+";
        plus.disabled = item.quantity >= item.available;
        qty.append(minus, input, plus);

        const lineTotal = document.createElement("p");
        lineTotal.className = "line-total";
        lineTotal.textContent = formatINR(item.line_total);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "link-button";
        remove.dataset.action = "remove";
        remove.textContent = "Remove";

        row.append(img, info, qty, lineTotal, remove);
        return row;
    }

    // Redraw everything from the cart JSON the server sent back.
    function renderCart(cart) {
        cartList.replaceChildren(...cart.items.map(buildCartRow));
        document.getElementById("cart-item-count").textContent = cart.item_count;
        document.getElementById("cart-subtotal").textContent = formatINR(cart.subtotal);
        document.getElementById("cart-total").textContent = formatINR(cart.total);
        document.getElementById("cart-blocked").hidden = cart.can_checkout;
        const checkoutLink = document.getElementById("checkout-btn");
        if (cart.can_checkout) checkoutLink.removeAttribute("aria-disabled");
        else checkoutLink.setAttribute("aria-disabled", "true");
        document.getElementById("cart-layout").hidden = cart.items.length === 0;
        document.getElementById("cart-empty").hidden = cart.items.length > 0;
        setCartCount(cart.item_count);
    }

    async function changeCart(url, options) {
        messageBox.hidden = true;
        cartList.classList.add("busy");
        try {
            renderCart(await apiRequest(url, options));
        } catch (error) {
            showMessage(messageBox, error.message);
            // Put the page back in sync with what the server really has.
            try { renderCart(await apiRequest("/api/cart")); } catch { /* keep message */ }
        } finally {
            cartList.classList.remove("busy");
        }
    }

    function setQuantity(itemId, quantity) {
        return changeCart(`/api/cart/items/${itemId}`, { method: "PUT", body: { quantity } });
    }

    // One listener for the whole list ("event delegation"): it keeps working
    // for rows that are redrawn later.
    cartList.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;
        const row = button.closest(".cart-row");
        const itemId = row.dataset.itemId;
        const current = Number(row.querySelector("input").value);

        if (button.dataset.action === "increase") setQuantity(itemId, current + 1);
        if (button.dataset.action === "decrease") setQuantity(itemId, current - 1);
        if (button.dataset.action === "remove") {
            changeCart(`/api/cart/items/${itemId}`, { method: "DELETE" });
        }
    });

    cartList.addEventListener("change", (event) => {
        if (event.target.tagName !== "INPUT") return;
        const row = event.target.closest(".cart-row");
        setQuantity(row.dataset.itemId, Number(event.target.value));
    });
}

// ---------- checkout and orders (Phase 6) ----------

const checkoutForm = document.getElementById("checkout-form");
if (checkoutForm) {
    const messageBox = document.getElementById("form-message");
    const button = checkoutForm.querySelector('button[type="submit"]');

    checkoutForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        messageBox.hidden = true;
        // Disabled immediately so a double click can't send two requests.
        // (The server is also protected: it locks the cart rows.)
        button.disabled = true;
        button.textContent = "Placing order…";
        try {
            const result = await apiRequest("/api/orders", {
                method: "POST",
                body: formToObject(checkoutForm),
            });
            setCartCount(0);
            // One order: go straight to paying for it.
            window.location.href = result.orders.length === 1
                ? `/payment/${result.orders[0].id}`
                : `/orders?placed=${result.orders.length}`;
        } catch (error) {
            showMessage(messageBox, error.message);
            messageBox.scrollIntoView({ behavior: "smooth", block: "center" });
            button.disabled = false;
            button.textContent = "Place order";
        }
    });
}

const cancelOrderButton = document.getElementById("cancel-order-btn");
if (cancelOrderButton) {
    cancelOrderButton.addEventListener("click", async () => {
        if (!confirm("Cancel this order? The items go back on sale.")) return;
        cancelOrderButton.disabled = true;
        try {
            await apiRequest(`/api/orders/${cancelOrderButton.dataset.orderId}/cancel`, { method: "POST" });
            window.location.reload();
        } catch (error) {
            showMessage(document.getElementById("form-message"), error.message);
            cancelOrderButton.disabled = false;
        }
    });
}

// ---------- payment (Phase 7) ----------

const paymentForm = document.getElementById("payment-form");
if (paymentForm) {
    const messageBox = document.getElementById("form-message");
    const button = paymentForm.querySelector('button[type="submit"]');

    paymentForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        messageBox.hidden = true;
        button.disabled = true;
        try {
            await apiRequest("/api/payments", {
                method: "POST",
                body: {
                    order_id: Number(paymentForm.dataset.orderId),
                    transaction_reference: paymentForm.transaction_reference.value,
                },
            });
            // Reload: the page now shows "Pending verification",
            // never "Payment successful".
            window.location.reload();
        } catch (error) {
            showMessage(messageBox, error.message);
            button.disabled = false;
        }
    });
}

// ---------- seller verification (Phase 8) ----------

const sellerOrders = document.getElementById("seller-orders");
if (sellerOrders) {
    const messageBox = document.getElementById("form-message");

    sellerOrders.addEventListener("click", async (event) => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;
        const { action, paymentId, orderId, amount, reference, status } = button.dataset;

        let url;
        let body;
        if (action === "verify") {
            if (!confirm(`Verify ${amount} with reference ${reference}?\n\nOnly do this if the money is in your UPI app.`)) return;
            url = `/api/payments/${paymentId}/verify`;
        } else if (action === "reject") {
            if (!confirm("Reject this payment? The buyer can submit a corrected reference.")) return;
            url = `/api/payments/${paymentId}/reject`;
        } else {
            url = `/api/seller/orders/${orderId}/status`;
            body = { status };
        }

        button.disabled = true;
        try {
            await apiRequest(url, { method: "POST", body });
            window.location.reload();
        } catch (error) {
            showMessage(messageBox, error.message);
            button.disabled = false;
        }
    });
}

// ---------- Razorpay checkout (upgrade) ----------
// 1. Ask OUR server to create the Razorpay order (it holds the secret key).
// 2. Open Razorpay's payment window with that order id.
// 3. On success, send the result back to OUR server to verify. The page
//    never decides by itself that a payment succeeded.

const rzpButton = document.getElementById("rzp-pay-btn");
if (rzpButton) {
    const messageBox = document.getElementById("form-message");
    const orderId = Number(rzpButton.dataset.orderId);
    const buttonText = rzpButton.textContent;

    function resetButton() {
        rzpButton.disabled = false;
        rzpButton.textContent = buttonText;
    }

    rzpButton.addEventListener("click", async () => {
        messageBox.hidden = true;
        rzpButton.disabled = true;
        rzpButton.textContent = "Opening Razorpay…";

        let options;
        try {
            options = await apiRequest("/api/payments/razorpay/order", {
                method: "POST", body: { order_id: orderId },
            });
        } catch (error) {
            showMessage(messageBox, error.message);
            resetButton();
            return;
        }

        const checkout = new Razorpay({
            key: options.key_id,
            order_id: options.razorpay_order_id,
            amount: options.amount,
            currency: options.currency,
            name: options.name,
            description: options.description,
            prefill: options.prefill,
            theme: { color: "#1F5AA6" },
            handler: async (result) => {
                // result = { razorpay_payment_id, razorpay_order_id, razorpay_signature }
                rzpButton.textContent = "Confirming payment…";
                try {
                    await apiRequest("/api/payments/razorpay/verify", {
                        method: "POST", body: { order_id: orderId, ...result },
                    });
                    window.location.reload();
                } catch (error) {
                    // If this failed, the webhook can still confirm the payment.
                    showMessage(messageBox, `${error.message} If money was taken, refresh in a minute.`);
                    resetButton();
                }
            },
            modal: { ondismiss: resetButton },
        });

        checkout.on("payment.failed", (response) => {
            showMessage(messageBox, `Payment failed: ${response.error.description}`);
        });
        checkout.open();
    });
}

// ---------- payment settings: seller UPI ID ----------

const upiForm = document.getElementById("upi-form");
if (upiForm) {
    const messageBox = document.getElementById("form-message");
    upiForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        messageBox.hidden = true;
        try {
            await apiRequest("/api/auth/me/upi", { method: "PUT", body: formToObject(upiForm) });
            if (upiForm.dataset.next) {
                window.location.href = upiForm.dataset.next;   // back to "Sell an item"
            } else {
                showMessage(messageBox, "Saved. Buyers will now pay this UPI ID.", "success");
            }
        } catch (error) {
            showMessage(messageBox, error.message);
        }
    });
}
