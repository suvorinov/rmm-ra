from __future__ import annotations

from scripts.dev import local_meshcentral_url, parse_env_file, safe_url


def test_parse_env_file_ignores_comments_and_keeps_dollar(tmp_path):
    env = tmp_path / "dev.env"
    env.write_text(
        "# комментарий\n"
        "MON_AUTH_USER=dev\n"
        'MON_AUTH_PASS="p$word"\n'
        "export MON_TITLE = 'RMM (dev)'\n"
        "STRING_WITHOUT_EQUALS\n",
        encoding="utf-8",
    )
    assert parse_env_file(env) == {
        "MON_AUTH_USER": "dev",
        "MON_AUTH_PASS": "p$word",
        "MON_TITLE": "RMM (dev)",
    }


def test_docker_meshcentral_url_is_rewritten():
    assert local_meshcentral_url("ws://meshcentral:443") == (
        "wss://rmm.zr.local",
        "rmm.zr.local",
    )
    assert local_meshcentral_url("ws://meshcentral:443?key=secret", "dev.local") == (
        "wss://dev.local?key=secret",
        "dev.local",
    )
    assert local_meshcentral_url("wss://other.example") == ("wss://other.example", None)


def test_safe_url_hides_login_key():
    assert safe_url("wss://rmm.zr.local?key=topsecret") == "wss://rmm.zr.local?key=***"
