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
        },
        {
            "key": "FOT",
            "name": "fotello",
            "is_active": True,
            "expires_at": None,
            "machine_id": None,
            "product": "fotello",
        },
    ]
    assert saved[-1] == [record.to_dict() for record in records]

