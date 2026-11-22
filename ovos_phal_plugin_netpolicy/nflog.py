"""Reading NFLOG: which unit tried to reach which address, as it happens.

The refuse chains log every refused packet to an NFLOG group with the unit's
name as the prefix. This reads that group over netlink with nothing but the
standard library (the format is the kernel's nfnetlink_log), and gives
``(prefix, family, address, port)`` per packet. It works in an unprivileged
LXC container too, inside the container's own network namespace.
"""
import ipaddress
import socket
import struct

NETLINK_NETFILTER = 12
NFNL_SUBSYS_ULOG = 4
NFULNL_MSG_PACKET, NFULNL_MSG_CONFIG = 0, 1
NFULA_CFG_CMD, NFULA_CFG_MODE = 1, 2
NFULNL_CFG_CMD_BIND, NFULNL_CFG_CMD_UNBIND, NFULNL_CFG_CMD_PF_BIND = 1, 2, 3
NFULNL_COPY_PACKET = 2
NFULA_PAYLOAD, NFULA_PREFIX = 9, 10
NLM_F_REQUEST, NLM_F_ACK = 1, 4
NLMSG_ERROR = 2
#: only the headers are needed: IP (up to 40 bytes for IPv6) and the ports
COPY_BYTES = 64


def _attr(kind: int, data: bytes) -> bytes:
    length = 4 + len(data)
    return struct.pack("=HH", length, kind) + data + b"\0" * ((4 - length % 4) % 4)


def config_message(family: int, group: int, attrs: list, seq: int = 0) -> bytes:
    body = struct.pack("=BBH", family, 0, socket.htons(group)) + b"".join(attrs)
    header = struct.pack("=IHHII", 16 + len(body), (NFNL_SUBSYS_ULOG << 8) | NFULNL_MSG_CONFIG,
                         NLM_F_REQUEST | NLM_F_ACK, seq, 0)
    return header + body


def destination(payload: bytes):
    """(family, address, port) from the start of an IP packet."""
    if len(payload) >= 20 and payload[0] >> 4 == 4:
        ihl = (payload[0] & 0x0F) * 4
        address = str(ipaddress.IPv4Address(payload[16:20]))
        port = struct.unpack("!H", payload[ihl + 2:ihl + 4])[0] if len(payload) >= ihl + 4 else 0
        return 4, address, port
    if len(payload) >= 40 and payload[0] >> 4 == 6:
        address = str(ipaddress.IPv6Address(payload[24:40]))
        port = struct.unpack("!H", payload[42:44])[0] if len(payload) >= 44 else 0
        return 6, address, port
    return 0, "", 0


def parse(data: bytes) -> list:
    """Every logged packet in one netlink read: [(prefix, family, address, port)]."""
    out, off = [], 0
    packet = (NFNL_SUBSYS_ULOG << 8) | NFULNL_MSG_PACKET
    while off + 16 <= len(data):
        length, kind = struct.unpack("=IH", data[off:off + 6])
        if length < 16 or off + length > len(data):
            break
        if kind == packet:
            prefix, payload = "", b""
            p = off + 20  # netlink header + nfgenmsg
            while p + 4 <= off + length:
                alen, atype = struct.unpack("=HH", data[p:p + 4])
                if alen < 4:
                    break
                value = data[p + 4:p + alen]
                atype &= 0x7FFF
                if atype == NFULA_PREFIX:
                    prefix = value.rstrip(b"\0").decode("utf-8", "replace")
                elif atype == NFULA_PAYLOAD:
                    payload = value
                p += (alen + 3) & ~3
            out.append((prefix,) + destination(payload))
        off += (length + 3) & ~3
    return out


class NflogReader:
    """Bound to one NFLOG group; ``read()`` blocks until packets come (or
    the timeout) and returns them parsed."""

    def __init__(self, group: int, timeout: float = 1.0):
        self.group = group
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_NETFILTER)
        self.sock.bind((0, 0))
        self.sock.settimeout(5)
        for family in (socket.AF_INET, socket.AF_INET6):
            self._config(family, 0, [_attr(NFULA_CFG_CMD, struct.pack("=B", NFULNL_CFG_CMD_PF_BIND))])
        self._config(0, group, [_attr(NFULA_CFG_CMD, struct.pack("=B", NFULNL_CFG_CMD_BIND))])
        self._config(0, group, [_attr(NFULA_CFG_MODE, struct.pack("!IB", COPY_BYTES, NFULNL_COPY_PACKET) + b"\0\0\0")])
        self.sock.settimeout(timeout)

    def _config(self, family: int, group: int, attrs: list):
        self.sock.send(config_message(family, group, attrs))
        ack = self.sock.recv(4096)
        kind = struct.unpack("=H", ack[4:6])[0]
        if kind == NLMSG_ERROR:
            err = struct.unpack("=i", ack[16:20])[0]
            if err:
                raise OSError(-err, f"NFLOG group {group}: {err}")

    def read(self) -> list:
        try:
            return parse(self.sock.recv(65536))
        except socket.timeout:
            return []

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
