"""Binary layouts of Assetto Corsa's telemetry interfaces."""
import ctypes
import struct

import pytest

from actrainer.sources import shared_memory as sm
from actrainer.sources import udp


def test_udp_struct_sizes():
    assert udp.CAR_INFO.size == 328
    assert udp.HANDSHAKE_RESPONSE.size == 408


def _car_info(**kw) -> bytes:
    vals = [b"a", 328, 100.0, 62.1, 27.78, True, False, False, True, False, False,
            0.1, -1.2, 0.3, 61234, 92000, 91000, 4,
            0.8, 0.0, 1.0, 6500.0, -0.2, 4, 0.3]
    wheel = [kw.get("w", 89.6)] * 4 + [0.0] * 36           # wheelAngularSpeed + 9 more arrays
    radius = [0.0] * 4 + [0.31] * 4 + [0.30] * 4            # camber, tyreRadius, loaded radius
    tail = [0.0] * 4 + [0.4321, 0.01, 10.0, 2.0, -30.0]     # susp height, pos, slope, xyz
    return udp.CAR_INFO.pack(*vals, *wheel, *radius, *tail)


def test_udp_car_info_fields():
    s = udp.parse_car_info(_car_info(), t=5.0)
    assert s.speed == 100.0 and s.gas == pytest.approx(0.8) and s.rpm == 6500.0
    assert s.gear == 3                    # AC 4 = 3rd gear (0 = R, 1 = N)
    assert abs(s.pos - 0.4321) < 1e-6
    assert (s.x, s.z) == (10.0, -30.0)
    assert s.lap_time == 61.234 and s.last_lap == 92.0 and s.laps == 4
    assert s.g_lat == pytest.approx(-1.2)
    assert abs(s.slip_fl) < 0.01          # 89.6 rad/s * 0.31 m ~= 27.78 m/s


def test_udp_lockup_slip():
    s = udp.parse_car_info(_car_info(w=10.0), t=0)
    assert s.slip_fl < -0.8


def test_udp_handshake():
    def w(text):
        return text.encode("utf-16-le").ljust(100, b"\0")
    data = udp.HANDSHAKE_RESPONSE.pack(w("ks_mazda_mx5_cup%"), w("Gio"), 1, 1, w("magione"), w(""))
    info = udp.parse_handshake(data)
    assert (info.car, info.track, info.track_config, info.driver) == ("ks_mazda_mx5_cup", "magione", "", "Gio")


def test_shared_memory_offsets():
    # wchar_t must stay 2 bytes regardless of the platform the tests run on
    assert sm.Static.sectorCount.offset == 400
    assert sm.Static.maxRpm.offset == 412
    assert sm.Graphics.completedLaps.offset == 132
    assert sm.Graphics.iCurrentTime.offset == 140
    assert sm.Graphics.normalizedCarPosition.offset == 248
    assert sm.Physics.numberOfTyresOut.offset == 244
    assert sm.Physics.clutch.offset == 364
    assert ctypes.sizeof(sm.Physics) == 580


def test_decode_wstr():
    arr = (ctypes.c_uint16 * 8)(*[ord(c) for c in "monza"], 0, 65, 66)
    assert sm.decode_wstr(arr) == "monza"


def test_request_packet():
    assert struct.unpack("<iii", udp._REQUEST.pack(1, 1, udp.SUBSCRIBE_UPDATE)) == (1, 1, 1)
