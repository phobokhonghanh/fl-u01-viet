// Payment Page Logic - AutoHDR (SePay Payment Gateway)
const API_BASE = import.meta.env?.VITE_API_BASE || "https://autohdr-backend.up.railway.app";

const card = document.getElementById('main-card');

const params = new URLSearchParams(window.location.search);
const orderId = params.get('id');
const token = params.get('token');
const paymentResult = params.get('payment'); // "success" | "error" | "cancel" khi SePay redirect về

let currentOrder = null;

// === Helpers ===
function formatCurrency(amount) {
    return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(amount);
}

function statusLabel(status) {
    const map = {
        PENDING: 'Chờ thanh toán',
        PAID: 'Đã thanh toán',
        PARTIALLY_PAID: 'Thanh toán một phần',
        EXPIRED: 'Hết hạn',
    };
    return map[status] || status;
}

function escapeHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// === Render ===
function renderError(message) {
    card.innerHTML = `
        <div class="error-box fade-in">
            <h2>⚠️ Không tìm thấy đơn hàng</h2>
            <p>${message}</p>
            <p style="margin-top:0.75rem;">Vui lòng kiểm tra lại link hoặc liên hệ người bán.</p>
        </div>`;
}

function renderLookupForm() {
    card.innerHTML = `
        <h3 style="margin-bottom:1rem; font-size:1rem;">🔍 Tra cứu đơn hàng</h3>
        <div class="lookup-form">
            <label for="inp-id">Mã đơn hàng</label>
            <input type="text" id="inp-id" placeholder="Ví dụ: AB12CD34" maxlength="8" />
            <label for="inp-token">Token truy cập</label>
            <input type="text" id="inp-token" placeholder="Token trong link bạn nhận được" />
            <button class="btn-refresh" id="btn-lookup">Tra cứu</button>
        </div>`;
    document.getElementById('btn-lookup').addEventListener('click', () => {
        const id = document.getElementById('inp-id').value.trim().toUpperCase();
        const tk = document.getElementById('inp-token').value.trim();
        if (id && tk) window.location.href = `payment.html?id=${id}&token=${tk}`;
    });
}

function renderBanner(type) {
    // Render banner thông báo kết quả từ SePay redirect
    const configs = {
        success: { bg: '#dcfce7', border: '#86efac', color: '#166534', icon: '✅', text: 'Thanh toán thành công! Trang đang cập nhật trạng thái đơn hàng...' },
        error:   { bg: '#fef2f2', border: '#fca5a5', color: '#991b1b', icon: '❌', text: 'Thanh toán thất bại. Vui lòng thử lại.' },
        cancel:  { bg: '#fef9c3', border: '#fde68a', color: '#854d0e', icon: '⚠️', text: 'Bạn đã hủy thanh toán.' },
    };
    const c = configs[type];
    if (!c) return '';
    return `<div style="background:${c.bg};border:1px solid ${c.border};color:${c.color};padding:0.75rem 1rem;border-radius:8px;margin-bottom:1rem;font-size:0.85rem;font-weight:600;">
        ${c.icon} ${c.text}
    </div>`;
}

function renderOrder(order) {
    currentOrder = order;
    const isPaid = order.status === 'PAID';
    const isExpired = order.status === 'EXPIRED';
    const date = order.created_at
        ? new Date(order.created_at).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
        : '';

    let contentSection = '';
    if (isPaid && order.item) {
        contentSection = `
            <hr class="divider">
            <div class="section-label">📦 Nội dung nhận hàng</div>
            <div class="item-box">${escapeHtml(order.item)}</div>`;
    }
    if (order.note) {
        contentSection += `
            <div class="section-label">💬 Lời nhắn từ người bán</div>
            <div class="note-box">${escapeHtml(order.note)}</div>`;
    }

    // Nút thanh toán SePay
    let actionSection = '';
    if (!isPaid && !isExpired) {
        actionSection = `
            <button class="btn-refresh" id="btn-pay" style="background:linear-gradient(135deg,#3b82f6,#1d4ed8);">
                💳 Thanh toán qua SePay
            </button>
            <p style="text-align:center;font-size:0.72rem;color:var(--text-light);margin-top:0.5rem;">
                Bạn sẽ được chuyển sang trang thanh toán an toàn của SePay
            </p>`;
    }

    card.innerHTML = `
        <div class="fade-in">
            ${paymentResult ? renderBanner(paymentResult) : ''}
            <div class="order-header">
                <div>
                    <div class="order-user">${escapeHtml(order.user_name)}</div>
                    <div class="order-id">Đơn #${order.id} · ${date}</div>
                </div>
                <span class="status-badge status-${order.status}">${statusLabel(order.status)}</span>
            </div>
            <div class="amount-display">${formatCurrency(order.amount)}</div>
            ${isPaid ? '<div style="text-align:center;color:var(--success);font-weight:600;margin-bottom:1rem;">✅ Thanh toán thành công!</div>' : ''}
            ${isExpired ? '<div class="waiting-box"><p>Đơn hàng này đã hết hạn. Vui lòng liên hệ người bán để được hỗ trợ.</p></div>' : ''}
            ${actionSection}
            ${contentSection}
        </div>`;

    if (!isPaid && !isExpired) {
        document.getElementById('btn-pay').addEventListener('click', initiateCheckout);
        // SSE để cập nhật khi SePay IPN về
        connectSSE();
    }
}

// === SePay Checkout ===
async function initiateCheckout() {
    const btn = document.getElementById('btn-pay');
    if (btn) { btn.disabled = true; btn.textContent = 'Đang khởi tạo...'; }

    try {
        const res = await fetch(
            `${API_BASE}/api/payment/order/${orderId}/checkout?token=${encodeURIComponent(token)}`
        );

        // Xử lý các lỗi từ server
        if (res.status === 400) {
            const err = await res.json();
            showToast(err.detail || 'Đơn hàng không thể thanh toán.', 'error');
            if (btn) { btn.disabled = false; btn.textContent = '💳 Thanh toán qua SePay'; }
            // Reload lại để cập nhật trạng thái mới nhất
            setTimeout(loadOrder, 1500);
            return;
        }

        if (res.status === 503) {
            showToast('Cổng thanh toán tạm thời không khả dụng. Vui lòng thử lại sau.', 'error');
            if (btn) { btn.disabled = false; btn.textContent = '💳 Thanh toán qua SePay'; }
            return;
        }

        if (!res.ok) {
            throw new Error(`Lỗi server: ${res.status}`);
        }

        const { checkout_url, fields } = await res.json();

        // Tạo form ẩn và submit sang SePay
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = checkout_url;
        form.style.display = 'none';

        // Render theo thứ tự object backend đã ký, giống cách SDK SePay dựng form.
        for (const [name, value] of Object.entries(fields)) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = name;
            input.value = value;
            form.appendChild(input);
        }

        console.group('SePay checkout form');
        console.log('POST', checkout_url);
        console.table(Array.from(form.elements).map((input, index) => ({
            order: index + 1,
            name: input.name,
            value: input.value,
        })));
        console.groupEnd();

        document.body.appendChild(form);
        form.submit();

    } catch (e) {
        console.error('Checkout error:', e);
        showToast('Không thể kết nối đến server. Vui lòng thử lại.', 'error');
        if (btn) { btn.disabled = false; btn.textContent = '💳 Thanh toán qua SePay'; }
    }
}

// === SSE ===
function connectSSE() {
    const url = `${API_BASE}/api/payment/order/${orderId}/events?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);

    es.onmessage = (e) => {
        if (!e.data || e.data === 'ping') return;
        try {
            const payload = JSON.parse(e.data);
            if (payload.status && payload.status !== currentOrder?.status) {
                es.close();
                loadOrder();
            }
        } catch (_) {}
    };

    es.onerror = () => {
        // EventSource tự reconnect, không cần làm gì thêm
    };
}

// === Toast notification ===
function showToast(msg, type = 'info') {
    const color = type === 'error' ? '#ef4444' : '#3b82f6';
    const el = document.createElement('div');
    el.style.cssText = `position:fixed;top:1rem;right:1rem;background:${color};color:#fff;padding:0.75rem 1.25rem;border-radius:8px;font-size:0.85rem;font-weight:600;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.2);`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

// === Main ===
async function loadOrder() {
    if (!orderId || !token) {
        renderLookupForm();
        return;
    }

    try {
        const res = await fetch(
            `${API_BASE}/api/payment/order/${orderId}?token=${encodeURIComponent(token)}`
        );
        if (res.status === 404) {
            renderError('Đơn hàng không tồn tại hoặc token không hợp lệ.');
            return;
        }
        if (!res.ok) throw new Error(`Lỗi server: ${res.status}`);
        const order = await res.json();
        renderOrder(order);
    } catch (e) {
        renderError(e.message || 'Không thể kết nối tới server. Vui lòng thử lại sau.');
    }
}

window.loadOrder = loadOrder;
loadOrder();
