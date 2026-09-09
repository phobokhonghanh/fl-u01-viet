"""Unit tests for Shared CDP and Autoenhance CDP Token Extraction.

All tests run completely offline with mocked endpoints, WebSockets, and processes.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.autoenhance.constants import (
    API_KEY_REGEX,
    DOMAIN,
    SETTINGS_URL,
)
from core.autoenhance.cdp import (
    extract_and_save_api_key,
    extract_api_key,
)
from core.shared.cdp import (
    CDPClient,
    call,
    eval_js,
    find_chrome,
    find_or_open_tab,
    get_chrome_profile_dir,
    get_cookie_header,
    get_cookies,
    launch_chrome,
    open_tab,
    request_json,
    tabs,
    wait_for_debugging,
)


# ---------------------------------------------------------------------------
# Shared CDP Tests
# ---------------------------------------------------------------------------

def test_find_chrome_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome = tmp_path / "fake_chrome"
    fake_chrome.touch()
    monkeypatch.setenv("CHROME_PATH", str(fake_chrome))
    assert find_chrome() == str(fake_chrome)


def test_get_chrome_profile_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    profile_dir = get_chrome_profile_dir("test_profile")
    assert profile_dir.is_dir()
    assert profile_dir.name == "test_profile"


def test_launch_chrome_not_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHROME_PATH", "/non/existent/path/chrome")
    with patch("core.shared.cdp.find_chrome", return_value=None):
        with pytest.raises(FileNotFoundError, match="Google Chrome executable not found"):
            launch_chrome()


def test_launch_chrome_args(tmp_path: Path):
    fake_exe = tmp_path / "chrome.exe"
    fake_exe.touch()
    fake_profile = tmp_path / "profile"

    with patch("subprocess.Popen") as mock_popen:
        launch_chrome(
            chrome_path=str(fake_exe),
            port=9333,
            profile_dir=fake_profile,
            url="https://example.com",
            headless=True,
            extra_args=["--custom-flag"],
        )
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == str(fake_exe)
        assert "--remote-debugging-port=9333" in cmd
        assert "--remote-allow-origins=*" in cmd
        assert f"--user-data-dir={fake_profile}" in cmd
        assert "--headless=new" in cmd
        assert "--custom-flag" in cmd
        assert "https://example.com" in cmd


def test_request_json():
    fake_response = MagicMock()
    fake_response.read.return_value = b'{"Browser": "Chrome/120"}'
    fake_response.__enter__.return_value = fake_response

    fake_opener = MagicMock()
    fake_opener.open.return_value = fake_response

    with patch("urllib.request.build_opener", return_value=fake_opener):
        res = request_json("/json/version", port=9222)
        assert res == {"Browser": "Chrome/120"}


def test_wait_for_debugging_success():
    with patch("core.shared.cdp.request_json", return_value={"Browser": "Chrome/120"}):
        assert wait_for_debugging(port=9222, timeout=2.0, interval=0.01) is True


def test_wait_for_debugging_stop_event():
    stop_event = threading.Event()
    stop_event.set()
    with patch("core.shared.cdp.request_json", side_effect=Exception("Connection refused")):
        assert wait_for_debugging(port=9222, timeout=2.0, interval=0.01, stop_event=stop_event) is False


def test_tabs_filter():
    mock_tabs = [
        {"type": "page", "id": "1", "url": "https://a.com", "webSocketDebuggerUrl": "ws://1"},
        {"type": "background_page", "id": "2", "url": "https://b.com"},
        {"type": "page", "id": "3", "url": "https://c.com"},  # missing ws
    ]
    with patch("core.shared.cdp.request_json", return_value=mock_tabs):
        tab_list = tabs(port=9222)
        assert len(tab_list) == 1
        assert tab_list[0]["id"] == "1"


def test_open_tab():
    with patch("core.shared.cdp.request_json", return_value={"id": "new_tab", "url": "https://a.com"}) as mock_json:
        res = open_tab("https://a.com?q=1&b=2", port=9222)
        assert res["id"] == "new_tab"
        mock_json.assert_called_once()
        assert mock_json.call_args[0][0] == "/json/new?https://a.com?q=1&b=2"


def test_find_or_open_tab():
    existing = [{"type": "page", "id": "1", "url": "https://app.autoenhance.ai/orders", "webSocketDebuggerUrl": "ws://1"}]
    with patch("core.shared.cdp.tabs", return_value=existing):
        found = find_or_open_tab("autoenhance.ai", port=9222)
        assert found is not None
        assert found["id"] == "1"


def test_call_success():
    tab = {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/1"}
    fake_ws = MagicMock()

    sent_payload = []

    def mock_send(data):
        sent_payload.append(json.loads(data))

    fake_ws.send.side_effect = mock_send

    def mock_recv():
        req_id = sent_payload[-1]["id"]
        return json.dumps({"id": req_id, "result": {"cookies": [{"name": "sid", "value": "123"}]}})

    fake_ws.recv.side_effect = mock_recv

    with patch("websocket.create_connection", return_value=fake_ws):
        res = call(tab, "Network.getAllCookies", timeout=2.0)
        assert res == {"cookies": [{"name": "sid", "value": "123"}]}
        fake_ws.close.assert_called_once()


def test_call_error():
    tab = {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/1"}
    fake_ws = MagicMock()

    sent_payload = []

    def mock_send(data):
        sent_payload.append(json.loads(data))

    fake_ws.send.side_effect = mock_send

    def mock_recv():
        req_id = sent_payload[-1]["id"]
        return json.dumps({"id": req_id, "error": {"message": "Invalid method"}})

    fake_ws.recv.side_effect = mock_recv

    with patch("websocket.create_connection", return_value=fake_ws):
        with pytest.raises(RuntimeError, match="Invalid method"):
            call(tab, "Network.invalid", timeout=2.0)


def test_eval_js():
    tab = {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/1"}
    with patch("core.shared.cdp.call", return_value={"result": {"value": 42}}):
        val = eval_js(tab, "21 + 21")
        assert val == 42


def test_get_cookies_and_header():
    tab = {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/1"}
    mock_cookies = [
        {"name": "sess", "value": "abc", "domain": ".autoenhance.ai"},
        {"name": "token", "value": "xyz", "domain": ".autoenhance.ai"},
        {"name": "other", "value": "123", "domain": ".google.com"},
    ]
    with patch("core.shared.cdp.call", return_value={"cookies": mock_cookies}):
        cookie_list = get_cookies(tab, domain="autoenhance.ai")
        assert len(cookie_list) == 2
        header = get_cookie_header(tab, domain="autoenhance.ai")
        assert header == "sess=abc; token=xyz"


def test_cdp_client_facade():
    client = CDPClient(port=9444)
    with patch("core.shared.cdp.request_json", return_value={"Browser": "Chrome/120"}):
        assert client.is_alive() is True


# ---------------------------------------------------------------------------
# Autoenhance Token Extraction Tests
# ---------------------------------------------------------------------------

def test_api_key_regex_match():
    # Test standard JSON
    text1 = '{"user": {"key": "12345678-1234-1234-1234-123456789abc"}}'
    m1 = API_KEY_REGEX.search(text1)
    assert m1 is not None
    assert m1.group(1) == "12345678-1234-1234-1234-123456789abc"

    # Test escaped Next.js / HTML attribute JSON
    text2 = r'{\"key\":\"abcdef01-2345-6789-abcd-ef0123456789\"}'
    m2 = API_KEY_REGEX.search(text2)
    assert m2 is not None
    assert m2.group(1) == "abcdef01-2345-6789-abcd-ef0123456789"

    # Test modern Autoenhance 40-character Base62 API key
    text3 = '"apiKey":"ebO1bvCt9i8WPo9adU7aF13u6aQM08Yf1KUy3sMO","preferences":null'
    m3 = API_KEY_REGEX.search(text3)
    assert m3 is not None
    assert m3.group(1) == "ebO1bvCt9i8WPo9adU7aF13u6aQM08Yf1KUy3sMO"

    # Test modern Autoenhance escaped Next.js state
    text4 = r'{\"apiKey\":\"ebO1bvCt9i8WPo9adU7aF13u6aQM08Yf1KUy3sMO\"}'
    m4 = API_KEY_REGEX.search(text4)
    assert m4 is not None
    assert m4.group(1) == "ebO1bvCt9i8WPo9adU7aF13u6aQM08Yf1KUy3sMO"


def test_extract_api_key_success():
    tab = {"webSocketDebuggerUrl": "ws://tab1"}
    mock_cookies = [{"name": "session", "value": "s123", "domain": ".autoenhance.ai"}]
    logs = []

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"apiKey": {"key": "12345678-abcd-1234-abcd-123456789abc"}}'

    with patch("core.autoenhance.cdp.get_cookies", return_value=mock_cookies), \
         patch("requests.get", return_value=mock_resp) as mock_get:
        api_key = extract_api_key(tab, timeout=5.0, log_fn=logs.append)
        assert api_key == "12345678-abcd-1234-abcd-123456789abc"
        mock_get.assert_called_once_with(
            SETTINGS_URL,
            headers={"Cookie": "session=s123", "User-Agent": mock_get.call_args[1]["headers"]["User-Agent"]},
            timeout=5.0,
        )
        assert any("Extracted API key" in l for l in logs)


def test_extract_api_key_no_cookies():
    tab = {"webSocketDebuggerUrl": "ws://tab1"}
    logs = []

    with patch("core.autoenhance.cdp.get_cookies", return_value=[]):
        api_key = extract_api_key(tab, log_fn=logs.append)
        assert api_key is None
        assert any("No autoenhance.ai cookies found" in l for l in logs)


def test_extract_api_key_http_error():
    tab = {"webSocketDebuggerUrl": "ws://tab1"}
    mock_cookies = [{"name": "session", "value": "s123", "domain": ".autoenhance.ai"}]
    logs = []

    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("core.autoenhance.cdp.get_cookies", return_value=mock_cookies), \
         patch("requests.get", return_value=mock_resp):
        api_key = extract_api_key(tab, log_fn=logs.append)
        assert api_key is None
        assert any("Settings page returned 403" in l for l in logs)


def test_extract_api_key_missing_in_html():
    tab = {"webSocketDebuggerUrl": "ws://tab1"}
    mock_cookies = [{"name": "session", "value": "s123", "domain": ".autoenhance.ai"}]
    logs = []

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body>Welcome to Autoenhance</body></html>"

    with patch("core.autoenhance.cdp.get_cookies", return_value=mock_cookies), \
         patch("requests.get", return_value=mock_resp):
        api_key = extract_api_key(tab, log_fn=logs.append)
        assert api_key is None
        assert any("API key not found in settings page HTML" in l for l in logs)


def test_extract_and_save_api_key_orchestration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    logs = []
    fake_key = "12345678-abcd-1234-abcd-123456789abc"

    tab = {"id": "1", "url": "https://app.autoenhance.ai", "webSocketDebuggerUrl": "ws://1"}

    with patch("core.autoenhance.cdp.CDPClient.is_alive", return_value=True), \
         patch("core.autoenhance.cdp.find_or_open_tab", return_value=tab), \
         patch("core.autoenhance.cdp.extract_api_key", return_value=fake_key), \
         patch("core.autoenhance.cdp.validate_api_key", return_value=True) as mock_val, \
         patch("core.autoenhance.cdp.save_api_key") as mock_save:
        ok, key = extract_and_save_api_key(
            port=9222,
            launch_browser=False,
            timeout=5.0,
            log_fn=logs.append,
        )
        assert ok is True
        assert key == fake_key
        mock_val.assert_called_once_with(fake_key)
        mock_save.assert_called_once_with(fake_key)


def test_extract_and_save_api_key_stop_event():
    stop_event = threading.Event()
    stop_event.set()
    logs = []

    tab = {"id": "1", "url": "https://app.autoenhance.ai", "webSocketDebuggerUrl": "ws://1"}

    with patch("core.autoenhance.cdp.CDPClient.is_alive", return_value=True), \
         patch("core.autoenhance.cdp.find_or_open_tab", return_value=tab):
        ok, msg = extract_and_save_api_key(
            port=9222,
            launch_browser=False,
            timeout=5.0,
            log_fn=logs.append,
            stop_event=stop_event,
        )
        assert ok is False
        assert msg == "Dừng luồng liên kết"
