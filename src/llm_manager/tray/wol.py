"""Wake-on-LAN magic packet + send."""
from __future__ import annotations

import socket


def build_magic_packet(mac: str) -> bytes:
    mac_clean = mac.replace("-", ":").replace(".", ":").strip()
    mac_bytes = bytes.fromhex(mac_clean.replace(":", ""))
    if len(mac_bytes) != 6:
        raise ValueError(f"bad mac: {mac}")
    return b"\xff" * 6 + mac_bytes * 16


def send_wol(mac: str, broadcast: str, port: int = 9) -> None:
    pkt = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, (broadcast, port))
