// Payment Admin Logic
const API_BASE = import.meta.env?.VITE_API_BASE || "https://autohdr-backend.up.railway.app";

// === DOM References ===
const adminPassInput = document.getElementById('admin-pass');
const orderForm = document.getElementById('order-form');
const editForm = document.getElementById('edit-form');
const editCard = document.getElementById('edit-card');
const ordersContainer = document.getElementById('orders-container');
const txCard = document.getElementById('tx-card');
const txContainer = document.getElementById('tx-container');

let currentEditId = null;
let allOrders = [];
let currentOrdersPage = 1;
const ORDERS_PER_PAGE = 10;

// === Helpers ===
function getPass() {
    return adminPassInput.value || sessionStorage.getItem('admin_payment_pass') || '';
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(amount);
}

function formatDate(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric' });
}

function statusBadge(status) {
    const colors = {
        PENDING: { bg: '#fef9c3', color: '#854d0e' },
        PAID: { bg: '#dcfce7', color: '#166534' },
        PARTIALLY_PAID: { bg: '#ffedd5', color: '#9a3412' },
        EXPIRED: { bg: '#f1f5f9', color: '#64748b' },
    };
    const labels = { PENDING: 'Chờ TT', PAID: 'Đã TT', PARTIALLY_PAID: 'Thiếu tiền', EXPIRED: 'Hết hạn' };
    const c = colors[status] || colors.EXPIRED;
    return `<span class="badge" style="background:${c.bg}; color:${c.color};">${labels[status] || status}</span>`;
}

function buildLink(order) {
    const base = window.location.origin;
    return `${base}/payment.html?id=${order.id}&token=${order.access_token}`;
}

async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Lỗi ${res.status}`);
    return data;
}

function showAlert(msg, type = 'info') {
    const color = type === 'error' ? '#ef4444' : type === 'success' ? '#22c55e' : '#3b82f6';
    const el = document.createElement('div');
    el.style.cssText = `position:fixed;top:1rem;right:1rem;background:${color};color:#fff;padding:0.75rem 1.25rem;border-radius:8px;font-size:0.85rem;font-weight:600;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.2);`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

function renderOrdersLoading() {
    ordersContainer.innerHTML = `
        <div class="admin-loading">
            <div class="admin-loading-spinner"></div>
            <span>Đang tải danh sách đơn hàng...</span>
        </div>`;
}

function sortOrdersNewestFirst(orders) {
    return [...orders].sort((a, b) => {
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        return bTime - aTime;
    });
}

function renderOrderPagination(totalOrders) {
    const totalPages = Math.max(1, Math.ceil(totalOrders / ORDERS_PER_PAGE));
    if (totalPages <= 1) return '';

    return `
        <div class="pagination">
            <button class="pagination-btn" data-page-nav="prev" ${currentOrdersPage === 1 ? 'disabled' : ''}>Trước</button>
            <span>Trang ${currentOrdersPage}/${totalPages}</span>
            <button class="pagination-btn" data-page-nav="next" ${currentOrdersPage === totalPages ? 'disabled' : ''}>Sau</button>
        </div>`;
}

function attachPaginationActions() {
    document.querySelectorAll('[data-page-nav]').forEach(btn => {
        btn.addEventListener('click', () => {
            const totalPages = Math.max(1, Math.ceil(allOrders.length / ORDERS_PER_PAGE));
            if (btn.dataset.pageNav === 'prev') {
                currentOrdersPage = Math.max(1, currentOrdersPage - 1);
            } else {
                currentOrdersPage = Math.min(totalPages, currentOrdersPage + 1);
            }
            renderOrders();
        });
    });
}

function renderOrders() {
    if (allOrders.length === 0) {
        ordersContainer.innerHTML = '<p style="color:var(--text-light);font-size:0.85rem;">Chưa có đơn hàng nào.</p>';
        return;
    }

    const totalPages = Math.max(1, Math.ceil(allOrders.length / ORDERS_PER_PAGE));
    currentOrdersPage = Math.min(Math.max(currentOrdersPage, 1), totalPages);

    const start = (currentOrdersPage - 1) * ORDERS_PER_PAGE;
    const orders = allOrders.slice(start, start + ORDERS_PER_PAGE);

    ordersContainer.innerHTML = `
        <div style="overflow-x:auto;">
            <table class="admin-table">
                <thead>
                    <tr>
                        <th>Mã đơn</th>
                        <th>Khách hàng</th>
                        <th>Số tiền</th>
                        <th>Trạng thái</th>
                        <th>Ngày tạo</th>
                        <th>Hành động</th>
                    </tr>
                </thead>
                <tbody>
                    ${orders.map(o => `
                    <tr>
                        <td data-label="Mã đơn"><code>${o.id}</code></td>
                        <td data-label="Khách hàng">${escapeHtml(o.user_name)}</td>
                        <td data-label="Số tiền">${formatCurrency(o.amount)}</td>
                        <td data-label="Trạng thái">${statusBadge(o.status)}</td>
                        <td data-label="Ngày tạo" style="font-size:0.75rem;color:var(--text-light);">${formatDate(o.created_at)}</td>
                        <td data-label="Hành động">
                             <div style="display:flex;gap:0.35rem;flex-wrap:wrap;">
                                 <button class="btn-action" data-action="copy" data-id="${o.id}" data-token="${o.access_token}" title="Copy link thanh toán">Copy link</button>
                                 <button class="btn-action" data-action="edit" data-id="${o.id}" data-name="${escapeHtml(o.user_name)}" data-amount="${o.amount}" data-item="${escapeHtml(o.item || '')}" data-note="${escapeHtml(o.note || '')}" data-status="${o.status}" title="Chỉnh sửa">Sửa</button>
                                 <button class="btn-action" data-action="tx" data-id="${o.id}" title="Lịch sử giao dịch">Giao dịch</button>
                                 <button class="btn-action btn-danger" data-action="delete" data-id="${o.id}" title="Xóa đơn">Xóa</button>
                             </div>
                        </td>
                    </tr>`).join('')}
                </tbody>
            </table>
        </div>
        ${renderOrderPagination(allOrders.length)}`;

    document.querySelectorAll('.btn-action').forEach(btn => {
        btn.style.cssText = 'background:#f1f5f9;border:1px solid var(--border);padding:0.25rem 0.5rem;border-radius:4px;cursor:pointer;font-size:0.85rem;';
        btn.addEventListener('mouseenter', () => btn.style.background = '#e2e8f0');
        btn.addEventListener('mouseleave', () => btn.style.background = '#f1f5f9');
        if (btn.classList.contains('btn-danger')) {
            btn.style.cssText += 'color:var(--error);';
        }
    });

    attachOrderActions();
    attachPaginationActions();
}

// === Load Orders ===
async function loadOrders({ resetPage = true } = {}) {
    const pass = getPass();
    if (!pass) { showAlert('Vui lòng nhập mật khẩu Admin', 'error'); return; }

    renderOrdersLoading();

    try {
        const orders = await apiPost('/api/payment/admin/orders/list', { password: pass });
        allOrders = sortOrdersNewestFirst(orders);
        if (resetPage) currentOrdersPage = 1;
        renderOrders();
    } catch (e) {
        showAlert(e.message, 'error');
        ordersContainer.innerHTML = '<p style="color:var(--error);font-size:0.85rem;">Không thể tải danh sách đơn hàng.</p>';
    }
}

function attachOrderActions() {
    document.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const action = btn.dataset.action;
            const id = btn.dataset.id;

            if (action === 'copy') {
                const link = buildLink({ id, access_token: btn.dataset.token });
                await navigator.clipboard.writeText(link);
                showAlert('Đã copy link thanh toán!', 'success');
            }

            if (action === 'edit') {
                currentEditId = id;
                document.getElementById('edit-order-id').textContent = `#${id}`;
                document.getElementById('edit-username').value = btn.dataset.name;
                document.getElementById('edit-amount').value = btn.dataset.amount;
                document.getElementById('edit-item').value = btn.dataset.item;
                document.getElementById('edit-note').value = btn.dataset.note;
                document.getElementById('edit-status').value = btn.dataset.status;
                editCard.style.display = 'block';
                editCard.scrollIntoView({ behavior: 'smooth' });
            }

            if (action === 'tx') {
                await loadTransactions(id);
            }

            if (action === 'delete') {
                if (!confirm(`Xóa đơn hàng #${id}? Hành động này không thể hoàn tác.`)) return;
                try {
                    await apiPost(`/api/payment/admin/orders/delete/${id}`, { password: getPass() });
                    showAlert('Đã xóa đơn hàng.', 'success');
                    loadOrders({ resetPage: false });
                } catch (e) {
                    showAlert(e.message, 'error');
                }
            }
        });
    });
}

// === Load Transaction History ===
async function loadTransactions(orderId) {
    txCard.style.display = 'block';
    document.getElementById('tx-order-id').textContent = `#${orderId}`;
    txContainer.innerHTML = '<p style="color:var(--text-light);font-size:0.85rem;">Đang tải...</p>';
    txCard.scrollIntoView({ behavior: 'smooth' });

    try {
        const txs = await apiPost(`/api/payment/admin/orders/${orderId}/transactions`, { password: getPass() });
        if (txs.length === 0) {
            txContainer.innerHTML = '<p style="color:var(--text-light);font-size:0.85rem;">Chưa có giao dịch nào.</p>';
            return;
        }
        txContainer.innerHTML = `
            <table class="admin-table">
                <thead><tr><th>#</th><th>Số tiền</th><th>Nội dung CK</th><th>Thời gian</th></tr></thead>
                <tbody>
                    ${txs.map((tx, i) => `
                    <tr>
                        <td data-label="#">${i + 1}</td>
                        <td data-label="Số tiền" style="color:var(--success);font-weight:600;">${formatCurrency(tx.transfer_amount)}</td>
                        <td data-label="Nội dung CK" style="font-size:0.8rem;">${escapeHtml(tx.content)}</td>
                        <td data-label="Thời gian" style="font-size:0.75rem;color:var(--text-light);">${formatDate(tx.created_at)}</td>
                    </tr>`).join('')}
                </tbody>
            </table>`;
    } catch (e) {
        showAlert(e.message, 'error');
    }
}

function escapeHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// === Event Listeners ===
orderForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const pass = getPass();
    if (!pass) { showAlert('Vui lòng nhập mật khẩu Admin', 'error'); return; }

    const btn = document.getElementById('btn-create');
    btn.disabled = true; btn.textContent = 'Đang tạo...';

    try {
        const order = await apiPost('/api/payment/admin/orders/create', {
            password: pass,
            user_name: document.getElementById('order-username').value,
            amount: parseFloat(document.getElementById('order-amount').value),
            item: document.getElementById('order-item').value,
            note: document.getElementById('order-note').value,
        });
        showAlert(`Tạo đơn #${order.id} thành công!`, 'success');
        orderForm.reset();
        adminPassInput.value = pass;
        await loadOrders();
    } catch (e) {
        showAlert(e.message, 'error');
    } finally {
        btn.disabled = false; btn.textContent = 'Tạo đơn hàng';
    }
});

editForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!currentEditId) return;
    try {
        await apiPost(`/api/payment/admin/orders/update/${currentEditId}`, {
            password: getPass(),
            user_name: document.getElementById('edit-username').value,
            amount: parseFloat(document.getElementById('edit-amount').value),
            item: document.getElementById('edit-item').value,
            note: document.getElementById('edit-note').value,
            status: document.getElementById('edit-status').value,
        });
        showAlert('Cập nhật thành công!', 'success');
        editCard.style.display = 'none';
        currentEditId = null;
        loadOrders({ resetPage: false });
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

document.getElementById('btn-cancel-edit').addEventListener('click', () => {
    editCard.style.display = 'none';
    currentEditId = null;
});

document.getElementById('btn-refresh-orders').addEventListener('click', loadOrders);

// Lưu mật khẩu vào session
adminPassInput.addEventListener('change', () => {
    sessionStorage.setItem('admin_payment_pass', adminPassInput.value);
});

// Load mật khẩu từ session
window.addEventListener('load', () => {
    const saved = sessionStorage.getItem('admin_payment_pass');
    if (saved) adminPassInput.value = saved;
});
