from __future__ import annotations

import json
import subprocess

from app.mc import MeshCentralClient


def test_large_meshctrl_output_is_not_truncated(settings, monkeypatch):
    """node закрывает stdout раньше чтения pipe — обрезка на 64 KiB."""
    settings.meshctrl_path.write_text("// fake", encoding="utf-8")
    devices = [{"_id": f"node//{i}", "name": f"ws-{i}"} for i in range(2000)]
    payload = json.dumps(devices, indent=2)
    assert len(payload) > 70_000

    def fake_run(command, **kwargs):  # noqa: ARG001
        kwargs["stdout"].write(payload)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert len(MeshCentralClient(settings).list_devices()) == 2000
