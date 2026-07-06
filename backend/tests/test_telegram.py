import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from config.settings import Settings
from core import telegram, key_manager
from models.schemas import KeyRecord

client = TestClient(app)


@pytest.mark.anyio
async def test_send_telegram_notification_missing_config(monkeypatch):
    # Mock Settings to return empty config
    fake_settings = Settings(telegram_bot_token=None, telegram_chat_id=None)
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    # Call should return False immediately
    result = await telegram.send_telegram_notification("test message")
    assert result is False


@pytest.mark.anyio
async def test_send_telegram_notification_success(monkeypatch):
    fake_settings = Settings(telegram_bot_token="fake_token", telegram_chat_id="fake_chat_id")
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    class MockResponse:
        def raise_for_status(self):
            pass

    async def fake_post(*args, **kwargs):
        return MockResponse()

    # Mock httpx.AsyncClient.post
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await telegram.send_telegram_notification("test message")
    assert result is True


@pytest.mark.anyio
async def test_send_telegram_notification_with_thread_id(monkeypatch):
    fake_settings = Settings(telegram_bot_token="fake_token", telegram_chat_id="fake_chat_id", telegram_thread_id=42)
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    posted_payloads = []
    class MockResponse:
        def raise_for_status(self):
            pass

    async def fake_post(url, *args, **kwargs):
        posted_payloads.append(kwargs.get("json"))
        return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await telegram.send_telegram_notification("test message")
    assert result is True
    assert len(posted_payloads) == 1
    assert posted_payloads[0]["message_thread_id"] == 42


@pytest.mark.anyio
async def test_send_telegram_notification_failure(monkeypatch):
    fake_settings = Settings(telegram_bot_token="fake_token", telegram_chat_id="fake_chat_id")
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    async def fake_post(*args, **kwargs):
        raise Exception("Network error")

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await telegram.send_telegram_notification("test message")
    assert result is False


def test_admin_add_key_sends_notification(monkeypatch):
    # Mock admin authorization
    fake_settings = Settings(
        pass_admin="vietkey",
        keys_filename="test_keys.json",
        telegram_bot_token="fake_token",
        telegram_chat_id="fake_chat_id",
    )
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    # Mock Key storage functions
    records = []
    def fake_load_keys(_s3_key):
        return records
    def fake_save_keys(_s3_key, updated_records):
        nonlocal records
        records[:] = updated_records

    monkeypatch.setattr(key_manager, "load_keys", fake_load_keys)
    monkeypatch.setattr(key_manager, "save_keys", fake_save_keys)

    # Track calls to send_telegram_notification
    called_messages = []
    async def mock_send_telegram_notification(msg: str):
        called_messages.append(msg)
        return True

    monkeypatch.setattr("routers.keys.send_telegram_notification", mock_send_telegram_notification)

    # Request to add key
    response = client.post(
        "/api/admin/keys/add",
        json={
            "name": "test_customer",
            "password": "vietkey",
            "days": 10,
            "product": "fotello",
        }
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "new"
    
    # Assert notification was triggered
    assert len(called_messages) == 1
    assert "License Key CREATED" in called_messages[0]
    assert "test_customer" in called_messages[0]
    assert "fotello" in called_messages[0]


def test_admin_delete_key_sends_notification(monkeypatch):
    fake_settings = Settings(
        pass_admin="vietkey",
        keys_filename="test_keys.json",
        telegram_bot_token="fake_token",
        telegram_chat_id="fake_chat_id",
    )
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    # Mock Key storage functions containing an existing key
    existing_key = KeyRecord(key="DELETEME123", name="delete_test", product="autohdr")
    records = [existing_key]
    def fake_load_keys(_s3_key):
        return records
    def fake_save_keys(_s3_key, updated_records):
        nonlocal records
        records[:] = updated_records

    monkeypatch.setattr(key_manager, "load_keys", fake_load_keys)
    monkeypatch.setattr(key_manager, "save_keys", fake_save_keys)

    # Track calls to send_telegram_notification
    called_messages = []
    async def mock_send_telegram_notification(msg: str):
        called_messages.append(msg)
        return True

    monkeypatch.setattr("routers.keys.send_telegram_notification", mock_send_telegram_notification)

    # Request to delete key
    response = client.post(
        "/api/admin/keys/delete",
        json={
            "key": "delete_test",
            "password": "vietkey",
        }
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    
    # Assert key deleted from storage
    assert len(records) == 0

    # Assert notification was triggered
    assert len(called_messages) == 1
    assert "License Key DELETED" in called_messages[0]
    assert "delete_test" in called_messages[0]
    assert "autohdr" in called_messages[0]


def test_admin_reset_key_sends_notification(monkeypatch):
    fake_settings = Settings(
        pass_admin="vietkey",
        keys_filename="test_keys.json",
        telegram_bot_token="fake_token",
        telegram_chat_id="fake_chat_id",
    )
    monkeypatch.setattr(Settings, "from_env", lambda *args, **kwargs: fake_settings)

    # Mock Key storage functions containing an existing key with machine_id
    existing_key = KeyRecord(key="RESETME123", name="reset_test", machine_id="old_machine_id", product="autohdr")
    records = [existing_key]
    def fake_load_keys(_s3_key):
        return records
    def fake_save_keys(_s3_key, updated_records):
        nonlocal records
        records[:] = updated_records

    monkeypatch.setattr(key_manager, "load_keys", fake_load_keys)
    monkeypatch.setattr(key_manager, "save_keys", fake_save_keys)

    # Track calls to send_telegram_notification
    called_messages = []
    async def mock_send_telegram_notification(msg: str):
        called_messages.append(msg)
        return True

    monkeypatch.setattr("routers.keys.send_telegram_notification", mock_send_telegram_notification)

    # Request to reset key
    response = client.post(
        "/api/admin/keys/reset",
        json={
            "key": "reset_test",
            "password": "vietkey",
        }
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    
    # Assert machine_id cleared from storage
    assert records[0].machine_id is None

    # Assert notification was triggered
    assert len(called_messages) == 1
    assert "License Key RESET" in called_messages[0]
    assert "reset_test" in called_messages[0]
    assert "Machine ID Cleared" in called_messages[0]
