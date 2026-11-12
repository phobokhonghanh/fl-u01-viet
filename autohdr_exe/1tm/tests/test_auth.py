"""
Test Suite cho Universal Auth Package (1tm/libs/auth).
Kiểm thử:
1. Import sạch (không try-except, không lỗi).
2. Format display_name của Autoenhance (chỉ hiện masked key, không có tiền tố 'Key Chính').
3. Quản lý danh sách tài khoản (list, switch, delete) cho AutoHDR.
4. Quản lý danh sách API Key (list, switch, delete) cho Autoenhance.
5. Tự động chuyển active sang tài khoản kế tiếp khi tài khoản active bị xóa.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Đảm bảo đường dẫn import
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR.parent))

from libs.auth import (
    AutoHDRSessionRecord,
    AutoenhanceSessionRecord,
    get_auth_provider,
    list_accounts,
    switch_account,
    delete_account,
    get_active_session,
)


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hdr_provider = get_auth_provider("autohdr")
        self.ae_provider = get_auth_provider("autoenhance")

        # Trỏ sessions_file vào temp dir để cô lập hoàn toàn môi trường test
        self.hdr_provider.sessions_file = os.path.join(self.temp_dir.name, "autohdr.json")
        self.ae_provider.sessions_file = os.path.join(self.temp_dir.name, "autoenhance.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_1_autoenhance_display_name_masked_only(self):
        """Kiểm thử display_name của Autoenhance chỉ hiển thị masked key."""
        rec = AutoenhanceSessionRecord(api_key="ae_live_1234567890abcdef49a1")
        # Phải là dạng ae_live_...49a1 (không chứa 'Default Key' hay 'Key Chính')
        self.assertEqual(rec.display_name, "ae_live_...49a1")
        self.assertNotIn("Key Chính", rec.display_name)
        self.assertNotIn("Default Key", rec.display_name)

        short_rec = AutoenhanceSessionRecord(api_key="short_key")
        self.assertEqual(short_rec.display_name, "short_key")

    def test_2_autohdr_list_switch_delete(self):
        """Kiểm thử list, switch, delete với AutoHDR."""
        rec1 = AutoHDRSessionRecord(email="user1@gmail.com", cookie="c1", is_active=True)
        rec2 = AutoHDRSessionRecord(email="user2@gmail.com", cookie="c2", is_active=False)
        self.hdr_provider._write_records([rec1, rec2])

        accounts = list_accounts("autohdr")
        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0].email, "user1@gmail.com")

        # Chuyển active sang user2
        switched = switch_account("user2@gmail.com", "autohdr")
        self.assertIsNotNone(switched)
        self.assertTrue(switched.is_active)

        active = get_active_session("autohdr")
        self.assertEqual(active.email, "user2@gmail.com")

        # Xóa tài khoản user1
        deleted = delete_account("user1@gmail.com", "autohdr")
        self.assertTrue(deleted)
        self.assertEqual(len(list_accounts("autohdr")), 1)
        self.assertEqual(list_accounts("autohdr")[0].email, "user2@gmail.com")

    def test_3_autohdr_delete_active_auto_switches(self):
        """Kiểm thử xóa tài khoản active của AutoHDR sẽ tự động chuyển active sang tài khoản còn lại."""
        rec1 = AutoHDRSessionRecord(email="user1@gmail.com", cookie="c1", is_active=True)
        rec2 = AutoHDRSessionRecord(email="user2@gmail.com", cookie="c2", is_active=False)
        self.hdr_provider._write_records([rec1, rec2])

        # Xóa user1 (đang active)
        deleted = delete_account("user1@gmail.com", "autohdr")
        self.assertTrue(deleted)

        remaining = list_accounts("autohdr")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].email, "user2@gmail.com")
        self.assertTrue(remaining[0].is_active)

    def test_4_autoenhance_list_switch_delete(self):
        """Kiểm thử list, switch, delete với Autoenhance."""
        rec1 = AutoenhanceSessionRecord(api_key="key_1111222233334444", is_active=True)
        rec2 = AutoenhanceSessionRecord(api_key="key_5555666677778888", is_active=False)
        self.ae_provider._write_records([rec1, rec2])

        keys = list_accounts("autoenhance")
        self.assertEqual(len(keys), 2)

        # Chuyển active sang key 2
        switched = switch_account("key_5555666677778888", "autoenhance")
        self.assertIsNotNone(switched)
        self.assertTrue(switched.is_active)

        # Xóa key 2 (đang active) -> key 1 phải tự động thành active
        deleted = delete_account("key_5555666677778888", "autoenhance")
        self.assertTrue(deleted)

        remaining = list_accounts("autoenhance")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].api_key, "key_1111222233334444")
        self.assertTrue(remaining[0].is_active)


if __name__ == "__main__":
    unittest.main()

