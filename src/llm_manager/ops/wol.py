"""Wake-on-LAN magic packet (extracted from tray.py; protocol-mandated layout)."""

from __future__ import annotations

import socket


def build_magic_packet(mac: str) -> bytes:
    """6x0xFF + 16x MAC. mac is colon-separated hex."""
    hex_bytes = bytes(int(b, 16) for b in mac.split(":"))
    if len(hex_bytes) != 6:
        raise ValueError(f"bad MAC: {mac!r}")
    return b"\xff" * 6 + hex_bytes * 16


def send_wol_packet(broadcast_address: str, mac: str) -> None:
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast_address, 9))
