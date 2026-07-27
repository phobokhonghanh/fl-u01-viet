# FL-U01-VIET Repository

Hệ thống ứng dụng Client (**AutoHDR**, **Fotello**) và Backend API Service.

---

## 🛠️ Thử nghiệm GitHub Actions Workflows tại Local (dùng `act`)

[nektos/act](https://github.com/nektos/act) cho phép chạy và kiểm tra các GitHub Actions Workflows trực tiếp bên dưới máy local mà không cần đẩy code lên GitHub.

### 1. Yêu cầu tiên quyết
- **Docker**: `act` sử dụng Docker để tạo môi trường chạy giả lập. Hãy đảm bảo Docker Desktop hoặc Docker Daemon đang hoạt động (`docker --version`).

### 2. Cài đặt `act`

#### Trên Linux (Ubuntu / Debian):
*Tải trực tiếp bản chuẩn chính thức từ GitHub (Khuyên dùng):*
```bash
curl -sL https://github.com/nektos/act/releases/latest/download/act_Linux_x86_64.tar.gz | tar -xz -C /tmp
sudo mv /tmp/act /usr/local/bin/
hash -r
```

#### Trên macOS:
```bash
brew install act
```

#### Trên Windows (PowerShell / CMD):
```powershell
winget install nektos.act
# hoặc dùng Chocolatey
choco install act-cli
```

Kiểm tra phiên bản sau khi cài:
```bash
act --version
```

---

### 3. Lệnh chạy thử nghiệm Workflows ở Local

Vì các workflow trong dự án chạy Matrix trên nhiều hệ điều hành (`windows-latest`, `macos-latest`), khi chạy bằng `act` ở local Linux, bạn cần thêm tham số mapping `-P`:

#### Test Build AutoHDR:
```bash
act -j build_autohdr -P windows-latest=catthehacker/ubuntu:act-latest -P macos-latest=catthehacker/ubuntu:act-latest
```

#### Test Build Fotello:
```bash
act -j build_fotello -P windows-latest=catthehacker/ubuntu:act-latest -P macos-latest=catthehacker/ubuntu:act-latest
```

---

## 📌 Hướng dẫn Nâng cấp Version sản phẩm

Khi phát hành phiên bản mới (ví dụ từ `1.0` ➔ `2.0`):

1. **AutoHDR (`autohdr_exe/`)**:
   - Cập nhật số version trong [`autohdr_exe/version.md`](autohdr_exe/version.md) (ví dụ: `2.0`).
   - Cập nhật `CLIENT_VERSION = "2.0"` trong [`autohdr_exe/core/constants.py`](autohdr_exe/core/constants.py).

2. **Fotello (`fotello/`)**:
   - Cập nhật số version trong [`fotello/version.md`](fotello/version.md) (ví dụ: `2.0`).
   - Cập nhật `CLIENT_VERSION = "2.0"` trong [`fotello/backend/constants.py`](fotello/backend/constants.py).

3. **Backend (`backend/`) - Tùy chọn ép buộc bản mới:**
   - Cập nhật `min_client_version = "2.0"` trong [`backend/config/settings.py`](backend/config/settings.py) hoặc biến môi trường `MIN_CLIENT_VERSION=2.0` trên Server (Railway).

---

## 🚀 Quy trình CI/CD Đóng gói tự động (GitHub Actions)

Các workflow đóng gói ứng dụng được đặt tại thư mục `.github/workflows/`:
- **`build-exe.yml`**: Đóng gói ứng dụng AutoHDR song song cho **Windows** (`.exe`) và **macOS** (`.zip`).
- **`build-fotello-exe.yml`**: Obfuscate code backend qua PyArmor và đóng gói ứng dụng Fotello song song cho **Windows** (`.exe`) và **macOS** (`.zip`).

*Workflow sẽ tự động kích hoạt khi push code lên branch `deploy/exe` hoặc khi nhấn nút **Run workflow** thủ công trên tab Actions của GitHub.*
