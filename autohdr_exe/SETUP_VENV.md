# Hướng dẫn thiết lập môi trường ảo (venv) cho AutoHDR v2

Để đảm bảo project chạy ổn định và không xung đột với các thư viện khác trong máy, bạn nên sử dụng môi trường ảo (Virtual Environment).

## 1. Sửa lỗi "No module named 'tkinter'"
Trên Linux (Ubuntu/Debian), thư viện giao diện `tkinter` thường không đi kèm mặc định. Bạn cần cài đặt nó trực tiếp vào hệ thống bằng lệnh:

```bash
sudo apt-get update
sudo apt-get install python3-tk
```

## 2. Các bước thiết lập môi trường ảo

### Bước 1: Tạo môi trường ảo
Chạy lệnh này tại thư mục `autohdr_client_exe_v2`:
```bash
python3 -m venv ven
```

### Bước 2: Kích hoạt môi trường ảo
- **Linux/macOS:**
  ```bash
  source ven/bin/activate
  ```
- **Windows:**
  ```bash
  .\ven\Scripts\activate
  ```

Sau khi kích hoạt, bạn sẽ thấy `(ven)` xuất hiện ở đầu dòng lệnh terminal.

### Bước 3: Cài đặt các thư viện cần thiết
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Chạy ứng dụng
Sau khi đã kích hoạt môi trường và cài đặt đầy đủ:
```bash
python main.py
```

## 5. Đóng gói ứng dụng (Build EXE)

Để tạo file chạy độc lập (.exe trên Windows), bạn cần sử dụng thư viện `PyInstaller`.

### Bước 1: Cài đặt PyInstaller
Trong môi trường ảo (ven), chạy lệnh:
```bash
pip install pyinstaller
```

### Bước 2: Chạy lệnh Build
Dùng file spec đã cấu hình sẵn trong project:
```bash
pyinstaller main.spec
```

Sau khi chạy xong, file `.exe` sẽ nằm trong thư mục `dist/`.

---

## 6. Thoát khỏi môi trường ảo
Khi không làm việc nữa, bạn có thể thoát ra bằng lệnh:
```bash
deactivate
```

---
**Lưu ý:** 
- Luôn đảm bảo bạn đã chạy `sudo apt-get install python3-tk` trước khi chạy `main.py` trên Linux.
- Khi build trên Windows, hãy chắc chắn bạn đang dùng terminal với quyền Admin nếu gặp lỗi quyền truy cập.
