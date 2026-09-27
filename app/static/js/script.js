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


// ---------- small shared helpers (premium theme) ----------

// Inline SVG icons used by cards that JavaScript builds (same shapes the
// server renders with the icon() template helper).
const ICON_PATHS = {
    person: "M372-523q-42-42-42-108t42-108q42-42 108-42t108 42q42 42 42 108t-42 108q-42 42-108 42t-108-42ZM160-160v-94q0-38 19-65t49-41q67-30 128.5-45T480-420q62 0 123 15.5T731-360q31 14 50 41t19 65v94H160Zm60-60h520v-34q0-16-9.5-30.5T707-306q-64-31-117-42.5T480-360q-57 0-111 11.5T252-306q-14 7-23 21.5t-9 30.5v34Zm324.5-346.5Q570-592 570-631t-25.5-64.5Q519-721 480-721t-64.5 25.5Q390-670 390-631t25.5 64.5Q441-541 480-541t64.5-25.5ZM480-631Zm0 411Z",
    arrow_forward: "M686-450H160v-60h526L438-758l42-42 320 320-320 320-42-42 248-248Z",
    check: "m421-298 283-283-46-45-237 237-120-120-45 45 165 166Zm59 218q-82 0-155-31.5t-127.5-86Q143-252 111.5-325T80-480q0-83 31.5-156t86-127Q252-817 325-848.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 82-31.5 155T763-197.5q-54 54.5-127 86T480-80Zm0-60q142 0 241-99.5T820-480q0-142-99-241t-241-99q-141 0-240.5 99T140-480q0 141 99.5 240.5T480-140Zm0-340Z",
    copy: "M300-200q-24 0-42-18t-18-42v-560q0-24 18-42t42-18h440q24 0 42 18t18 42v560q0 24-18 42t-42 18H300Zm0-60h440v-560H300v560ZM180-80q-24 0-42-18t-18-42v-620h60v620h500v60H180Zm120-180v-560 560Z",
};
function svgIcon(name, size = 18) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("viewBox", "0 -960 960 960");
    svg.setAttribute("fill", "currentColor");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ICON_PATHS[name]);
    svg.append(path);
    return svg;
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;   // textContent: never parsed as HTML
    return node;
}

// "2026-09-24T10:00:00" (UTC from the API) -> "2h ago"
function timeAgo(isoUtc) {
    const then = new Date(isoUtc.endsWith("Z") ? isoUtc : `${isoUtc}Z`);
    const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)}d ago`;
    return then.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

// Quantity steppers: [data-stepper] with − / + buttons around a number input.
document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-stepper] [data-step]");
    if (!button) return;
    const input = button.closest("[data-stepper]").querySelector("input");
    const min = Number(input.min || 0);
    const max = Number(input.max || 999);
    const next = Math.min(max, Math.max(min, (Number(input.value) || 0) + Number(button.dataset.step)));
    input.value = next;
    input.dispatchEvent(new Event("change", { bubbles: true }));
});

// Copy buttons: <button data-copy="text">
document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.replaceChildren(svgIcon("check", 16));
        setTimeout(() => button.replaceChildren(svgIcon("copy", 16)), 1500);
    } catch { /* clipboard blocked: the text is still visible to copy by hand */ }
});

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

    const emptyPhoto = document.getElementById("image-empty");
    const dropzone = document.getElementById("dropzone");

    // Quick checks for a better experience. The server re-checks everything.
    function usePhoto() {
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
        if (emptyPhoto) emptyPhoto.hidden = true;
    }
    imageInput.addEventListener("change", usePhoto);

    // Drag and drop a photo onto the drop zone.
    if (dropzone) {
        ["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
            event.preventDefault();
            dropzone.classList.add("is-dragover");
        }));
        ["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, () => {
            dropzone.classList.remove("is-dragover");
        }));
        dropzone.addEventListener("drop", (event) => {
            event.preventDefault();
            if (event.dataTransfer.files.length) {
                imageInput.files = event.dataTransfer.files;
                usePhoto();
            }
        });
    }

    productForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!productForm.reportValidity()) return;   // browser shows which field is missing
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

    // Same structure as templates/_product_card.html, built with textContent:
    // a product named "<img src=x onerror=alert(1)>" is shown as plain text.
    function buildCard(product) {
        const card = el("a", "product-card");
        card.href = `/product/${product.id}`;

        const media = el("div", "media");
        const img = el("img");
        img.src = product.image_url;
        img.alt = product.name;
        img.loading = "lazy";
        media.append(img, el("span", "pill pill-white", product.condition_label));

        const body = el("div", "body");
        const priceRow = el("div", "price-row");
        priceRow.append(el("span", "price", priceFormat.format(Number(product.price))),
                        el("span", "pill pill-mint", product.category_label));

        const meta = el("div", "meta");
        const who = el("span", "who");
        who.append(svgIcon("person", 14), el("strong", "", product.seller_username));
        const time = el("time", "", timeAgo(product.created_at));
        time.dateTime = product.created_at;
        meta.append(who, time);

        const view = el("span", "view", "View product ");
        view.append(svgIcon("arrow_forward"));

        body.append(priceRow, el("h3", "", product.name), el("p", "desc", product.description), meta, view);
        card.append(media, body);
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
            countText.querySelector("span").textContent =
                `${n} item${n === 1 ? "" : "s"}${params.toString() ? " found" : ""}`;
            emptyState.hidden = n > 0;
            if (n === 0) {
                const filtered = params.toString() !== "";
                const icon = emptyState.querySelector(".empty-icon");
                const action = el("a", "btn btn-white", filtered ? "Clear filters" : "Browse all");
                action.href = "/";
                emptyState.replaceChildren(
                    icon,
                    el("h2", "", filtered ? "No items match these filters." : "Nothing is listed yet."),
                    el("p", "", filtered ? "Try a wider price range or another category." : "Be the first to list something."),
                    action,
                );
            }
        } catch (error) {
            if (error.name === "AbortError") return;   // replaced by a newer search
            showMessage(filterMessage, error.message);
        } finally {
            grid.classList.remove("loading");
        }
    }

    // Typing: wait until the user pauses for 300 ms instead of one request per key.
    // Chips (radio buttons) and the sort menu update instantly.
    const instant = (target) => target.tagName === "SELECT" || target.type === "radio";
    filterForm.addEventListener("input", (event) => {
        if (instant(event.target)) return;   // handled by "change"
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(refreshResults, 300);
    });
    filterForm.addEventListener("change", (event) => {
        if (instant(event.target)) refreshResults();
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
        each.textContent = `${formatINR(item.unit_price)} each · Seller ${item.seller_username}`;
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
        remove.className = "link-btn";
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
        if (!checkoutForm.reportValidity()) return;
        const original = button.innerHTML;
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
            button.innerHTML = original;
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
    const buttonHtml = rzpButton.innerHTML;

    function resetButton() {
        rzpButton.disabled = false;
        rzpButton.innerHTML = buttonHtml;
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
