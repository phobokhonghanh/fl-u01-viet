// Admin Dashboard Logic - AutoHDR Key Management

const API_BASE = import.meta.env?.VITE_API_BASE || "https://autohdr-backend.up.railway.app";

// DOM Elements
const passwordModal = document.getElementById('password-modal');
const passwordForm = document.getElementById('password-form');
const adminPassInput = document.getElementById('admin-pass');
const btnLockSession = document.getElementById('btn-lock-session');

const createKeyModal = document.getElementById('create-key-modal');
const btnOpenCreateModal = document.getElementById('btn-open-create-modal');
const btnCloseCreateModal = document.getElementById('btn-close-create-modal');
const keyForm = document.getElementById('key-form');
const keyNameInput = document.getElementById('key-name');
const keyProductSelect = document.getElementById('key-product');
const btnCreate = document.getElementById('btn-create');
const keyLevelSelect = document.getElementById('key-level');
const resultDiv = document.getElementById('new-key-result');
const displayKey = document.getElementById('display-key');
const btnCopy = document.getElementById('btn-copy');

const extendKeyModal = document.getElementById('extend-key-modal');
const btnCloseExtendModal = document.getElementById('btn-close-extend-modal');
const extendKeyForm = document.getElementById('extend-key-form');
const extendKeyNameInput = document.getElementById('extend-key-name');
const extendKeyProductInput = document.getElementById('extend-key-product');
const extendDisplayName = document.getElementById('extend-display-name');
const extendDisplayProduct = document.getElementById('extend-display-product');
const btnExtendSubmit = document.getElementById('btn-extend-submit');

const searchNameInput = document.getElementById('search-name-input');
const filterProductSelect = document.getElementById('filter-product-select');
const filterStatusSelect = document.getElementById('filter-status-select');
const btnList = document.getElementById('btn-list');
const btnExport = document.getElementById('btn-export');
const inputImport = document.getElementById('import-file');
const keysBody = document.getElementById('keys-body');

let allKeys = [];
let searchQuery = "";
let selectedProduct = "all";
let selectedStatus = "all";
let currentKeysPage = 1;
const KEYS_PER_PAGE = 10;

/**
 * Show basic alert/toast message
 */
function showToast(message, isError = false) {
    alert(message);
}

function escapeHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function normalizeProduct(product) {
    return String(product || 'autohdr').trim().toLowerCase() || 'autohdr';
}

function productBadge(product) {
    const value = normalizeProduct(product);
    const labels = { autohdr: 'AutoHDR', fotello: 'Fotello' };
    const colors = {
        autohdr: { bg: '#dbeafe', color: '#1e40af' },
        fotello: { bg: '#dcfce7', color: '#166534' },
    };
    const c = colors[value] || { bg: '#f1f5f9', color: '#64748b' };
    return `<span class="badge" style="background:${c.bg}; color:${c.color};">${labels[value] || escapeHtml(value)}</span>`;
}

function levelBadge(level) {
    const value = String(level || 'lite').trim().toLowerCase();
    const labels = { lite: 'Lite', plus: 'Plus' };
    const colors = {
        lite: { bg: '#f1f5f9', color: '#64748b' },
        plus: { bg: '#fef3c7', color: '#b45309' }
    };
    const c = colors[value] || { bg: '#f1f5f9', color: '#64748b' };
    return `<span class="badge" style="background:${c.bg}; color:${c.color}; font-weight:700;">${labels[value] || escapeHtml(value).toUpperCase()}</span>`;
}

function getStoredPassword() {
    return sessionStorage.getItem('admin_password') || '';
}

function setStoredPassword(pass) {
    sessionStorage.setItem('admin_password', pass);
}

function clearStoredPassword() {
    sessionStorage.removeItem('admin_password');
}

/**
 * Show password modal overlay
 */
function showPasswordModal() {
    passwordModal.classList.add('is-active');
}

function hidePasswordModal() {
    passwordModal.classList.remove('is-active');
}

/**
 * Handle password submission
 */
passwordForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const password = adminPassInput.value.trim();
    if (!password) {
        showToast("Vui lòng nhập mật khẩu!");
        return;
    }
    setStoredPassword(password);
    hidePasswordModal();
    await loadKeys();
});

/**
 * Lock session/Logout
 */
btnLockSession.addEventListener('click', () => {
    clearStoredPassword();
    adminPassInput.value = '';
    allKeys = [];
    renderKeys();
    showPasswordModal();
});

/**
 * Create key modal triggers
 */
btnOpenCreateModal.addEventListener('click', () => {
    keyNameInput.value = '';
    resultDiv.style.display = 'none';
    displayKey.innerText = '';
    createKeyModal.classList.add('is-active');
});

btnCloseCreateModal.addEventListener('click', () => {
    createKeyModal.classList.remove('is-active');
});

/**
 * Extend key modal close
 */
btnCloseExtendModal.addEventListener('click', () => {
    extendKeyModal.classList.remove('is-active');
});

function renderKeysLoading() {
    const oldPagination = document.getElementById('keys-pagination');
    if (oldPagination) oldPagination.remove();
    keysBody.innerHTML = `
        <tr>
            <td colspan="7" data-empty="true">
                <div class="admin-loading">
                    <div class="admin-loading-spinner"></div>
                    <span>Đang tải danh sách key...</span>
                </div>
            </td>
        </tr>`;
}

function isExpired(k) {
    if (!k.is_active) return true;
    if (!k.expires_at) return false;
    try {
        const expiresDt = new Date(k.expires_at);
        const now = new Date();
        return now >= expiresDt;
    } catch (e) {
        return true;
    }
}

function getFilteredKeys() {
    let keys = allKeys;
    if (searchQuery) {
        const query = searchQuery.toLowerCase().trim();
        keys = keys.filter(k =>
            (k.name || '').toLowerCase().includes(query) ||
            (k.key || '').toLowerCase().includes(query)
        );
    }
    if (selectedProduct !== "all") {
        keys = keys.filter(k => normalizeProduct(k.product) === selectedProduct);
    }
    if (selectedStatus !== "all") {
        if (selectedStatus === "expired") {
            keys = keys.filter(k => isExpired(k));
        } else if (selectedStatus === "active") {
            keys = keys.filter(k => !isExpired(k));
        }
    }
    return keys;
}

function renderKeyPagination(totalKeys) {
    const totalPages = Math.max(1, Math.ceil(totalKeys / KEYS_PER_PAGE));
    if (totalPages <= 1) return '';

    return `
        <div class="pagination">
            <button class="pagination-btn" data-key-page-nav="prev" ${currentKeysPage === 1 ? 'disabled' : ''}>Trước</button>
            <span>Trang ${currentKeysPage}/${totalPages}</span>
            <button class="pagination-btn" data-key-page-nav="next" ${currentKeysPage === totalPages ? 'disabled' : ''}>Sau</button>
        </div>`;
}

function attachKeyPaginationActions(filteredKeysCount) {
    document.querySelectorAll('[data-key-page-nav]').forEach(btn => {
        btn.addEventListener('click', () => {
            const totalPages = Math.max(1, Math.ceil(filteredKeysCount / KEYS_PER_PAGE));
            if (btn.dataset.keyPageNav === 'prev') {
                currentKeysPage = Math.max(1, currentKeysPage - 1);
            } else {
                currentKeysPage = Math.min(totalPages, currentKeysPage + 1);
            }
            renderKeys();
        });
    });
}

function sortKeys(keys) {
    return [...keys].sort((a, b) => {
        const productCompare = normalizeProduct(a.product).localeCompare(normalizeProduct(b.product));
        if (productCompare !== 0) return productCompare;
        return String(a.name || '').localeCompare(String(b.name || ''));
    });
}

/**
 * Fetch keys list from S3/Backend
 */
async function loadKeys({ resetPage = true } = {}) {
    const password = getStoredPassword();
    if (!password) {
        showPasswordModal();
        return;
    }

    renderKeysLoading();

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/list`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Sai mật khẩu hoặc lỗi server");
        }

        const keys = await response.json();
        allKeys = sortKeys(keys);
        if (resetPage) currentKeysPage = 1;
        renderKeys();
    } catch (error) {
        showToast(error.message, true);
        clearStoredPassword();
        showPasswordModal();
        const oldPagination = document.getElementById('keys-pagination');
        if (oldPagination) oldPagination.remove();
        keysBody.innerHTML = `<tr><td colspan="7" data-empty="true" style="text-align: center; padding: 2rem; color: var(--error);">Không thể tải danh sách key. Mật khẩu không hợp lệ.</td></tr>`;
    }
}

/**
 * Render Keys List Table
 */
function renderKeys() {
    const tableContainer = document.getElementById('keys-table-container');
    const filteredKeys = getFilteredKeys();

    if (!filteredKeys || filteredKeys.length === 0) {
        keysBody.innerHTML = `<tr><td colspan="7" data-empty="true" style="text-align: center; padding: 2rem; color: var(--text-light);">Không tìm thấy Key phù hợp.</td></tr>`;
        const oldPagination = document.getElementById('keys-pagination');
        if (oldPagination) oldPagination.remove();
        return;
    }

    const totalPages = Math.max(1, Math.ceil(filteredKeys.length / KEYS_PER_PAGE));
    currentKeysPage = Math.min(Math.max(currentKeysPage, 1), totalPages);

    const start = (currentKeysPage - 1) * KEYS_PER_PAGE;
    const keys = filteredKeys.slice(start, start + KEYS_PER_PAGE);

    keysBody.innerHTML = keys.map(k => {
        const expires = k.expires_at ? new Date(k.expires_at).toLocaleDateString('vi-VN') : 'Vĩnh viễn';
        const machine = k.machine_id
            ? `<span style="font-size: 0.72rem; color: var(--text); font-weight: 500;" title="${escapeHtml(k.machine_id)}">${escapeHtml(k.machine_id.substring(0, 10))}...</span>`
            : '<span style="color: var(--text-light); font-size: 0.72rem;">Chưa kích hoạt</span>';

        return `
            <tr style="border-bottom: 1px solid var(--border);">
                <td data-label="Tên" style="padding: 0.75rem;">${escapeHtml(k.name)}</td>
                <td data-label="Key" style="padding: 0.75rem;"><code style="background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-family: monospace;">${escapeHtml(k.key)}</code></td>
                <td data-label="Ứng dụng" style="padding: 0.75rem;">${productBadge(k.product)}</td>
                <td data-label="Gói" style="padding: 0.75rem;">${levelBadge(k.level)}</td>
                <td data-label="Hạn dùng" style="padding: 0.75rem;">${expires}</td>
                <td data-label="Máy khóa" style="padding: 0.75rem;">${machine}</td>
                <td data-label="Hành động" style="padding: 0.75rem; text-align: center;">
                    <div class="key-actions-cell">
                        <button class="btn-action btn-action-reset" data-action="reset" data-key-val="${escapeHtml(k.key)}" data-key-name="${escapeHtml(k.name)}" title="Reset máy khóa">Reset</button>
                        <button class="btn-action btn-action-extend" data-action="extend" data-key-name="${escapeHtml(k.name)}" data-key-product="${escapeHtml(k.product)}" title="Gia hạn key">Gia hạn</button>
                        <button class="btn-action btn-action-delete" data-action="delete" data-key-val="${escapeHtml(k.key)}" title="Xóa key">Xóa</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    const oldPagination = document.getElementById('keys-pagination');
    if (oldPagination) oldPagination.remove();
    tableContainer.insertAdjacentHTML('afterend', `<div id="keys-pagination">${renderKeyPagination(filteredKeys.length)}</div>`);
    attachKeyPaginationActions(filteredKeys.length);

    // Attach actions
    document.querySelectorAll('.btn-action').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const action = btn.dataset.action;
            const keyVal = btn.dataset.keyVal;
            const keyName = btn.dataset.keyName;
            const keyProduct = btn.dataset.keyProduct;

            if (action === 'delete') {
                if (confirm(`Bạn có chắc chắn muốn xóa Key "${keyVal}" không?`)) {
                    await deleteKey(keyVal);
                }
            } else if (action === 'reset') {
                if (confirm(`Bạn có chắc chắn muốn reset máy khóa (Machine ID) cho key "${keyName || keyVal}"?`)) {
                    await resetKey(keyVal || keyName);
                }
            } else if (action === 'extend') {
                openExtendModal(keyName, keyProduct);
            }
        });
    });
}

/**
 * API call: Reset key machine ID
 */
async function resetKey(key) {
    const password = getStoredPassword();
    if (!password) {
        showPasswordModal();
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/reset`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password, key })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Không thể reset Key");
        }

        showToast("Đã reset máy khóa thành công!");
        await loadKeys({ resetPage: false });
    } catch (error) {
        showToast(error.message, true);
    }
}

/**
 * API call: Delete key
 */
async function deleteKey(key) {
    const password = getStoredPassword();
    if (!password) {
        showPasswordModal();
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password, key })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Không thể xóa Key");
        }

        showToast("Đã xóa Key thành công!");
        await loadKeys({ resetPage: false });
    } catch (error) {
        showToast(error.message, true);
    }
}

/**
 * Open Extend Modal
 */
function openExtendModal(name, product) {
    extendKeyNameInput.value = name;
    extendKeyProductInput.value = product;
    extendDisplayName.innerText = name;
    extendDisplayProduct.innerText = product === 'autohdr' ? 'AutoHDR' : 'Fotello';
    extendKeyModal.classList.add('is-active');
}

/**
 * Submit Gia Hạn (Extend Key) form
 */
extendKeyForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const password = getStoredPassword();
    const name = extendKeyNameInput.value;
    const product = extendKeyProductInput.value;
    const expiryType = document.querySelector('input[name="extend-expiry"]:checked').value;

    let days = null;
    if (expiryType === 'days') {
        const daysInput = document.getElementById('extend-days-input').value;
        days = parseInt(daysInput, 10);
        if (isNaN(days) || days <= 0) {
            showToast("Số ngày không hợp lệ.");
            return;
        }
    }

    const payload = {
        password,
        name,
        product,
        forever: expiryType === 'forever',
        days: days
    };

    btnExtendSubmit.disabled = true;
    btnExtendSubmit.innerText = "Đang gia hạn...";

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Gia hạn thất bại");
        }

        showToast("Gia hạn thành công!");
        extendKeyModal.classList.remove('is-active');
        await loadKeys({ resetPage: false });
    } catch (error) {
        showToast(error.message, true);
    } finally {
        btnExtendSubmit.disabled = false;
        btnExtendSubmit.innerText = "Xác nhận Gia hạn";
    }
});

/**
 * Submit Create Key Form
 */
keyForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const password = getStoredPassword();
    const name = keyNameInput.value.trim();
    const product = normalizeProduct(keyProductSelect.value);
    const expiryType = document.querySelector('input[name="expiry"]:checked').value;

    let days = null;
    if (expiryType === 'days') {
        const daysInput = document.getElementById('expiry-days-input').value;
        days = parseInt(daysInput, 10);
        if (isNaN(days) || days <= 0) {
            showToast("Số ngày không hợp lệ. Vui lòng nhập số dương.");
            return;
        }
    }

    const payload = {
        password,
        name,
        product,
        level: keyLevelSelect ? keyLevelSelect.value : 'lite',
        forever: expiryType === 'forever',
        days: days
    };

    btnCreate.disabled = true;
    btnCreate.innerText = "Đang tạo...";

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Không thể tạo Key");
        }

        const data = await response.json();
        const record = data.record;

        // Display results in modal
        displayKey.innerText = record.key;
        resultDiv.style.display = 'block';
        showToast("Tạo Key thành công!");

        await loadKeys();
    } catch (error) {
        showToast(error.message, true);
    } finally {
        btnCreate.disabled = false;
        btnCreate.innerText = "Tạo Key";
    }
});

/**
 * Export and Import
 */
btnExport.addEventListener('click', async () => {
    const password = getStoredPassword();
    if (!password) {
        showPasswordModal();
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });

        if (!response.ok) {
            let errorMsg = "Lấy file thất bại";
            try { const err = await response.json(); errorMsg = err.detail || errorMsg; } catch (e) { }
            throw new Error(errorMsg);
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'keys_export.json';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (error) {
        showToast("Export thất bại: " + error.message, true);
    }
});

inputImport.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const password = getStoredPassword();
    if (!password) {
        showPasswordModal();
        e.target.value = '';
        return;
    }

    const formData = new FormData();
    formData.append("password", password);
    formData.append("file", file);

    try {
        const response = await fetch(`${API_BASE}/api/admin/keys/import`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Không thể import Key");
        }

        const data = await response.json();
        showToast(data.message || "Import thành công!");
        await loadKeys();
    } catch (error) {
        showToast("Import thất bại: " + error.message, true);
    } finally {
        e.target.value = ''; // reset file input
    }
});

/**
 * Copy Key Button
 */
btnCopy.addEventListener('click', () => {
    const key = displayKey.innerText;
    navigator.clipboard.writeText(key).then(() => {
        btnCopy.innerText = "Copied!";
        setTimeout(() => btnCopy.innerText = "Copy", 2000);
    });
});

/**
 * Client-side Search and Filter Event Listeners
 */
searchNameInput.addEventListener('input', (e) => {
    searchQuery = e.target.value;
    currentKeysPage = 1;
    renderKeys();
});

filterProductSelect.addEventListener('change', (e) => {
    selectedProduct = e.target.value;
    currentKeysPage = 1;
    renderKeys();
});

filterStatusSelect.addEventListener('change', (e) => {
    selectedStatus = e.target.value;
    currentKeysPage = 1;
    renderKeys();
});

btnList.addEventListener('click', () => loadKeys());

// Load password or request it on window load
window.addEventListener('load', () => {
    const saved = getStoredPassword();
    if (saved) {
        adminPassInput.value = saved;
        loadKeys();
    } else {
        showPasswordModal();
    }
});
