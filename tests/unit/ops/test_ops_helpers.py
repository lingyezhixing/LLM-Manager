import json
from unittest import mock

from llm_manager.ops import claude_settings, wol


def test_wol_packet_layout():
    pkt = wol.build_magic_packet("a8:b8:e0:08:12:ff")
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == b"\xa8\xb8\xe0\x08\x12\xff" * 16


def test_send_wol_uses_broadcast_and_port(monkeypatch):
    sent: list = []
    fake_sock = mock.MagicMock()
    fake_sock.__enter__.return_value = fake_sock  # `with socket(...) as sock` binds here
    fake_sock.__exit__.return_value = None
    fake_sock.sendto = lambda data, addr: sent.append((data, addr))
    monkeypatch.setattr(wol.socket, "socket", lambda *a, **k: fake_sock)
    wol.send_wol_packet("192.168.50.255", "a8:b8:e0:08:12:ff")
    assert sent and sent[0][1] == ("192.168.50.255", 9)


def test_apply_preset_preserves_other_keys(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"env": {"FOO": "bar"}, "other": 1}), encoding="utf-8")
    claude_settings.apply_preset(p, {"ANTHROPIC_BASE_URL": "https://x", "ANTHROPIC_API_KEY": "k"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["env"]["FOO"] == "bar"  # preserved
    assert data["other"] == 1  # preserved
    assert data["env"]["ANTHROPIC_BASE_URL"] == "https://x"
    assert data["env"]["ANTHROPIC_API_KEY"] == "k"


def test_apply_preset_creates_env_when_absent(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{}", encoding="utf-8")
    claude_settings.apply_preset(p, {"ANTHROPIC_BASE_URL": "https://x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"] == "https://x"
