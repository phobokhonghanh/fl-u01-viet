// Admin Dashboard Logic - AutoHDR Key Management

const API_BASE = import.meta.env?.VITE_API_BASE || "https://autohdr-backend.up.railway.app";

// DOM Elements
const keyForm = document.getElementById('key-form');
const adminPassInput = document.getElementById('admin-pass');
const keyNameInput = document.getElementById('key-name');
const btnCreate = document.getElementById('btn-create');
const btnList = document.getElementById('btn-list');
const btnCopy = document.getElementById('btn-copy');
const resultDiv = document.getElementById('new-key-result');
const displayKey = document.getElementById('display-key');
const keysBody = document.getElementById('keys-body');
const btnExport = document.getElementById('btn-export');
const inputImport = document.getElementById('import-file');

/**
 * Hiển thị thông báo Toast đơn giản
 */
function showToast(message, isError = false) {
    // Tạm thời dùng alert, có thể cải tiến UI sau
    alert(message);
}

/**
 * Liệt kê danh sách keys
 */
async function loadKeys() {
    const password = adminPassInput.value;
    if (!password) {
        showToast("Vui lòng nhập mật khẩu!");
        return;
    }

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
        renderKeys(keys);
    } catch (error) {
        showToast(error.message, true);
    }
}

/**
 * Render bảng danh sách keys
 */
function renderKeys(keys) {
    if (!keys || keys.length === 0) {
        keysBody.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 2rem; color: var(--text-light);">Chưa có Key nào được tạo.</td></tr>`;
        return;
    }

    keysBody.innerHTML = keys.map(k => {
        const expires = k.expires_at ? new Date(k.expires_at).toLocaleDateString('vi-VN') : 'Vĩnh viễn';
        const machine = k.machine_id ? `<span style="font-size: 0.7rem; color: var(--text-light);">${k.machine_id.substring(0, 15)}...</span>` : '<span style="color: var(--text-light);">Chưa dùng</span>';

        return `
            <tr style="border-bottom: 1px solid var(--border);">
                <td style="padding: 0.75rem;">${k.name}</td>
                <td style="padding: 0.75rem;"><code style="background: #f1f5f9; padding: 2px 4px; border-radius: 4px; font-weight: 600;">${k.key}</code></td>
                <td style="padding: 0.75rem;">${expires}</td>
                <td style="padding: 0.75rem;">${machine}</td>
                <td style="padding: 0.75rem; text-align: center;">
                    <button class="btn-delete-key" data-key="${k.key}" style="background: none; border: none; color: #DC2626; cursor: pointer; font-weight: bold; font-size: 1.1rem;" title="Xóa key">&times;</button>
                </td>
            </tr>
        `;
    }).join('');

    // Attach event listeners to delete buttons
    document.querySelectorAll('.btn-delete-key').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            if (confirm('Bạn có chắc chắn muốn xóa Key này không?')) {
                await deleteKey(e.target.dataset.key);
            }
        });
    });
}

async function deleteKey(key) {
    const password = adminPassInput.value;
    if (!password) {
        showToast("Vui lòng nhập mật khẩu!");
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
        loadKeys();
    } catch (error) {
        showToast(error.message, true);
    }
}

/**
 * Tạo Key mới
 */
keyForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const password = adminPassInput.value;
    const name = keyNameInput.value;
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

        // Hiển thị kết quả
        displayKey.innerText = record.key;
        resultDiv.style.display = 'block';
        showToast("Tạo Key thành công!");

        // Cập nhật lại danh sách
        loadKeys();

    } catch (error) {
        showToast(error.message, true);
    } finally {
        btnCreate.disabled = false;
        btnCreate.innerText = "Tạo";
    }
});

btnList.addEventListener('click', loadKeys);

btnExport.addEventListener('click', async () => {
    const password = adminPassInput.value;
    if (!password) {
        showToast("Vui lòng nhập mật khẩu để tải keys!");
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

    const password = adminPassInput.value;
    if (!password) {
        showToast("Vui lòng nhập mật khẩu để import keys!");
        e.target.value = ''; // reset
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
        loadKeys();
    } catch (error) {
        showToast("Import thất bại: " + error.message, true);
    } finally {
        e.target.value = ''; // reset file input
    }
});

btnCopy.addEventListener('click', () => {
    const key = displayKey.innerText;
    navigator.clipboard.writeText(key).then(() => {
        btnCopy.innerText = "Coiped!";
        setTimeout(() => btnCopy.innerText = "Copy", 2000);
    });
});

// Lưu mật khẩu vào localStorage nếu đã nhập (tiện dụng cho admin)
adminPassInput.addEventListener('change', () => {
    sessionStorage.setItem('admin_password', adminPassInput.value);
});

// Load mật khẩu từ session nếu có
window.addEventListener('load', () => {
    const saved = sessionStorage.getItem('admin_password');
    if (saved) adminPassInput.value = saved;
});
