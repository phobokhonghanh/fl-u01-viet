import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import key_manager
from models.schemas import KeyRecord


def _patch_storage(monkeypatch, records):
    saved = []

    def fake_load_keys(_s3_key):
        return records

    def fake_save_keys(_s3_key, updated_records):
        saved.append([record.to_dict() for record in updated_records])

    monkeypatch.setattr(key_manager, "load_keys", fake_load_keys)
    monkeypatch.setattr(key_manager, "save_keys", fake_save_keys)
    return saved


def test_old_key_without_product_defaults_to_autohdr(monkeypatch):
    records = [KeyRecord.from_dict({"key": "OLD", "name": "legacy"})]
    _patch_storage(monkeypatch, records)

    assert key_manager.check_key("keys.json", "OLD", "machine-1") is True
    assert records[0].machine_id == "machine-1"
    assert records[0].product == "autohdr"


def test_old_key_without_product_rejects_fotello_without_machine_bind(monkeypatch):
    records = [KeyRecord.from_dict({"key": "OLD", "name": "legacy"})]
    saved = _patch_storage(monkeypatch, records)

    assert key_manager.check_key("keys.json", "OLD", "machine-1", "fotello") is False
    assert records[0].machine_id is None
    assert saved == []


def test_fotello_key_accepts_fotello(monkeypatch):
    records = [KeyRecord.from_dict({"key": "FOT", "name": "fotello", "product": "fotello"})]
    _patch_storage(monkeypatch, records)

    assert key_manager.check_key("keys.json", "FOT", "machine-1", "fotello") is True


def test_import_defaults_missing_product_and_skips_invalid(monkeypatch):
    records = []
    saved = _patch_storage(monkeypatch, records)

    imported = key_manager.import_keys(
        "keys.json",
        [
            {"key": "OLD", "name": "legacy"},
            {"key": "FOT", "name": "fotello", "product": " fotello "},
            {"key": "BAD", "name": "bad", "product": "unknown"},
            {"name": "missing-key"},
        ],
    )

    assert imported == 2
    assert [record.to_dict() for record in records] == [
        {
            "key": "OLD",
            "name": "legacy",
            "is_active": True,
            "expires_at": None,
            "machine_id": None,
            "product": "autohdr",
            "level": "lite",
        },
        {
            "key": "FOT",
            "name": "fotello",
            "is_active": True,
            "expires_at": None,
            "machine_id": None,
            "product": "fotello",
            "level": "lite",
        },
    ]
    assert saved[-1] == [record.to_dict() for record in records]


def test_reset_key_machine(monkeypatch):
    records = [KeyRecord(key="TESTKEY", name="test_user", machine_id="old_machine", product="autohdr")]
    _patch_storage(monkeypatch, records)

    updated_record = key_manager.reset_key_machine("keys.json", "test_user")
    assert updated_record is not None
    assert updated_record.machine_id is None
    assert records[0].machine_id is None


def test_reset_all_keys_machine(monkeypatch):
    records = [
        KeyRecord(key="KEY1", name="user1", machine_id="m1", product="autohdr"),
        KeyRecord(key="KEY2", name="user2", machine_id="m2", product="fotello"),
        KeyRecord(key="KEY3", name="user3", machine_id=None, product="autohdr"),
    ]
    _patch_storage(monkeypatch, records)

    reset_count = key_manager.reset_all_keys_machine("keys.json")
    assert reset_count == 2
    assert records[0].machine_id is None
    assert records[1].machine_id is None
    assert records[2].machine_id is None



def test_check_level_access():
    from models.schemas import check_level_access, VALID_KEY_LEVELS
    # Kiểm tra mặc định và các cấp độ hợp lệ
    assert check_level_access("lite", "lite") is True
    assert check_level_access("plus", "lite") is True
    assert check_level_access("plus", "plus") is True
    assert check_level_access("lite", "plus") is False

    # Thử nghiệm với các cấp độ chưa hỗ trợ hoặc rỗng
    assert check_level_access("", "lite") is True
    assert check_level_access(None, "plus") is False

    # Kiểm tra kịch bản giả lập có thêm cấp độ pro trong tương lai
    # Lưu ý: Vì VALID_KEY_LEVELS được định nghĩa tĩnh, ta kiểm tra logic index
    VALID_KEY_LEVELS.append("pro")
    try:
        assert check_level_access("pro", "lite") is True
        assert check_level_access("pro", "plus") is True
        assert check_level_access("pro", "pro") is True
        assert check_level_access("plus", "pro") is False
    finally:
        VALID_KEY_LEVELS.remove("pro")


def test_key_levels_verify_and_get_key(monkeypatch):
    records = [
        KeyRecord.from_dict({"key": "LITE_KEY", "name": "user_lite", "product": "fotello", "level": "lite"}),
        KeyRecord.from_dict({"key": "PLUS_KEY", "name": "user_plus", "product": "fotello", "level": "plus"}),
    ]
    _patch_storage(monkeypatch, records)

    # Verify LITE key
    rec_lite = key_manager.verify_and_get_key("keys.json", "LITE_KEY", "mach-1", "fotello")
    assert rec_lite is not None
    assert rec_lite.level == "lite"

    # Verify PLUS key
    rec_plus = key_manager.verify_and_get_key("keys.json", "PLUS_KEY", "mach-2", "fotello")
    assert rec_plus is not None
    assert rec_plus.level == "plus"


def test_parse_version():
    from routers.keys import parse_version
    assert parse_version("1.0") == (1, 0)
    assert parse_version("2.1") == (2, 1)
    assert parse_version("0.9") == (0, 9)
    assert parse_version("") == (0, 0)
    assert parse_version(None) == (0, 0)
    assert parse_version("abc") == (0, 0)


def test_verify_key_version_checking(monkeypatch):
    from fastapi.testclient import TestClient
    from app import app
    from config.settings import Settings

    # Mock storage để tránh gọi S3 thật
    _patch_storage(monkeypatch, [])

    # Ép Settings.min_client_version = "1.0"
    monkeypatch.setattr(Settings, "min_client_version", "1.0")

    client = TestClient(app)

    # 1. Product fotello với client version cũ 0.9 -> Trả về 200 valid=False và thông báo nâng cấp
    resp = client.post("/api/key/active", json={
        "key": "SOMEKEY",
        "machine_id": "mach-1",
        "product": "fotello",
        "client_version": "0.9"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "Phiên bản Fotello của bạn đã cũ" in data["message"]

    # 2. Product autohdr với version 0.9 -> Cũng kiểm tra version và trả về 200 valid=False thông báo nâng cấp
    resp = client.post("/api/key/active", json={
        "key": "SOMEKEY",
        "machine_id": "mach-1",
        "product": "autohdr",
        "client_version": "0.9"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "Phiên bản AutoHDR của bạn đã cũ" in data["message"]





