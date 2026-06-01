let fotelloListingsData = [];
let fotelloJobs = {};
let selectedJobId = null;
let appLogLines = [];
const folderStorageKeys = {
    'fotello-inputdir': 'fotello.inputdir',
    'fotello-upload-savedir': 'fotello.uploadSavedir',
    'fotello-savedir': 'fotello.savedir'
};

function qs(id) {
    return document.getElementById(id);
}

function setStatus(state, text) {
    const dot = qs('status-dot');
    if (dot) dot.className = 'status-dot ' + state;
    const status = qs('status-text');
    if (status) status.textContent = text;
}

function clearButtonFocus() {
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
        document.activeElement.blur();
    }
}

function addLog(msg, type = '') {
    const ts = new Date().toLocaleTimeString('vi-VN', { hour12: false });
    const prefix = type ? `[${type.toUpperCase()}] ` : '';
    appLogLines.push(`[${ts}] ${prefix}${msg}`);
    if (!selectedJobId) renderLogBox();
}

function clearLog() {
    if (selectedJobId && fotelloJobs[selectedJobId]) {
        fotelloJobs[selectedJobId].logs = [];
    } else {
        appLogLines = [];
    }
    renderLogBox();
}

function updateProgress(current, total, pct) {
    const section = qs('progress-section');
    if (total > 0) {
        section.classList.remove('hidden');
        qs('progress-text').textContent = `${current} / ${total}`;
        qs('progress-pct').textContent = `${pct}%`;
        qs('progress-fill').style.width = pct + '%';
    }
    if (current >= total && total > 0) {
        setTimeout(() => section.classList.add('hidden'), 2500);
    }
}

function resetProgress() {
    const section = qs('progress-section');
    section.classList.add('hidden');
    qs('progress-text').textContent = '0 / 0';
    qs('progress-pct').textContent = '0%';
    qs('progress-fill').style.width = '0%';
}

function saveFolder(id, folder) {
    const key = folderStorageKeys[id];
    if (!key) return;
    try {
        if (folder) {
            localStorage.setItem(key, folder);
        } else {
            localStorage.removeItem(key);
        }
    } catch (error) {
    }
}

function updateUploadOutputDisplay(folder) {
    const display = qs('fotello-upload-savedir-display');
    if (!display) return;
    display.textContent = folder || '';
    display.title = folder || '';
}

function restoreSavedFolders() {
    Object.entries(folderStorageKeys).forEach(([id, key]) => {
        const input = qs(id);
        if (!input) return;
        try {
            const folder = localStorage.getItem(key) || '';
            if (!folder) return;
            input.value = folder;
            if (id === 'fotello-upload-savedir') {
                updateUploadOutputDisplay(folder);
            }
        } catch (error) {
        }
    });
}

function bindFolderPersistence() {
    Object.keys(folderStorageKeys).forEach(id => {
        const input = qs(id);
        if (!input) return;
        input.addEventListener('change', () => {
            const folder = input.value.trim();
            saveFolder(id, folder);
            if (id === 'fotello-upload-savedir') {
                updateUploadOutputDisplay(folder);
            }
        });
    });
}

function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

function normalizeJob(job) {
    const existing = fotelloJobs[job.job_id] || {};
    fotelloJobs[job.job_id] = {
        logs: existing.logs || [],
        ...existing,
        ...job,
        logs: job.logs || existing.logs || []
    };
    return fotelloJobs[job.job_id];
}

function jobStatusText(status) {
    return {
        pending: 'Chờ chạy',
        running: 'Đang chạy',
        success: 'Thành công',
        failed: 'Thất bại',
        stopped: 'Đã dừng'
    }[status] || status || 'Không rõ';
}

function renderJobs() {
    const list = qs('job-list');
    if (!list) return;
    const jobs = Object.values(fotelloJobs);
    qs('job-count').textContent = String(jobs.length);
    if (!jobs.length) {
        list.innerHTML = '<div class="empty">Chưa có job nào</div>';
        return;
    }
    list.innerHTML = jobs.map(job => {
        const active = job.job_id === selectedJobId ? ' active' : '';
        const done = Number(job.done_count || 0);
        const uploaded = Number(job.uploaded_count || 0);
        const downloaded = Number(job.downloaded_count || 0);
        const leftCount = job.type === 'upload' ? uploaded : done;
        const countText = `${leftCount}/${downloaded}`;
        const folderButton = job.status === 'success' && job.output_path
            ? `<button class="job-folder-btn" onclick="fotelloOpenJobFolder(event, '${job.job_id}')">📂</button>`
            : '';
        return `
            <div class="job-item${active}" onclick="selectJob('${job.job_id}')">
                <div class="job-main">
                    <strong class="job-item-id">#${escapeHtml(job.job_id)}</strong>
                    <span class="job-item-name" title="${escapeHtml(job.name)}">${escapeHtml(job.name)}</span>
                    <span class="job-item-count">${escapeHtml(countText)}</span>
                </div>
                <div class="job-side">
                    <span>${folderButton}</span>
                    <span class="job-item-status ${escapeHtml(job.status)}">${escapeHtml(jobStatusText(job.status))}</span>
                </div>
            </div>
        `;
    }).join('');
}

function renderLogBox() {
    const box = qs('log-box');
    if (!box) return;
    const title = qs('log-title');
    const job = selectedJobId ? fotelloJobs[selectedJobId] : null;
    const lines = job ? (job.logs || []) : appLogLines;
    if (title) {
        title.textContent = job ? `Log: Job ${job.job_id}` : 'Log';
    }
    box.value = lines.join('\n');
    if (box.value) box.value += '\n';
    box.scrollTop = box.scrollHeight;
}

async function selectJob(jobId) {
    selectedJobId = jobId;
    renderJobs();
    renderLogBox();
    const logs = await callApi('fotello_job_logs', jobId);
    if (Array.isArray(logs) && fotelloJobs[jobId]) {
        fotelloJobs[jobId].logs = logs;
        renderLogBox();
    }
}

function upsertJob(job, select = false) {
    if (!job || !job.job_id) return;
    normalizeJob(job);
    if (select || !selectedJobId) {
        selectedJobId = job.job_id;
    }
    renderJobs();
    renderLogBox();
    updateGlobalJobStatus();
}

function updateGlobalJobStatus() {
    const running = Object.values(fotelloJobs).filter(job => job.status === 'running').length;
    if (running > 0) {
        setStatus('running', `${running} job đang chạy`);
    } else {
        setStatus('idle', 'Sẵn sàng');
    }
}

function jobLog(jobId, line) {
    const job = fotelloJobs[jobId] || normalizeJob({ job_id: jobId, name: `Job ${jobId}`, status: 'running' });
    job.logs = job.logs || [];
    job.logs.push(line);
    if (job.logs.length > 500) job.logs.shift();
    if (jobId === selectedJobId) renderLogBox();
}

function jobProgress(job) {
    upsertJob(job);
}

function jobStatus(job) {
    upsertJob(job);
}

async function fotelloOpenJobFolder(event, jobId) {
    event.stopPropagation();
    const result = await callApi('fotello_open_job_folder', jobId);
    if (result && !result.ok) {
        addLog(result.msg || 'Không mở được thư mục job', 'error');
    }
}

function toCamelCase(name) {
    return name.replace(/_([a-z])/g, (_match, letter) => letter.toUpperCase());
}

function findApiMethod(api, name) {
    if (!api) return null;
    if (typeof api[name] === 'function') return api[name].bind(api);
    const camelName = toCamelCase(name);
    if (typeof api[camelName] === 'function') return api[camelName].bind(api);
    return null;
}

function listApiMethods(api) {
    if (!api) return '';
    return Object.keys(api).filter(key => typeof api[key] === 'function').join(', ');
}

function hasPyWebViewMethod(name) {
    return Boolean(findApiMethod(window.pywebview?.api, name));
}

async function waitForPyWebView(maxWait = 10000, readyMethod = 'get_settings') {
    if (hasPyWebViewMethod(readyMethod)) return true;
    return new Promise(resolve => {
        let resolved = false;
        const finish = value => {
            if (resolved) return;
            resolved = true;
            resolve(value);
        };
        window.addEventListener('pywebviewready', () => {
            if (hasPyWebViewMethod(readyMethod)) finish(true);
        });
        const start = Date.now();
        const poll = setInterval(() => {
            if (hasPyWebViewMethod(readyMethod)) {
                clearInterval(poll);
                finish(true);
            } else if (Date.now() - start > maxWait) {
                clearInterval(poll);
                finish(false);
            }
        }, 100);
    });
}

function requireApi() {
    if (!window.pywebview || !window.pywebview.api) {
        addLog('PyWebView chưa sẵn sàng', 'warn');
        return null;
    }
    return window.pywebview.api;
}

function getApiMethod(api, name) {
    const method = findApiMethod(api, name);
    if (method) return method;
    const available = listApiMethods(api);
    addLog(`PyWebView API thiếu hàm ${name}. Có: ${available || 'không rõ'}`, 'error');
    return null;
}

async function callApi(name, ...args) {
    const api = requireApi();
    if (!api) return null;
    const method = getApiMethod(api, name);
    if (!method) return null;
    return method(...args);
}

function showPanel(id) {
    document.querySelectorAll('.panel').forEach(panel => panel.classList.remove('active'));
    document.querySelectorAll('.nav').forEach(nav => nav.classList.remove('active'));
    qs(id).classList.add('active');
    document.querySelector(`.nav[data-panel="${id}"]`).classList.add('active');
}

function fotelloOpenChrome() {
    const api = requireApi();
    if (api) callApi('fotello_open_chrome');
}

async function fotelloConnect() {
    const api = requireApi();
    if (!api) return;
    addLog('Đang kết nối Fotello. Hãy login app.fotello.co trong Chrome nếu chưa login.', 'info');
    setStatus('running', 'Đang kết nối...');
    const result = await callApi('fotello_connect');
    if (!result) return;
    if (result.ok) {
        qs('fotello-status').textContent = 'Đã kết nối';
        addLog('Đã kết nối Fotello', 'success');
    } else {
        qs('fotello-status').textContent = 'Chưa kết nối';
        addLog(result.msg || 'Không kết nối được Fotello', 'error');
    }
    setStatus('idle', result.ok ? 'Đã kết nối' : 'Lỗi kết nối');
}

async function fotelloReconnect() {
    const api = requireApi();
    if (!api) return;
    const result = await callApi('fotello_reconnect');
    if (!result) return;
    qs('fotello-status').textContent = result.ok ? 'Đã kết nối' : 'Chưa kết nối';
}

async function fotelloLoadListings() {
    const api = requireApi();
    if (!api) return;
    addLog('Đang tải danh sách listings...', 'info');
    const result = await callApi('fotello_list_listings');
    if (!result) return;
    if (!result.ok) {
        addLog(result.msg || 'Lỗi tải listings', 'error');
        return;
    }
    fotelloListingsData = result.listings || [];
    fotelloRenderListings();
}

function fotelloRenderListings() {
    const container = qs('fotello-listings');
    if (!fotelloListingsData.length) {
        container.innerHTML = '<div class="empty">Không có listings nào</div>';
        return;
    }
    container.innerHTML = fotelloListingsData.map(listing => `
        <label class="listing-item">
            <input type="checkbox" class="fotello-listing-cb" data-id="${listing.id}">
            <span class="listing-name">${listing.name}</span>
            <span>${listing.brackets || 0} brackets</span>
            <span>${listing.created_at || ''}</span>
        </label>
    `).join('');
}

function fotelloToggleAll() {
    const checked = qs('fotello-select-all').checked;
    document.querySelectorAll('.fotello-listing-cb').forEach(cb => cb.checked = checked);
}

async function browseToInput(id) {
    const api = requireApi();
    if (!api) return;
    const folder = await callApi('browse_folder');
    if (folder) {
        qs(id).value = folder;
        saveFolder(id, folder);
    }
}

function fotelloBrowseFolder() {
    browseToInput('fotello-savedir');
}

function fotelloBrowseInputFolder() {
    browseToInput('fotello-inputdir');
}

async function fotelloBrowseUploadOutput() {
    const api = requireApi();
    if (!api) return;
    const folder = await callApi('browse_folder');
    if (!folder) return;
    const input = qs('fotello-upload-savedir');
    if (input) input.value = folder;
    saveFolder('fotello-upload-savedir', folder);
    updateUploadOutputDisplay(folder);
}

function selectedListings() {
    return Array.from(document.querySelectorAll('.fotello-listing-cb:checked')).map(cb => cb.dataset.id);
}

async function fotelloStartDownload() {
    clearButtonFocus();
    const api = requireApi();
    if (!api) return;
    const selected = selectedListings();
    const savedir = qs('fotello-savedir').value.trim();
    if (!selected.length) {
        addLog('Chưa chọn listing nào', 'warn');
        return;
    }
    if (!savedir) {
        addLog('Chưa chọn thư mục lưu', 'warn');
        return;
    }
    addLog(`Bắt đầu tải ${selected.length} listings...`, 'info');
    const result = await callApi('fotello_download', selected, savedir);
    if (!result) return;
    if (!result.ok) {
        addLog(result.msg || 'Không bắt đầu được job download', 'error');
        return;
    }
    upsertJob(result.job, true);
}

async function fotelloStartUpload() {
    clearButtonFocus();
    const api = requireApi();
    if (!api) return;
    const inputdir = qs('fotello-inputdir').value.trim();
    const savedir = qs('fotello-upload-savedir').value.trim();
    if (!inputdir) {
        addLog('Chưa chọn thư mục ảnh gốc', 'warn');
        return;
    }
    if (!savedir) {
        addLog('Chưa chọn thư mục lưu kết quả', 'warn');
        return;
    }
    const preferences = {
        bracket_size: parseInt(qs('fotello-pref-bracket').value, 10),
        contrast_style: qs('fotello-pref-contrast').value,
        exterior_sky_replacement: qs('fotello-pref-sky').value,
        perspective_correction: qs('fotello-pref-perspective').value,
        cloud_style: qs('fotello-pref-cloud').value,
        listing_name_prefix: qs('fotello-listing-name').value.trim(),
        custom_style_id: null
    };
    addLog(`Bắt đầu jobs với thư mục ${inputdir}...`, 'info');
    const result = await callApi('fotello_upload', inputdir, savedir, preferences);
    if (!result) return;
    if (!result.ok) {
        addLog(result.msg || 'Không bắt đầu được job upload', 'error');
        return;
    }
    upsertJob(result.job, true);
}

function fotelloStop() {
    clearButtonFocus();
    const api = requireApi();
    if (!api) return;
    if (!selectedJobId) {
        addLog('Chưa chọn job để dừng', 'warn');
        return;
    }
    callApi('fotello_stop', selectedJobId);
}

async function loadSettings() {
    const api = requireApi();
    if (!api) return;
    const settings = await callApi('get_settings');
    if (!settings) return;
    qs('set-port').value = settings.port || 9222;
    qs('set-fotello-url').value = settings.fotello_url || 'https://app.fotello.co';
    qs('set-timeout').value = settings.timeout || 30;
    qs('set-delay').value = settings.delay || 0.3;
    qs('set-browser-strategy').value = settings.browser_strategy || 'system_then_download';
    qs('set-browser-channel').value = settings.browser_channel || 'Stable';
    qs('set-browser-cache-dir').value = settings.browser_cache_dir || '';
    qs('set-browser-override').value = settings.browser_path_override || '';
    qs('set-poll-initial-interval').value = settings.poll_initial_interval || 60;
    qs('set-poll-initial-attempts').value = settings.poll_initial_attempts || 10;
    qs('set-poll-later-interval').value = settings.poll_later_interval || 30;
    qs('set-poll-ready-divisor').value = settings.poll_ready_divisor || 2;
    qs('set-poll-timeout').value = settings.poll_timeout || 1800;
}

async function saveSettings() {
    const api = requireApi();
    if (!api) return;
    await callApi('save_settings', {
        port: parseInt(qs('set-port').value, 10),
        fotello_url: qs('set-fotello-url').value.trim(),
        timeout: parseInt(qs('set-timeout').value, 10),
        delay: parseFloat(qs('set-delay').value),
        browser_strategy: qs('set-browser-strategy').value,
        browser_channel: qs('set-browser-channel').value,
        browser_cache_dir: qs('set-browser-cache-dir').value.trim(),
        browser_path_override: qs('set-browser-override').value.trim(),
        poll_initial_interval: parseInt(qs('set-poll-initial-interval').value, 10),
        poll_initial_attempts: parseInt(qs('set-poll-initial-attempts').value, 10),
        poll_later_interval: parseInt(qs('set-poll-later-interval').value, 10),
        poll_ready_divisor: parseFloat(qs('set-poll-ready-divisor').value),
        poll_timeout: parseInt(qs('set-poll-timeout').value, 10)
    });
    addLog('Đã lưu settings. Khởi động lại app nếu đổi debug port.', 'success');
    await refreshBrowserInfo();
}

async function refreshBrowserInfo() {
    const api = requireApi();
    if (!api) return;
    const info = await callApi('get_browser_info');
    if (!info) return;
    const version = info.version ? ` v${info.version}` : '';
    qs('chrome-path-display').textContent = 'Browser: ' + (info.path || 'chưa resolve');
    qs('browser-path-readonly').value = info.path || '';
    qs('browser-source-readonly').value = (info.source || '') + version;
    qs('browser-channel-readonly').value = info.channel || '';
}

async function repairBrowserRuntime() {
    const api = requireApi();
    if (!api) return;
    addLog('Đang repair Chrome runtime...', 'warn');
    setStatus('running', 'Đang repair runtime...');
    resetProgress();
    const result = await callApi('repair_browser_runtime');
    if (!result) return;
    if (result.ok) {
        addLog('Repair runtime xong', 'success');
        await refreshBrowserInfo();
        setStatus('idle', 'Runtime sẵn sàng');
        return;
    }
    addLog(result.msg || 'Repair runtime thất bại', 'error');
    setStatus('idle', 'Repair thất bại');
}

async function loadJobs() {
    const jobs = await callApi('fotello_jobs');
    if (!Array.isArray(jobs)) return;
    jobs.forEach(job => normalizeJob(job));
    renderJobs();
    renderLogBox();
    updateGlobalJobStatus();
}

document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('.nav').forEach(nav => {
        nav.addEventListener('click', () => showPanel(nav.dataset.panel));
    });
    restoreSavedFolders();
    bindFolderPersistence();
    const ready = await waitForPyWebView();
    if (!ready) {
        addLog('PyWebView API chưa sẵn sàng. Hãy chạy bằng python main.py và kiểm tra js_api.', 'warn');
        return;
    }
    await loadSettings();
    await refreshBrowserInfo();
    await loadJobs();
    const status = await callApi('fotello_status');
    if (!status) return;
    qs('fotello-status').textContent = status.connected ? 'Đã kết nối' : 'Chưa kết nối';
    if (!status.connected && status.has_saved_token) {
        await fotelloReconnect();
    }
});

window.addLog = addLog;
window.setStatus = setStatus;
window.updateProgress = updateProgress;
window.resetProgress = resetProgress;
window.jobLog = jobLog;
window.jobProgress = jobProgress;
window.jobStatus = jobStatus;
window.selectJob = selectJob;
window.fotelloOpenJobFolder = fotelloOpenJobFolder;
