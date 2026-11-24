from __future__ import annotations

import base64

import pytest

from app.config import Settings, redact
from app.mc import MeshCentralError, parse_json_list, powershell_encoded_command


def test_redact_password_and_login_key():
    text = "boom s3cret-pass ws://x/control.ashx?key=abcdef123"
    cleaned = redact(text, ("s3cret-pass",))
    assert "s3cret-pass" not in cleaned
    assert "abcdef123" not in cleaned
    assert "key=***" in cleaned


def test_powershell_encoded_command_is_utf16le():
    command = powershell_encoded_command("Write-Output 'привет'")
    encoded = command.rsplit(" ", 1)[-1]
    decoded = base64.b64decode(encoded).decode("utf-16-le")
    assert decoded == "Write-Output 'привет'"
    assert "-EncodedCommand" in command


def test_parse_json_list_ignores_meshctrl_warning():
    output = 'Warning: config is readable\n[{"_id": "node//a"}]\n'
    assert parse_json_list(output) == [{"_id": "node//a"}]


def test_parse_json_list_rejects_login_error():
    with pytest.raises(MeshCentralError):
        parse_json_list("Invalid login.")


def test_settings_validate_tz(monkeypatch, tmp_path):
    monkeypatch.setenv("MON_AUTH_USER", "u")
    monkeypatch.setenv("MON_AUTH_PASS", "p")
    monkeypatch.setenv("MON_MC_USER", "svc")
    monkeypatch.setenv("MON_MC_PASS", "pw")
    monkeypatch.setenv("MON_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("MON_TZ", "Nowhere/Unknown")
    with pytest.raises(ValueError, match="MON_TZ"):
        Settings.from_env()


def test_settings_require_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("MON_MC_USER", "svc")
    monkeypatch.setenv("MON_MC_PASS", "pw")
    monkeypatch.setenv("MON_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.delenv("MON_AUTH_USER", raising=False)
    monkeypatch.delenv("MON_AUTH_PASS", raising=False)
    monkeypatch.delenv("MON_ALLOW_ANONYMOUS", raising=False)
    with pytest.raises(ValueError, match="MON_AUTH_USER"):
        Settings.from_env()
