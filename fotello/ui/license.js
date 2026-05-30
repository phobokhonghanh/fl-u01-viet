const statusEl = document.getElementById('license-status');
const formEl = document.getElementById('license-form');
const inputEl = document.getElementById('license-key');
const buttonEl = document.getElementById('activate-btn');

function setStatus(message, type = '') {
  statusEl.textContent = message || '';
  statusEl.className = `status ${type}`.trim();
}

async function getApi() {
  if (window.pywebview?.api) return window.pywebview.api;
  return new Promise((resolve) => {
    window.addEventListener('pywebviewready', () => resolve(window.pywebview.api), { once: true });
  });
}

formEl.addEventListener('submit', async (event) => {
  event.preventDefault();
  const key = inputEl.value.trim();
  if (!key) {
    setStatus('Vui lòng nhập license key.', 'error');
    return;
  }

  buttonEl.disabled = true;
  setStatus('Đang kích hoạt license...');
  try {
    const api = await getApi();
    const result = await api.license_activate(key);
    if (result.ok) {
      setStatus(result.msg || 'Kích hoạt thành công, đang mở Fotello...', 'success');
      return;
    }
    setStatus(result.msg || 'Kích hoạt thất bại.', 'error');
  } catch (error) {
    setStatus(`Lỗi kích hoạt: ${error}`, 'error');
  } finally {
    buttonEl.disabled = false;
  }
});

window.addEventListener('DOMContentLoaded', async () => {
  try {
    const api = await getApi();
    const status = await api.license_status();
    if (status.ok) {
      setStatus('License đã active. Ứng dụng sẽ tự mở màn hình chính khi khởi động lại.', 'success');
    } else if (status.has_key) {
      setStatus(status.message || 'License cần được kiểm tra lại.', 'error');
    } else {
      setStatus('Chưa kích hoạt license.');
    }
  } catch {
    setStatus('Không đọc được trạng thái license.', 'error');
  }
});
