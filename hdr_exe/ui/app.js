/**
 * hdr_exe Production Desktop UI Controller
 * Integrates with pywebview BridgeApi via window.pywebview.api
 * Supports real-time step streaming, log console, sequential batch jobs, and offline safety.
 */

// Application State
const appState = {
  activeTab: 'autoenhance',
  config: {
    autoenhance_output_dir: '',
    fotello_output_dir: '',
    last_active_tab: 'autoenhance',
  },
  autoenhance: {
    license: null,       // 'lite' | 'plus' | null
    licenseInfo: null,
    serviceConnected: false,
    serviceMsg: '',
    taskMode: 'new',     // 'new' | 'download'
    execMode: 'single',  // 'single' | 'batch'
    inputDir: '',
    outputDir: '',
    inputFiles: 0,
    isRunning: false,
    selectedJobId: null,
    currentPane: 'steps',
    jobs: [],
    logs: [],
    runId: null,
  },
  fotello: {
    license: null,
    licenseInfo: null,
    serviceConnected: false,
    serviceMsg: '',
    taskMode: 'new',
    execMode: 'single',
    inputDir: '',
    outputDir: '',
    inputFiles: 0,
    bracketSize: 3,
    isRunning: false,
    selectedJobId: null,
    currentPane: 'steps',
    jobs: [],
    logs: [],
    runId: null,
    listings: [],
    selectedListingIds: new Set(),
  },
  autohdr: {
    license: null,
    licenseInfo: null,
  }
};

// Standard Step Definitions
const AUTOENHANCE_STEPS = [
  { key: 'prepare', name: '1. Chuẩn bị ảnh', desc: 'Đọc và chuyển đổi định dạng ảnh đầu vào' },
  { key: 'create_order', name: '2. Tạo đơn hàng', desc: 'Gửi yêu cầu tạo đơn lên máy chủ Autoenhance' },
  { key: 'upload', name: '3. Tải ảnh lên S3', desc: 'Tải các tệp ảnh lên lưu trữ đám mây' },
  { key: 'execute', name: '4. Bắt đầu xử lý', desc: 'Kích hoạt pipeline xử lý HDR từ máy chủ' },
  { key: 'polling', name: '5. Chờ xử lý (Polling)', desc: 'Theo dõi tiến trình hoàn tất từ máy chủ' },
  { key: 'download', name: '6. Tải ảnh kết quả', desc: 'Tải và kiểm tra tính toàn vẹn (Pillow verify)' },
  { key: 'export', name: '7. Xuất kết quả', desc: 'Lưu vào thư mục đích và ghi nhận manifest' },
];

const AUTOENHANCE_DOWNLOAD_STEPS = [
  { key: 'auth', name: '1. Xác thực đơn hàng', desc: 'Kiểm tra mã đơn hàng và quyền truy cập' },
  { key: 'download', name: '2. Tải ảnh kết quả', desc: 'Tải ảnh vào thư mục con theo Order ID' },
  { key: 'verify', name: '3. Kiểm tra toàn vẹn', desc: 'Xác thực định dạng và độ nét bằng Pillow' },
  { key: 'manifest', name: '4. Xuất Manifest', desc: 'Ghi file order_manifest.json' },
];

const FOTELLO_STEPS = [
  { key: 'auth', name: '1. Xác thực dịch vụ', desc: 'Kiểm tra phiên đăng nhập Fotello qua Chrome' },
  { key: 'prepare', name: '2. Phân nhóm Bracket', desc: 'Nhóm các phơi sáng theo EV và kiểm tra file' },
  { key: 'upload', name: '3. Tải ảnh lên S3', desc: 'Tải các file trong từng bracket lên S3' },
  { key: 'execute', name: '4. Khởi chạy xử lý', desc: 'Yêu cầu Fotello AI ghép ảnh HDR' },
  { key: 'polling', name: '5. Chờ ghép HDR', desc: 'Theo dõi trạng thái tiến độ xử lý listing' },
  { key: 'download', name: '6. Tải ảnh kết quả', desc: 'Tải rendition và kiểm tra ảnh (Pillow)' },
  { key: 'export', name: '7. Xuất kết quả', desc: 'Lưu vào thư mục đích và cập nhật checkpoint' },
];

const FOTELLO_DOWNLOAD_STEPS = [
  { key: 'auth', name: '1. Xác thực dịch vụ', desc: 'Kiểm tra phiên đăng nhập và Team ID' },
  { key: 'metadata', name: '2. Lấy metadata Listing', desc: 'Đọc thông tin Firestore và danh sách file ảnh' },
  { key: 'download_subfolders', name: '3. Tải vào thư mục con', desc: 'Tải tuần tự ảnh vào từng thư mục con theo listing_id' },
  { key: 'verify', name: '4. Kiểm tra toàn vẹn', desc: 'Xác thực định dạng ảnh và độ phân giải qua Pillow' },
  { key: 'manifest', name: '5. Xuất Manifest tổng', desc: 'Ghi file batch_listings_manifest.json' },
];

// Helper: safe call to pywebview.api
async function callPy(methodName, ...args) {
  if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[methodName] === 'function') {
    try {
      return await window.pywebview.api[methodName](...args);
    } catch (err) {
      console.error(`Pywebview error calling ${methodName}:`, err);
      showToast(`Lỗi Bridge (${methodName}): ${err.message || err}`, 'error');
      return null;
    }
  }
  console.warn(`pywebview.api.${methodName} not available (running outside pywebview?)`);
  return null;
}

// Initialization
document.addEventListener('DOMContentLoaded', () => {
  if (window.pywebview && window.pywebview.api) {
    initApp();
  } else {
    window.addEventListener('pywebviewready', () => {
      initApp();
    });
    // Timeout fallback for previewing in normal browser
    setTimeout(() => {
      if (!window.pywebview) {
        console.info('Browser preview mode: pywebview not detected.');
        initAppFallback();
      }
    }, 600);
  }
});

async function initApp() {
  handleFotelloSkyToggle();

  // 1. Load saved config & directories
  const cfg = await callPy('get_app_config');
  if (cfg) {
    appState.config = cfg;
    if (cfg.autoenhance_output_dir) {
      appState.autoenhance.outputDir = cfg.autoenhance_output_dir;
      const aeOut = document.getElementById('ae-global-output-dir');
      if (aeOut) aeOut.value = cfg.autoenhance_output_dir;
    }
    if (cfg.fotello_output_dir) {
      appState.fotello.outputDir = cfg.fotello_output_dir;
      const foOut = document.getElementById('fo-global-output-dir');
      if (foOut) foOut.value = cfg.fotello_output_dir;
    }
    if (cfg.last_active_tab && ['autoenhance', 'fotello', 'autohdr'].includes(cfg.last_active_tab)) {
      switchEngineTab(cfg.last_active_tab);
    }
  }

  // 2. Load License status for all 3 engines
  await refreshAllLicenses();

  // 3. Load Service Auth status for Autoenhance & Fotello
  await refreshAuthStatus('autoenhance');
  await refreshAuthStatus('fotello');

  // 4. Initial preflight estimation
  updateEstimation('autoenhance');
  updateEstimation('fotello');
}

function initAppFallback() {
  handleFotelloSkyToggle();
  appState.autoenhance.outputDir = '/home/user/Pictures/Autoenhance_Out';
  appState.fotello.outputDir = '/home/user/Pictures/Fotello_Downloads';
  const aeOut = document.getElementById('ae-global-output-dir');
  if (aeOut) aeOut.value = appState.autoenhance.outputDir;
  const foOut = document.getElementById('fo-global-output-dir');
  if (foOut) foOut.value = appState.fotello.outputDir;
  updateEstimation('autoenhance');
  updateEstimation('fotello');
}

// Top Engine Tabs Switcher
function switchEngineTab(engine) {
  appState.activeTab = engine;

  document.querySelectorAll('.engine-tab-btn').forEach(b => b.classList.remove('active'));
  const activeBtn = document.getElementById(`tabBtn-${engine}`);
  if (activeBtn) activeBtn.classList.add('active');

  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const activeContent = document.getElementById(`tabContent-${engine}`);
  if (activeContent) activeContent.classList.add('active');

  callPy('save_app_config', { last_active_tab: engine });
}

// Task Mode Switcher: 'new' vs 'download'
function switchTaskMode(engine, mode) {
  const state = appState[engine];
  state.taskMode = mode;

  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const newBtn = document.getElementById(`${prefix}-mode-new-btn`);
  const dlBtn = document.getElementById(`${prefix}-mode-download-btn`);
  const formNew = document.getElementById(`${prefix}-form-new`);
  const formDl = document.getElementById(`${prefix}-form-download`);
  const runBtn = document.getElementById(`${prefix}-run-btn`);
  const footerStatus = document.getElementById(`${prefix}-footer-status`);

  if (mode === 'new') {
    newBtn.classList.add('active');
    dlBtn.classList.remove('active');
    formNew.style.display = 'flex';
    formDl.style.display = 'none';
    if (runBtn) {
      runBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg><span>Bắt đầu xử lý</span>`;
      runBtn.onclick = () => startEngineTask(engine);
    }
    if (footerStatus) {
      footerStatus.innerText = `Sẵn sàng tạo tác vụ ${engine === 'fotello' ? 'Fotello' : 'Autoenhance'}`;
    }
  } else {
    newBtn.classList.remove('active');
    dlBtn.classList.add('active');
    formNew.style.display = 'none';
    formDl.style.display = 'flex';
    if (runBtn) {
      runBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>Tải kết quả (Tạo Job)</span>`;
      runBtn.onclick = engine === 'fotello' ? () => startDownloadListingsAsJob('fotello') : () => startDownloadOrderAsJob('autoenhance');
    }
    if (footerStatus) {
      footerStatus.innerText = engine === 'fotello' ? 'Chế độ tải Listing từ Firestore' : 'Chế độ tải đơn hàng Autoenhance';
    }
    // For Fotello: fetch listings if not already populated
    if (engine === 'fotello' && state.listings.length === 0) {
      refreshFotelloListings();
    }
  }
}

// Collapsible Sections
function toggleCollapsible(bodyId, triggerElem) {
  const body = document.getElementById(bodyId);
  const arrow = triggerElem.querySelector('.arrow');
  if (body.classList.contains('open')) {
    body.classList.remove('open');
    if (arrow) arrow.innerText = '▼';
  } else {
    body.classList.add('open');
    if (arrow) arrow.innerText = '▲';
  }
}

// Folder Selection via pywebview Native File Dialog
async function chooseOutputDir(engine) {
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const current = appState[engine].outputDir || '';
  const selected = await callPy('select_folder', current);
  if (selected) {
    appState[engine].outputDir = selected;
    const input = document.getElementById(`${prefix}-global-output-dir`);
    if (input) input.value = selected;
    
    // Persist in config
    const updateKey = engine === 'autoenhance' ? 'autoenhance_output_dir' : 'fotello_output_dir';
    await callPy('save_app_config', { [updateKey]: selected });
    showToast(`💾 Đã cập nhật thư mục lưu kết quả: <strong>${escapeHtml(selected)}</strong>`, 'success');
  }
}

async function chooseInputFolder(engine) {
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const current = appState[engine].inputDir || '';
  const selected = await callPy('select_folder', current);
  if (selected) {
    appState[engine].inputDir = selected;
    const input = document.getElementById(`${prefix}-input-path`);
    if (input) input.value = selected;

    // Inspect folder contents
    const bracketSize = engine === 'fotello' 
      ? (parseInt(document.getElementById('fo-bracket-select')?.value, 10) || 3) 
      : 1;
    
    const info = await callPy('inspect_input_folder', engine, selected, bracketSize);
    if (info && info.valid) {
      appState[engine].inputFiles = info.inputs;
      showToast(`📁 Đã nhận diện ${info.inputs} tệp ảnh (${info.outputs} outputs dự kiến).`, 'info');
    } else {
      appState[engine].inputFiles = 0;
      if (info && info.error) {
        showToast(`⚠️ ${info.error}`, 'warning');
      }
    }
    updateEstimation(engine);
  }
}

function openOutputDirectoryCurrent() {
  const engine = appState.activeTab;
  const dir = appState[engine]?.outputDir || '';
  if (dir) {
    callPy('open_folder', dir);
  } else {
    showToast('Chưa cấu hình thư mục xuất ảnh.', 'warning');
  }
}

function openActiveJobFolder(engine) {
  const state = appState[engine];
  const job = state.jobs.find(j => j.jobId === state.selectedJobId);
  const targetDir = job?.output_dir || state.outputDir;
  if (targetDir) {
    callPy('open_folder', targetDir);
  } else {
    showToast('Chưa có thư mục đầu ra cho job này.', 'warning');
  }
}

function openJobFolder(engine, jobId) {
  const state = appState[engine];
  const job = state.jobs.find(j => j.jobId === jobId);
  const targetDir = job?.output_dir || state.outputDir;
  if (targetDir) {
    callPy('open_folder', targetDir);
  } else {
    showToast('Chưa có thư mục đầu ra cho job này.', 'warning');
  }
}

// Live Preflight Estimation
function updateEstimation(engine) {
  const state = appState[engine];
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';

  const singleRadio = document.getElementById(`${prefix}-radio-single`);
  const batchRadio = document.getElementById(`${prefix}-radio-batch`);
  state.execMode = (batchRadio && batchRadio.checked) ? 'batch' : 'single';

  let inputCount = state.inputFiles;
  let outputCount = inputCount;
  let bracketSize = 1;

  if (engine === 'fotello') {
    const sel = document.getElementById('fo-bracket-select');
    bracketSize = sel ? parseInt(sel.value, 10) : 3;
    if (!bracketSize || isNaN(bracketSize)) bracketSize = 3;
    state.bracketSize = bracketSize;
    outputCount = Math.floor(inputCount / bracketSize);
  }

  let jobCount = 0;
  if (outputCount > 0) {
    if (state.execMode === 'single') {
      jobCount = 1;
    } else {
      const perJob = engine === 'autoenhance' ? 10 : 5;
      jobCount = Math.ceil(outputCount / perJob) || 1;
    }
  }

  const estIn = document.getElementById(`${prefix}-est-inputs`);
  if (estIn) estIn.innerText = inputCount;
  const estOut = document.getElementById(`${prefix}-est-outputs`);
  if (estOut) {
    estOut.innerText = engine === 'fotello' 
      ? `${outputCount} (${bracketSize} ảnh/bracket)` 
      : outputCount;
  }
  const estJobs = document.getElementById(`${prefix}-est-jobs`);
  if (estJobs) estJobs.innerText = `${jobCount} Job${jobCount > 1 ? 's' : ''}`;

  // Capacity validation for Lite
  const estBadge = document.getElementById(`${prefix}-est-badge`);
  const maxLite = engine === 'autoenhance' ? 20 : 15;
  if (state.license === 'lite' && outputCount > maxLite && state.execMode === 'single') {
    if (estBadge) {
      estBadge.className = 'badge badge-failed';
      estBadge.innerText = `Vượt giới hạn Lite (${outputCount}/${maxLite})`;
    }
  } else {
    if (estBadge) {
      estBadge.className = 'badge badge-success';
      estBadge.innerText = 'Sẵn sàng';
    }
  }
}

// License Checking & Badges
async function refreshAllLicenses() {
  await refreshLicenseStatus('autoenhance');
  await refreshLicenseStatus('fotello');
  await refreshLicenseStatus('autohdr');
}

async function refreshLicenseStatus(engine) {
  const prefix = engine === 'autoenhance' ? 'ae' : (engine === 'fotello' ? 'fo' : 'ah');
  const badge = document.getElementById(`${prefix}-license-badge`);
  const res = await callPy('get_license_status', engine);

  if (res && res.valid) {
    const level = (res.level || '').toLowerCase();
    appState[engine].license = level === 'plus' ? 'plus' : 'lite';
    appState[engine].licenseInfo = res;
    if (badge) {
      badge.className = `badge badge-${level === 'plus' ? 'plus' : 'lite'}`;
      badge.innerText = level === 'plus' ? 'Gói PLUS (Toàn quyền)' : 'Gói LITE (Hợp lệ)';
    }
  } else {
    appState[engine].license = null;
    appState[engine].licenseInfo = res;
    if (badge) {
      badge.className = 'badge badge-unregistered';
      badge.innerText = 'Chưa kích hoạt';
    }
  }

  // Update batch lock radio based on license
  const batchRadio = document.getElementById(`${prefix}-radio-batch`);
  const lockedBanner = document.getElementById(`${prefix}-batch-locked-banner`);
  const singleRadio = document.getElementById(`${prefix}-radio-single`);

  if (batchRadio) {
    if (appState[engine].license === 'plus') {
      batchRadio.disabled = false;
      if (lockedBanner) lockedBanner.style.display = 'none';
    } else {
      batchRadio.disabled = true;
      if (batchRadio.checked && singleRadio) singleRadio.checked = true;
      if (lockedBanner) lockedBanner.style.display = 'flex';
    }
  }

  updateEstimation(engine);
}

// Service Auth Checking & Badges
async function refreshAuthStatus(engine) {
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const badge = document.getElementById(`${prefix}-service-badge`);
  const res = await callPy('get_auth_status', engine);

  if (res && res.connected) {
    appState[engine].serviceConnected = true;
    appState[engine].serviceMsg = res.message || 'Đã kết nối';
    if (badge) {
      badge.className = 'badge badge-success';
      badge.innerText = res.message ? `Đã kết nối (${res.message})` : 'Đã kết nối (Chrome Profile)';
    }
  } else {
    appState[engine].serviceConnected = false;
    appState[engine].serviceMsg = res?.message || 'Chưa đăng nhập';
    if (badge) {
      badge.className = 'badge badge-unregistered';
      badge.innerText = res?.message || 'Chưa đăng nhập';
    }
  }
}

// Fotello Listings & Download Selection
async function refreshFotelloListings() {
  const tbody = document.getElementById('fo-listings-tbody');
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-subtle); padding: 20px;">Đang tải danh sách listing từ Firestore...</td></tr>`;
  }
  const res = await callPy('fetch_fotello_listings');
  if (res && res.success) {
    appState.fotello.listings = res.listings || [];
    renderFotelloListings();
    showToast(`Đã tải ${res.listings.length} listing từ Firestore.`, 'info');
  } else {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--danger); padding: 20px;">${escapeHtml(res?.message || 'Không thể tải danh sách listing')}</td></tr>`;
    }
  }
}

function renderFotelloListings() {
  const tbody = document.getElementById('fo-listings-tbody');
  if (!tbody) return;

  const searchInput = document.getElementById('fo-listings-search');
  const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
  const filterSelect = document.getElementById('fo-listings-filter');
  const filterVal = filterSelect ? filterSelect.value : 'all';

  const filtered = appState.fotello.listings.filter(item => {
    const matchQuery = !query || item.name.toLowerCase().includes(query) || item.id.toLowerCase().includes(query);
    const matchStatus = filterVal === 'all' || item.status === filterVal;
    return matchQuery && matchStatus;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-subtle); padding: 20px;">Không tìm thấy listing phù hợp.</td></tr>`;
    return;
  }

  tbody.innerHTML = '';
  filtered.forEach(item => {
    const isChecked = appState.fotello.selectedListingIds.has(item.id);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="text-align: center;"><input type="checkbox" ${isChecked ? 'checked' : ''} onchange="toggleSelectListing('${item.id}', this.checked)"></td>
      <td><strong>${escapeHtml(item.name)}</strong><br><span style="font-size: 10px; color: var(--text-muted);">${item.id}</span></td>
      <td>${item.date ? item.date.slice(0, 16) : '—'}</td>
      <td style="text-align: center;"><strong>${item.outputsCount}</strong></td>
      <td style="text-align: center;"><span class="badge badge-${item.status === 'completed' ? 'success' : 'warning'}">${escapeHtml(item.statusText || item.status)}</span></td>
    `;
    tbody.appendChild(tr);
  });

  updateFotelloSelectedCount();
}

function toggleSelectListing(id, isChecked) {
  if (isChecked) {
    appState.fotello.selectedListingIds.add(id);
  } else {
    appState.fotello.selectedListingIds.delete(id);
  }
  updateFotelloSelectedCount();
}

function toggleSelectAllListings(isChecked) {
  if (isChecked) {
    appState.fotello.listings.forEach(l => appState.fotello.selectedListingIds.add(l.id));
  } else {
    appState.fotello.selectedListingIds.clear();
  }
  renderFotelloListings();
}

function updateFotelloSelectedCount() {
  const selectedCount = appState.fotello.selectedListingIds.size;
  const totalListings = appState.fotello.listings.length;
  let totalImgs = 0;
  appState.fotello.listings.forEach(l => {
    if (appState.fotello.selectedListingIds.has(l.id)) {
      totalImgs += (l.outputsCount || 0);
    }
  });

  const countElem = document.getElementById('fo-selected-listings-count');
  if (countElem) {
    countElem.innerText = `Đã chọn: ${selectedCount} / ${totalListings} listing (${totalImgs} ảnh)`;
  }
}

function filterFotelloListings() {
  renderFotelloListings();
}

function handleFotelloSkyToggle() {
  const skySel = document.getElementById('fo-sky-select');
  const cloudSel = document.getElementById('fo-cloud-select');
  const hintElem = document.getElementById('fo-sky-off-hint');
  const isOff = skySel && skySel.value === 'off';

  if (cloudSel) cloudSel.disabled = isOff;
  if (hintElem) hintElem.style.display = isOff ? 'block' : 'none';
}

// Log Console
function appendLog(engine, text, level = 'info') {
  const state = appState[engine];
  const now = new Date().toTimeString().split(' ')[0];
  state.logs.push({ time: now, text, level });

  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const container = document.getElementById(`${prefix}-log-entries`);
  if (!container) return;

  const row = document.createElement('div');
  row.className = `log-entry ${level}`;
  row.innerHTML = `<span class="log-time">[${now}]</span>[${level.toUpperCase()}] ${escapeHtml(text)}`;
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

function clearLogs(engine) {
  appState[engine].logs = [];
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const c = document.getElementById(`${prefix}-log-entries`);
  if (c) c.innerHTML = '';
}

function copyLogs(engine) {
  const text = appState[engine].logs.map(l => `[${l.time}][${l.level.toUpperCase()}] ${l.text}`).join('\n');
  navigator.clipboard.writeText(text).then(() => showToast('Đã sao chép log vào clipboard!', 'info'));
}

// Jobs Table & Active Job Details Rendering
function renderJobsTable(engine) {
  const state = appState[engine];
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const tbody = document.getElementById(`${prefix}-jobs-tbody`);
  if (!tbody) return;

  if (state.jobs.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-subtle); padding: 24px;">
          Chưa có job nào trong đợt này. Nhấn "Bắt đầu xử lý" để khởi tạo.
        </td>
      </tr>
    `;
    const detailCard = document.getElementById(`${prefix}-job-detail-card`);
    if (detailCard) detailCard.style.display = 'none';
    return;
  }

  if (!state.selectedJobId && state.jobs.length > 0) {
    state.selectedJobId = state.jobs[0].jobId;
  }

  tbody.innerHTML = '';
  state.jobs.forEach(job => {
    const tr = document.createElement('tr');
    if (job.jobId === state.selectedJobId) tr.classList.add('selected');

    let badgeClass = 'badge-queued';
    let statusText = 'Đang chờ';
    if (job.status === 'running') { badgeClass = 'badge-lite'; statusText = 'Đang chạy'; }
    else if (job.status === 'success') { badgeClass = 'badge-success'; statusText = 'Hoàn tất'; }
    else if (job.status === 'partial') { badgeClass = 'badge-warning'; statusText = 'Một phần'; }
    else if (job.status === 'failed') { badgeClass = 'badge-failed'; statusText = 'Lỗi'; }
    else if (job.status === 'cancelled') { badgeClass = 'badge-cancelled'; statusText = 'Đã hủy'; }

    let actionBtn = '';
    if (job.status === 'failed' || job.status === 'cancelled') {
      actionBtn = `<button class="btn btn-secondary btn-sm" onclick="restartJobDirect('${engine}', '${job.jobId}')" title="Khởi động lại job này">🔄 Restart</button>`;
    } else if (job.status === 'success' || job.status === 'partial') {
      actionBtn = `
        <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); openJobFolder('${engine}', '${job.jobId}')" title="Mở thư mục">📂 Thư mục</button>
        <button class="btn btn-secondary btn-sm" onclick="selectJob('${engine}', '${job.jobId}')">👁️ Xem</button>
      `;
    } else {
      actionBtn = `<button class="btn btn-secondary btn-sm" onclick="selectJob('${engine}', '${job.jobId}')">👁️ Xem</button>`;
    }

    tr.innerHTML = `
      <td><strong>${job.jobId}</strong></td>
      <td>Lần ${job.attemptNo || 1}</td>
      <td>${job.outputsDone || 0}/${job.outputsTotal || 0}</td>
      <td>${escapeHtml(job.currentStepName || job.step || 'Khởi tạo')}</td>
      <td><span class="badge ${badgeClass}">${statusText}</span></td>
      <td style="text-align: right;">${actionBtn}</td>
    `;
    tr.onclick = (e) => {
      if (e.target.tagName !== 'BUTTON') {
        selectJob(engine, job.jobId);
      }
    };
    tbody.appendChild(tr);
  });

  if (state.selectedJobId) {
    renderJobDetails(engine, state.selectedJobId);
  }
}

function selectJob(engine, jobId) {
  const state = appState[engine];
  state.selectedJobId = jobId;
  renderJobsTable(engine);
}

function renderJobDetails(engine, jobId) {
  const state = appState[engine];
  const job = state.jobs.find(j => j.jobId === jobId);
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const detailCard = document.getElementById(`${prefix}-job-detail-card`);
  if (!detailCard || !job) return;

  detailCard.style.display = 'flex';
  document.getElementById(`${prefix}-detail-job-id`).innerText = job.jobId;
  document.getElementById(`${prefix}-detail-attempt-badge`).innerText = `Lần chạy ${job.attemptNo || 1}`;
  document.getElementById(`${prefix}-detail-outputs-text`).innerText = `${job.outputsTotal || 0} outputs (${job.outputsDone || 0} hoàn thành)`;
  document.getElementById(`${prefix}-detail-outputs-count`).innerText = job.outputsTotal || 0;

  // Restart Banner
  const restartBanner = document.getElementById(`${prefix}-restart-banner`);
  const restartTitle = document.getElementById(`${prefix}-restart-title`);
  const restartDesc = document.getElementById(`${prefix}-restart-desc`);

  if (job.status === 'failed' || job.status === 'cancelled') {
    restartBanner.style.display = 'flex';
    const isPrePolling = ['prepare', 'auth', 'create_order', 'upload'].includes(job.step);
    if (isPrePolling) {
      restartTitle.innerText = 'Chạy lại job này từ đầu:';
      restartDesc.innerText = `Lỗi ở bước [${job.currentStepName || job.step}]. Khởi chạy lại từ bước đầu tiên.`;
    } else {
      restartTitle.innerText = 'Tiếp tục từ bước bị lỗi:';
      restartDesc.innerText = `Lỗi ở bước [${job.currentStepName || job.step}]. Giữ nguyên tài nguyên máy chủ và tải tiếp ảnh chưa hoàn tất.`;
    }
  } else {
    restartBanner.style.display = 'none';
  }

  // Stepper Checklist
  const stepsDef = engine === 'autoenhance' ? AUTOENHANCE_STEPS : FOTELLO_STEPS;
  const stepperContainer = document.getElementById(`${prefix}-steps-container`);
  stepperContainer.innerHTML = '';

  const stepKeys = stepsDef.map(s => s.key);
  const currentStepIdx = stepKeys.indexOf(job.step);

  stepsDef.forEach((st, idx) => {
    let cardClass = 'step-card';
    let statusBadge = '<span class="badge badge-queued">Chờ</span>';

    if (job.status === 'success') {
      cardClass += ' success';
      statusBadge = '<span class="badge badge-success">✓ Hoàn tất</span>';
    } else if (job.status === 'failed' && idx === currentStepIdx) {
      cardClass += ' failed';
      statusBadge = '<span class="badge badge-failed">✗ Lỗi</span>';
    } else if (job.status === 'cancelled' && idx === currentStepIdx) {
      cardClass += ' failed';
      statusBadge = '<span class="badge badge-cancelled">⊘ Đã hủy</span>';
    } else if (currentStepIdx > idx) {
      cardClass += ' success';
      statusBadge = '<span class="badge badge-success">✓ Hoàn tất</span>';
    } else if (idx === currentStepIdx && job.status === 'running') {
      cardClass += ' running';
      statusBadge = '<span class="badge badge-lite">Đang chạy...</span>';
    } else if (idx === currentStepIdx && job.status === 'partial') {
      cardClass += ' warning';
      statusBadge = '<span class="badge badge-warning">Một phần</span>';
    }

    const card = document.createElement('div');
    card.className = cardClass;
    card.innerHTML = `
      <div class="step-card-left">
        <div class="step-num">${idx + 1}</div>
        <div class="step-meta">
          <div class="step-name">${st.name}</div>
          <div class="step-desc">${st.desc}</div>
        </div>
      </div>
      <div>${statusBadge}</div>
    `;
    stepperContainer.appendChild(card);
  });

  // Outputs Table
  const outputsTbody = document.getElementById(`${prefix}-outputs-tbody`);
  outputsTbody.innerHTML = '';
  const outputsList = job.outputs || [];
  if (outputsList.length === 0) {
    outputsTbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-subtle); padding: 16px;">Đang chuẩn bị outputs...</td></tr>`;
  } else {
    outputsList.forEach(out => {
      const tr = document.createElement('tr');
      let outBadge = 'badge-queued';
      let outText = 'Đang chờ';
      if (out.status === 'success') { outBadge = 'badge-success'; outText = 'Hoàn tất'; }
      else if (out.status === 'failed') { outBadge = 'badge-failed'; outText = 'Lỗi'; }
      else if (out.status === 'running') { outBadge = 'badge-lite'; outText = 'Đang xử lý'; }

      tr.innerHTML = `
        <td><strong>${escapeHtml(out.id || out.output_id || '')}</strong></td>
        <td><code>${escapeHtml(out.inputName || (out.input_files ? out.input_files.join(', ') : '—'))}</code></td>
        <td><code>${escapeHtml(out.outputName || '—')}</code></td>
        <td><span class="badge ${outBadge}">${outText}</span></td>
      `;
      outputsTbody.appendChild(tr);
    });
  }
}

function switchJobPane(engine, pane) {
  const state = appState[engine];
  state.currentPane = pane;
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';

  ['steps', 'outputs', 'logs'].forEach(p => {
    const btn = document.getElementById(`${prefix}-panebtn-${p}`);
    const div = document.getElementById(`${prefix}-pane-${p}`);
    if (btn && div) {
      if (p === pane) {
        btn.classList.add('active');
        div.classList.add('active');
      } else {
        btn.classList.remove('active');
        div.classList.remove('active');
      }
    }
  });
}

// Start Engine Task
async function startEngineTask(engine) {
  const state = appState[engine];
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';

  // 1. Validate Input & Output Folders
  if (!state.inputDir) {
    showToast(`Vui lòng chọn Thư mục ảnh đầu vào cho ${engine.toUpperCase()}.`, 'warning');
    return;
  }
  if (!state.outputDir) {
    showToast(`Vui lòng chọn Thư mục lưu kết quả cho ${engine.toUpperCase()}.`, 'warning');
    return;
  }

  // 2. Validate License
  if (!state.license) {
    showToast(`Chưa kích hoạt bản quyền cho ${engine.toUpperCase()}. Vui lòng kích hoạt key.`, 'error');
    openLicenseModal(engine);
    return;
  }

  // 3. Prepare payload options
  let options = {
    input_dir: state.inputDir,
    output_dir: state.outputDir,
    exec_mode: state.execMode,
  };

  if (engine === 'autoenhance') {
    options = {
      ...options,
      ai_version: document.getElementById('ae-ai-version-select')?.value || 'latest',
      preset: document.getElementById('ae-preset-select')?.value || 'vivid',
      sky_replacement: document.getElementById('ae-sky-select')?.value || 'clear',
      perspective_correction: Boolean(document.getElementById('ae-persp-check')?.checked),
      lens_correction: Boolean(document.getElementById('ae-lens-check')?.checked),
      privacy: Boolean(document.getElementById('ae-effect-privacy')?.checked),
      window_pull: Boolean(document.getElementById('ae-effect-window')?.checked),
      grass: Boolean(document.getElementById('ae-effect-grass')?.checked),
      tv: Boolean(document.getElementById('ae-effect-tv')?.checked),
      fireplace: Boolean(document.getElementById('ae-effect-fireplace')?.checked),
      photographer: Boolean(document.getElementById('ae-effect-photographer')?.checked),
    };
  } else if (engine === 'fotello') {
    const isSkyOn = document.getElementById('fo-sky-select')?.value === 'on';
    options = {
      ...options,
      bracket_size: parseInt(document.getElementById('fo-bracket-select')?.value, 10) || 3,
      contrast_style: document.getElementById('fo-contrast-select')?.value || 'signature',
      sky_replacement: isSkyOn,
      cloud_style: isSkyOn ? (document.getElementById('fo-cloud-select')?.value || 'full_house_puffs') : null,
      listing_name_prefix: document.getElementById('fo-listing-prefix')?.value || 'HDR Upload',
      perspective: Boolean(document.getElementById('fo-persp-check')?.checked),
      rendition: document.getElementById('fo-rendition-select')?.value || 'highres',
      upsize: document.getElementById('fo-upsize-select')?.value || '1x',
    };
  }

  // 4. Update UI to running
  state.isRunning = true;
  state.jobs = [];
  state.logs = [];
  clearLogs(engine);

  const tabBadge = document.getElementById(`tabBadge-${engine}`);
  if (tabBadge) {
    tabBadge.className = 'tab-status-pill running';
    tabBadge.innerHTML = '<span class="pulse-dot"></span> Đang chạy';
  }
  const batchBadge = document.getElementById(`${prefix}-batch-badge`);
  if (batchBadge) {
    batchBadge.className = 'badge badge-lite';
    batchBadge.innerText = 'Đang chạy';
  }
  document.getElementById(`${prefix}-run-btn`).style.display = 'none';
  document.getElementById(`${prefix}-stop-btn`).style.display = 'inline-flex';
  document.getElementById(`${prefix}-footer-status`).innerText = 'Đang chuẩn bị tác vụ...';

  renderJobsTable(engine);

  // 5. Call backend
  const res = await callPy('start_engine_task', engine, options);
  if (res && res.success) {
    state.runId = res.run_id;
    document.getElementById(`${prefix}-footer-status`).innerText = `Đang chạy phiên: ${res.run_id}`;
    appendLog(engine, `Khởi chạy thành công phiên ${res.run_id}`, 'info');

    // Load initial planned jobs and outputs from backend store immediately
    const backendJobs = await callPy('get_run_jobs', engine, res.run_id);
    if (backendJobs && backendJobs.length > 0) {
      state.jobs = backendJobs.map(bj => ({
        jobId: bj.job_id,
        runId: bj.run_id,
        attemptNo: bj.current_attempt || 1,
        status: bj.status || 'running',
        step: bj.latest_step || 'prepare',
        currentStepName: bj.latest_step || 'prepare',
        outputsTotal: bj.outputs_total,
        outputsDone: bj.outputs_succeeded,
        outputsFailed: bj.outputs_failed,
        outputs: bj.outputs || [],
        output_dir: bj.output_dir,
      }));
      state.selectedJobId = state.jobs[0].jobId;
      renderJobsTable(engine);
    }
  } else {
    state.isRunning = false;
    if (tabBadge) {
      tabBadge.className = 'tab-status-pill idle';
      tabBadge.innerText = 'Sẵn sàng';
    }
    document.getElementById(`${prefix}-run-btn`).style.display = 'inline-flex';
    document.getElementById(`${prefix}-stop-btn`).style.display = 'none';
    showToast(`Không thể khởi chạy: ${res?.message || 'Lỗi không xác định'}`, 'error');
  }
}

// Download Tasks
async function startDownloadOrderAsJob(engine) {
  const orderId = document.getElementById('ae-order-id-input')?.value.trim();
  if (!orderId) {
    showToast('Vui lòng nhập Order ID để tải.', 'warning');
    return;
  }
  const outDir = appState.autoenhance.outputDir;
  if (!outDir) {
    showToast('Vui lòng chọn Thư mục lưu kết quả.', 'warning');
    return;
  }

  const options = {
    order_id: orderId,
    output_dir: outDir,
    format: document.getElementById('ae-dl-format')?.value || 'jpeg',
  };

  appState.autoenhance.isRunning = true;
  document.getElementById('ae-run-btn').style.display = 'none';
  document.getElementById('ae-stop-btn').style.display = 'inline-flex';

  const res = await callPy('start_download_task', 'autoenhance', options);
  if (res && res.success) {
    appState.autoenhance.runId = res.run_id;
    showToast(`Đang tải đơn hàng ${orderId}...`, 'info');
  } else {
    appState.autoenhance.isRunning = false;
    document.getElementById('ae-run-btn').style.display = 'inline-flex';
    document.getElementById('ae-stop-btn').style.display = 'none';
    showToast(`Lỗi: ${res?.message || 'Không thể tải'}`, 'error');
  }
}

async function startDownloadListingsAsJob(engine) {
  const selectedIds = Array.from(appState.fotello.selectedListingIds);
  if (selectedIds.length === 0) {
    showToast('Vui lòng tích chọn ít nhất 1 listing để tải về.', 'warning');
    return;
  }
  const outDir = appState.fotello.outputDir;
  if (!outDir) {
    showToast('Vui lòng chọn Thư mục lưu kết quả.', 'warning');
    return;
  }

  const options = {
    listing_ids: selectedIds,
    output_dir: outDir,
    rendition: document.getElementById('fo-dl-rendition')?.value || 'highres',
    upsize: document.getElementById('fo-dl-upsize')?.value || 'original',
  };

  appState.fotello.isRunning = true;
  document.getElementById('fo-run-btn').style.display = 'none';
  document.getElementById('fo-stop-btn').style.display = 'inline-flex';

  const res = await callPy('start_download_task', 'fotello', options);
  if (res && res.success) {
    appState.fotello.runId = res.run_id;
    appState.fotello.jobs = [{
      jobId: 'dl_job_001',
      runId: res.run_id,
      attemptNo: 1,
      status: 'running',
      step: 'init',
      currentStepName: 'Khởi tạo tải về',
      outputsTotal: selectedIds.length,
      outputsDone: 0,
      outputsFailed: 0,
      outputs: selectedIds.map(id => ({
        id: id,
        output_id: id,
        inputName: `Listing ${id}`,
        outputName: `${id}/`,
        status: 'queued',
      })),
      output_dir: outDir,
    }];
    appState.fotello.selectedJobId = 'dl_job_001';
    renderJobsTable('fotello');
    showToast(`Đã bắt đầu Job tải ${selectedIds.length} listing...`, 'info');
  } else {
    appState.fotello.isRunning = false;
    document.getElementById('fo-run-btn').style.display = 'inline-flex';
    document.getElementById('fo-stop-btn').style.display = 'none';
    showToast(`Lỗi: ${res?.message || 'Không thể tải'}`, 'error');
  }
}

// Task Stopping
async function stopEngineBatch(engine) {
  const res = await callPy('stop_engine_task', engine);
  if (res && res.success) {
    showToast(`Đã gửi yêu cầu dừng tác vụ ${engine.toUpperCase()}.`, 'warning');
  } else {
    showToast(`Lỗi dừng tác vụ: ${res?.message || ''}`, 'error');
  }
}

// Job Restart
async function restartSelectedJob(engine) {
  const state = appState[engine];
  if (!state.selectedJobId || !state.runId) {
    showToast('Chưa chọn job để restart.', 'warning');
    return;
  }
  await restartJobDirect(engine, state.selectedJobId);
}

async function restartJobDirect(engine, jobId) {
  const state = appState[engine];
  const runId = state.runId;
  if (!runId) {
    showToast('Không tìm thấy run_id của phiên hiện tại.', 'error');
    return;
  }

  state.isRunning = true;
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  document.getElementById(`${prefix}-run-btn`).style.display = 'none';
  document.getElementById(`${prefix}-stop-btn`).style.display = 'inline-flex';

  const tabBadge = document.getElementById(`tabBadge-${engine}`);
  if (tabBadge) {
    tabBadge.className = 'tab-status-pill running';
    tabBadge.innerHTML = '<span class="pulse-dot"></span> Đang restart';
  }

  appendLog(engine, `Khởi động lại ${jobId}...`, 'info');
  const res = await callPy('restart_job', engine, runId, jobId);
  if (!res || !res.success) {
    showToast(`Lỗi restart: ${res?.message || 'Không thể restart'}`, 'error');
  }
}

// ==================== GLOBAL CALLBACKS FROM PYTHON BRIDGE ====================

window.onEngineLog = function(engine, message, level = 'info') {
  appendLog(engine, message, level);
};

window.onJobStepEvent = async function(engine, event) {
  const state = appState[engine];
  if (!state) return;

  const eventRunId = event.run_id;
  const baseRunId = (eventRunId && eventRunId.includes('_job_'))
    ? eventRunId.split('_job_')[0]
    : eventRunId;

  if (!state.runId && baseRunId) {
    state.runId = baseRunId;
  }
  const effectiveRunId = state.runId || baseRunId;

  const stepName = event.step;
  const status = event.status;
  const msg = event.message || '';

  // Synchronize planned jobs list from backend checkpoint store if needed
  if (state.jobs.length === 0 && effectiveRunId) {
    const backendJobs = await callPy('get_run_jobs', engine, effectiveRunId);
    if (backendJobs && backendJobs.length > 0) {
      state.jobs = backendJobs.map(bj => ({
        jobId: bj.job_id,
        runId: bj.run_id,
        attemptNo: bj.current_attempt || 1,
        status: bj.status || 'running',
        step: bj.latest_step,
        currentStepName: bj.latest_step,
        outputsTotal: bj.outputs_total,
        outputsDone: bj.outputs_succeeded,
        outputsFailed: bj.outputs_failed,
        outputs: bj.outputs || [],
        output_dir: bj.output_dir,
      }));
      if (!state.selectedJobId && state.jobs.length > 0) {
        state.selectedJobId = state.jobs[0].jobId;
      }
    }
  }

  // Find job
  let job = null;
  if (event.job_id) {
    job = state.jobs.find(j => j.jobId === event.job_id);
  }
  if (!job) {
    job = state.jobs.find(j => j.status === 'running') ||
          state.jobs.find(j => j.jobId === state.selectedJobId) ||
          state.jobs[0];
  }

  if (!job) {
    job = {
      jobId: event.job_id || `job_${String(state.jobs.length + 1).padStart(3, '0')}`,
      runId: effectiveRunId,
      attemptNo: 1,
      status: status,
      step: stepName,
      currentStepName: stepName,
      outputsTotal: event.total || 0,
      outputsDone: event.current || 0,
      outputsFailed: 0,
      outputs: [],
      output_dir: state.outputDir,
    };
    state.jobs.push(job);
    if (!state.selectedJobId) state.selectedJobId = job.jobId;
  } else {
    job.step = stepName;
    job.currentStepName = `${event.step_index || 1}/${event.step_total || 7} ${stepName}`;

    if (status === 'running') {
      job.status = 'running';
    } else if (status === 'success' && (stepName === 'export' || stepName === 'manifest')) {
      job.status = 'success';
      job.currentStepName = 'Hoàn tất';
      job.outputsDone = job.outputsTotal || job.outputsDone;
      if (job.outputs && job.outputs.length > 0) {
        job.outputs.forEach(o => {
          if (o.status !== 'failed') o.status = 'success';
        });
      }
    } else if (status === 'failed') {
      job.status = 'failed';
    } else if (status === 'cancelled') {
      job.status = 'cancelled';
    } else if (status === 'partial') {
      job.status = 'partial';
    }

    if (event.total) job.outputsTotal = event.total;
    if (event.current !== undefined && event.current !== null) {
      job.outputsDone = event.current;
      if (job.outputs && job.outputs.length > 0 && stepName === 'download') {
        const cur = Number(event.current);
        for (let i = 0; i < job.outputs.length; i++) {
          if (i < cur) job.outputs[i].status = 'success';
          else if (i === cur) job.outputs[i].status = 'running';
        }
      }
    }
  }

  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';
  const summaryText = document.getElementById(`${prefix}-jobs-summary-text`);
  if (summaryText) {
    const doneJobs = state.jobs.filter(j => j.status === 'success' || j.status === 'completed').length;
    summaryText.innerText = `${doneJobs}/${state.jobs.length} jobs`;
  }

  renderJobsTable(engine);
};

window.onTaskFinished = async function(engine, status, summary) {
  const state = appState[engine];
  if (!state) return;

  state.isRunning = false;
  const prefix = engine === 'autoenhance' ? 'ae' : 'fo';

  const tabBadge = document.getElementById(`tabBadge-${engine}`);
  if (tabBadge) {
    tabBadge.className = 'tab-status-pill idle';
    tabBadge.innerText = 'Sẵn sàng';
  }
  const batchBadge = document.getElementById(`${prefix}-batch-badge`);
  if (batchBadge) {
    batchBadge.className = `badge badge-${status === 'success' ? 'success' : (status === 'failed' ? 'failed' : 'warning')}`;
    batchBadge.innerText = status === 'success' ? 'Hoàn tất' : (status === 'failed' ? 'Có lỗi' : 'Đã kết thúc');
  }

  document.getElementById(`${prefix}-run-btn`).style.display = 'inline-flex';
  document.getElementById(`${prefix}-stop-btn`).style.display = 'none';
  document.getElementById(`${prefix}-footer-status`).innerText = `Phiên hoàn tất (${status.toUpperCase()})`;

  // Reload final job records from backend store
  const targetRunId = summary?.run_id || state.runId;
  if (targetRunId) {
    const backendJobs = await callPy('get_run_jobs', engine, targetRunId);
    if (backendJobs && backendJobs.length > 0) {
      state.jobs = backendJobs.map(bj => ({
        jobId: bj.job_id,
        runId: bj.run_id,
        attemptNo: bj.current_attempt || 1,
        status: bj.status,
        step: bj.latest_step,
        currentStepName: bj.status === 'success' ? 'Hoàn tất' : bj.latest_step,
        outputsTotal: bj.outputs_total,
        outputsDone: bj.outputs_succeeded,
        outputsFailed: bj.outputs_failed,
        outputs: bj.outputs || [],
        output_dir: bj.output_dir,
      }));
    }
  }

  // Safety fallback: ensure completed status on all jobs if task finished successfully
  if (status === 'success') {
    state.jobs.forEach(j => {
      if (j.status !== 'failed' && j.status !== 'cancelled') {
        j.status = 'success';
        j.step = 'export';
        j.currentStepName = 'Hoàn tất';
        j.outputsDone = j.outputsTotal || j.outputsDone;
        if (j.outputs && j.outputs.length > 0) {
          j.outputs.forEach(o => {
            if (o.status !== 'failed') o.status = 'success';
          });
        }
      }
    });
  }

  const summaryText = document.getElementById(`${prefix}-jobs-summary-text`);
  if (summaryText) {
    const doneJobs = state.jobs.filter(j => j.status === 'success' || j.status === 'completed').length;
    summaryText.innerText = `${doneJobs}/${state.jobs.length} jobs`;
  }

  renderJobsTable(engine);
  showToast(`[${engine.toUpperCase()}] Tác vụ hoàn tất với trạng thái: ${status.toUpperCase()}`, status === 'success' ? 'success' : 'warning');
};

// ==================== MODALS LOGIC ====================

let currentModalEngine = 'autoenhance';

function openLicenseModal(engine) {
  currentModalEngine = engine;
  document.getElementById('lic-modal-engine-name').innerText = engine.toUpperCase();
  document.getElementById('lic-input-key').value = '';

  const machineIdElem = document.getElementById('lic-machine-id');
  if (machineIdElem) {
    const info = appState[engine]?.licenseInfo;
    machineIdElem.innerText = info?.machine_id || 'Đang tải...';
  }

  document.getElementById('modal-license').classList.add('open');
}

function setPresetKey(key) {
  document.getElementById('lic-input-key').value = key;
}

async function submitActivateLicense() {
  const key = document.getElementById('lic-input-key')?.value.trim();
  if (!key) {
    showToast('Vui lòng nhập License Key.', 'warning');
    return;
  }
  const submitBtn = document.getElementById('lic-submit-btn');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerText = 'Đang xác thực trực tuyến...';
  }

  const res = await callPy('activate_license', currentModalEngine, key);
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.innerText = 'Xác thực & Kích hoạt';
  }

  if (res && res.valid) {
    showToast(`Kích hoạt thành công gói ${res.level.toUpperCase()} cho ${currentModalEngine.toUpperCase()}!`, 'success');
    await refreshLicenseStatus(currentModalEngine);
    closeModal('modal-license');
  } else {
    showToast(`Xác thực thất bại: ${res?.message || 'Key không hợp lệ hoặc đã hết hạn.'}`, 'error');
  }
}

async function submitClearLicense() {
  await callPy('clear_license', currentModalEngine);
  showToast(`Đã hủy kích hoạt bản quyền cho ${currentModalEngine.toUpperCase()}.`, 'info');
  await refreshLicenseStatus(currentModalEngine);
  closeModal('modal-license');
}

function openServiceModal(engine) {
  currentModalEngine = engine;
  document.getElementById('service-modal-title').innerText = `Đăng nhập dịch vụ ${engine.toUpperCase()}`;
  
  const statusText = document.getElementById('service-modal-status-text');
  const isConn = appState[engine]?.serviceConnected;
  if (statusText) {
    statusText.innerText = isConn ? `Đã kết nối (${appState[engine].serviceMsg})` : 'Chưa kết nối';
  }

  document.getElementById('modal-service').classList.add('open');
}

async function submitConnectService() {
  const btn = document.getElementById('service-connect-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerText = 'Đang mở Chrome...';
  }
  showToast(`Đang mở Chrome để đăng nhập ${currentModalEngine.toUpperCase()}...`, 'info');
  const res = await callPy('login_service_cdp', currentModalEngine);
  if (btn) {
    btn.disabled = false;
    btn.innerText = 'Mở Chrome đăng nhập';
  }

  if (res && res.success) {
    showToast(`Đăng nhập dịch vụ ${currentModalEngine.toUpperCase()} thành công!`, 'success');
    await refreshAuthStatus(currentModalEngine);
    closeModal('modal-service');
  } else {
    showToast(`Không thể trích xuất phiên đăng nhập: ${res?.message || 'Thất bại'}`, 'error');
  }
}

function openSettingsModal() {
  document.getElementById('setting-ae-dir').value = appState.autoenhance.outputDir;
  document.getElementById('setting-fo-dir').value = appState.fotello.outputDir;
  document.getElementById('modal-settings').classList.add('open');
}

async function chooseSettingDir(engine) {
  const selected = await callPy('select_folder');
  if (selected) {
    if (engine === 'autoenhance') {
      document.getElementById('setting-ae-dir').value = selected;
    } else {
      document.getElementById('setting-fo-dir').value = selected;
    }
  }
}

async function saveSettings() {
  const aeDir = document.getElementById('setting-ae-dir')?.value;
  const foDir = document.getElementById('setting-fo-dir')?.value;

  if (aeDir) {
    appState.autoenhance.outputDir = aeDir;
    document.getElementById('ae-global-output-dir').value = aeDir;
  }
  if (foDir) {
    appState.fotello.outputDir = foDir;
    document.getElementById('fo-global-output-dir').value = foDir;
  }

  await callPy('save_app_config', {
    autoenhance_output_dir: aeDir,
    fotello_output_dir: foDir,
  });

  closeModal('modal-settings');
  showToast('Đã lưu cài đặt hệ thống thành công.', 'success');
}

async function openManifestHistoryModal(engine) {
  const state = appState[engine];
  const job = state.jobs.find(j => j.jobId === state.selectedJobId);
  if (!job) {
    showToast('Vui lòng chọn một Job để xem Manifest.', 'warning');
    return;
  }

  document.getElementById('mnf-job-id').innerText = job.jobId;
  document.getElementById('mnf-engine-name').innerText = `Engine: ${engine.toUpperCase()}`;
  document.getElementById('mnf-status-badge').className = `badge badge-${job.status === 'success' ? 'success' : (job.status === 'failed' ? 'failed' : 'warning')}`;
  document.getElementById('mnf-status-badge').innerText = (job.status || 'UNKNOWN').toUpperCase();
  document.getElementById('mnf-step-name').innerText = job.step || 'export';
  document.getElementById('mnf-stat-success').innerText = `${job.outputsDone || 0}/${job.outputsTotal || 0}`;
  document.getElementById('mnf-stat-failed').innerText = job.outputsFailed || 0;

  // Load history from backend
  const history = await callPy('get_job_history', engine, job.runId, job.jobId) || [];
  const selector = document.getElementById('mnf-attempt-selector');
  selector.innerHTML = '';

  history.forEach(h => {
    const btn = document.createElement('button');
    btn.className = 'btn btn-secondary btn-sm';
    btn.innerText = `Lần ${h.attempt_no} (${h.status})`;
    btn.onclick = async () => {
      const mnf = await callPy('get_job_manifest', engine, job.runId, job.jobId, h.attempt_no);
      renderManifestJson(mnf || h);
    };
    selector.appendChild(btn);
  });

  // Current Attempt
  const currBtn = document.createElement('button');
  currBtn.className = 'btn btn-primary btn-sm';
  currBtn.innerText = `Lần ${job.attemptNo || 1} (Hiện tại)`;
  currBtn.onclick = async () => {
    const mnf = await callPy('get_job_manifest', engine, job.runId, job.jobId, job.attemptNo);
    renderManifestJson(mnf || job);
  };
  selector.appendChild(currBtn);

  // Load manifest JSON
  const manifest = await callPy('get_job_manifest', engine, job.runId, job.jobId, job.attemptNo);
  renderManifestJson(manifest || {
    run_id: job.runId,
    job_id: job.jobId,
    engine: engine,
    status: job.status,
    step: job.step,
    outputs_total: job.outputsTotal,
    outputs_succeeded: job.outputsDone,
  });

  document.getElementById('modal-manifest').classList.add('open');
}

function renderManifestJson(obj) {
  const viewer = document.getElementById('mnf-json-viewer');
  if (viewer) {
    viewer.innerText = JSON.stringify(obj, null, 2);
  }
}

function copyManifestJson() {
  const code = document.getElementById('mnf-json-viewer')?.innerText || '';
  navigator.clipboard.writeText(code).then(() => showToast('Đã sao chép Manifest JSON!', 'info'));
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove('open');
}

// Toast Notifications
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast-item toast-${type}`;
  toast.innerHTML = `<span>${message}</span><button onclick="this.parentElement.remove()">&times;</button>`;
  container.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 4000);
}

function escapeHtml(text) {
  if (!text) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return String(text).replace(/[&<>"']/g, m => map[m]);
}
