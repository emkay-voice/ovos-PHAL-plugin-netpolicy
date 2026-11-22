"""NFLOG messages as the kernel sends them, parsed."""
import ipaddress
import socket
import struct

from ovos_phal_plugin_netpolicy import nflog


def packet_message(prefix: str, payload: bytes) -> bytes:
    attrs = nflog._attr(nflog.NFULA_PREFIX, prefix.encode() + b"\0") + nflog._attr(nflog.NFULA_PAYLOAD, payload)
    body = struct.pack("=BBH", socket.AF_INET, 0, socket.htons(4711)) + attrs
    kind = (nflog.NFNL_SUBSYS_ULOG << 8) | nflog.NFULNL_MSG_PACKET
    return struct.pack("=IHHII", 16 + len(body), kind, 0, 0, 0) + body


def ipv4(dst: str, port: int) -> bytes:
    header = bytes([0x45, 0, 0, 60, 0, 0, 0, 0, 64, 6, 0, 0]) + bytes(4) + ipaddress.IPv4Address(dst).packed
    return header + struct.pack("!HH", 40000, port)


def ipv6(dst: str, port: int) -> bytes:
    header = bytes([0x60, 0, 0, 0, 0, 20, 6, 64]) + bytes(16) + ipaddress.IPv6Address(dst).packed
    return header + struct.pack("!HH", 40000, port)


def test_one_and_two_packets_in_a_read():
    data = packet_message("ovos-core", ipv4("185.15.59.224", 443))
    assert nflog.parse(data) == [("ovos-core", 4, "185.15.59.224", 443)]
    data += packet_message("ovos-audio", ipv6("2a02:ec80:300:ed1a::1", 53))
    assert nflog.parse(data)[1] == ("ovos-audio", 6, "2a02:ec80:300:ed1a::1", 53)


def test_rubbish_is_not_a_crash():
    assert nflog.parse(b"\x00" * 7) == []
    assert nflog.parse(packet_message("x", b"\x45")) == [("x", 0, "", 0)]
