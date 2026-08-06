from llm_manager.tray.wol import build_magic_packet


def test_magic_packet_shape():
    pkt = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(pkt) == 102  # 6 + 16*6
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes.fromhex("AABBCCDDEEFF")


def test_magic_packet_rejects_bad_mac():
    import pytest

    with pytest.raises(ValueError):
        build_magic_packet("not-a-mac")
