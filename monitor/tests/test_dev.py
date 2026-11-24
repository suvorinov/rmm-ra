from __future__ import annotations

from scripts.dev import parse_env_file


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
