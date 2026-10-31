"""Assetto Corsa "remote telemetry" UDP protocol (port 9996).

Lets the coach run on another machine on the same network. It carries less than
shared memory (no track length, rev limit or track-limit info), so those are estimated.
"""
from __future__ import annotations

import socket
import struct
import time

from ..sample import Sample, SessionInfo
from .base import SourceClosed

HANDSHAKE, SUBSCRIBE_UPDATE, DISMISS = 0, 1, 3
_REQUEST = struct.Struct("<iii")
# handshakerResponse: wchar carName[50], wchar driverName[50], int identifier, int version,
#                     wchar trackName[50], wchar trackConfig[50]
HANDSHAKE_RESPONSE = struct.Struct("<100s100sii100s100s")
# RTCarInfo, 328 bytes
CAR_INFO = struct.Struct("<c3xi3f6?2x3f4i5fif40f12f4fff3f")


def _decode(raw: bytes) -> str:
    text = raw.decode("utf-16-le", errors="ignore")
    return text.split("\x00", 1)[0].split("%", 1)[0].strip()


def parse_handshake(data: bytes) -> SessionInfo:
    car, driver, _ident, _ver, track, config = HANDSHAKE_RESPONSE.unpack_from(data)
    return SessionInfo(car=_decode(car), track=_decode(track), track_config=_decode(config),
                       driver=_decode(driver), source="udp")


def parse_car_info(data: bytes, t: float) -> Sample:
    f = CAR_INFO.unpack_from(data)
    # f[0:2] identifier, size | f[2:5] speeds | f[5:11] flags | f[11:14] accG
    speed_kmh, speed_ms = f[2], f[4]
    is_in_pit = f[9]
    acc_vertical, acc_horizontal, acc_frontal = f[11:14]
    lap_time, last_lap, _best, lap_count = f[14:18]
    gas, brake, clutch, rpm, steer = f[18:23]
    gear = f[23]
    wheel_speed = f[25:29]          # f[25:65] ten 4-wheel arrays, f[65:77] camber/radius/loaded
    tyre_radius = f[69:73]
    pos = f[81]                     # f[77:81] suspension height, f[82] slope
    x, _y, z = f[83:86]
    slips = [0.0] * 4
    if speed_ms > 3.0:
        for i in range(4):
            r = tyre_radius[i] if tyre_radius[i] > 0.05 else 0.3
            slips[i] = abs(wheel_speed[i]) * r / speed_ms - 1.0
    return Sample(
        t=t, lap_time=lap_time / 1000.0, laps=lap_count, last_lap=max(last_lap, 0) / 1000.0,
        pos=pos, speed=speed_kmh, gas=gas, brake=brake, clutch=clutch, gear=gear - 1,
        rpm=rpm, steer=steer, g_lat=acc_horizontal, g_lon=acc_frontal, x=x, z=z,
        slip_fl=slips[0], slip_fr=slips[1], slip_rl=slips[2], slip_rr=slips[3],
        in_pit=int(is_in_pit),
    )


class UdpSource:
    name = "udp"
    TIMEOUT = 5.0

    def __init__(self, host: str = "127.0.0.1", port: int = 9996):
        self.addr = (host, port)
        self.sock: socket.socket | None = None
        self._last_rx = 0.0

    def _send(self, op: int) -> None:
        self.sock.sendto(_REQUEST.pack(1, 1, op), self.addr)

    def open(self) -> SessionInfo | None:
        self.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        try:
            self._send(HANDSHAKE)
            data, _ = self.sock.recvfrom(2048)
        except OSError:
            self.close()
            return None
        if len(data) < HANDSHAKE_RESPONSE.size:
            self.close()
            return None
        info = parse_handshake(data)
        self._send(SUBSCRIBE_UPDATE)
        self._last_rx = time.monotonic()
        return info

    def read(self) -> Sample | None:
        try:
            data, _ = self.sock.recvfrom(2048)
        except socket.timeout:
            if time.monotonic() - self._last_rx > self.TIMEOUT:
                raise SourceClosed("no UDP telemetry")
            return None
        now = time.monotonic()
        if len(data) < CAR_INFO.size:
            return None
        self._last_rx = now
        return parse_car_info(data, now)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self._send(DISMISS)
            except OSError:
                pass
            self.sock.close()
            self.sock = None
