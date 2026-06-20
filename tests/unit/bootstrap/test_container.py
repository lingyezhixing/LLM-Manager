import pathlib

import pytest

from llm_manager.bootstrap.container import AppContainer

VALID_YAML = """
program:
  host: "127.0.0.1"
  port: 9090
  data_dir: ./data
Local-Models:
  Qwen:
    aliases: ["Qwen"]
    mode: "Chat"
    port: 10001
    V100:
      required_devices: ["v100"]
      script_path: "qwen.bat"
      memory_mb: {v100: 8000}
"""


def _write_cfg(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "config.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return p


def test_container_constructs_and_wires(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = AppContainer(_write_cfg(tmp_path))
    assert c.config.program.port == 9090
    assert c.runtime is c.runtime
    assert c.ops is not None
    assert c.gateway is not None
    assert c.app is not None
    c.shutdown()


def test_container_rejects_invalid_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "config.yaml"
    bad.write_text(
        'program: {host: "0.0.0.0", port: 1}\n'
        'Local-Models:\n  A:\n    aliases: ["A"]\n    mode: "Chat"\n    port: 1\n'
        '    V1: {required_devices: ["v100"], script_path: "a.bat", memory_mb: {v100: 1}}\n'
        '  B:\n    aliases: ["B"]\n    mode: "Chat"\n    port: 1\n'
        '    V2: {required_devices: ["v100"], script_path: "b.bat", memory_mb: {v100: 1}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        AppContainer(bad)
