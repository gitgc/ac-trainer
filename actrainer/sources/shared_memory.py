"""Assetto Corsa shared memory reader (Windows only).

AC publishes three memory-mapped pages: Local\\acpmf_physics, Local\\acpmf_graphics and
Local\\acpmf_static. The layouts below are prefixes of the AC 1.16 structures (CSP keeps
them unchanged). We only ever *open* existing mappings: creating them ourselves before AC
starts could leave AC with an undersized page and break every telemetry app.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import c_float, c_int32, c_uint16

from ..sample import Sample, SessionInfo
from .base import SessionChanged, SourceClosed, SourceError

AC_OFF, AC_REPLAY, AC_LIVE, AC_PAUSE = 0, 1, 2, 3


def _wstr(n: int):
    # wchar_t is 2 bytes on Windows; c_wchar would be 4 bytes elsewhere, so use uint16
    return c_uint16 * n


def decode_wstr(arr) -> str:
    chars = []
    for code in arr:
        if code == 0:
            break
        chars.append(chr(code))
    return "".join(chars).strip()


class Physics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpms", c_int32),
        ("steerAngle", c_float),
        ("speedKmh", c_float),
        ("velocity", c_float * 3),
        ("accG", c_float * 3),
        ("wheelSlip", c_float * 4),
        ("wheelLoad", c_float * 4),
        ("wheelsPressure", c_float * 4),
        ("wheelAngularSpeed", c_float * 4),
        ("tyreWear", c_float * 4),
        ("tyreDirtyLevel", c_float * 4),
        ("tyreCoreTemperature", c_float * 4),
        ("camberRAD", c_float * 4),
        ("suspensionTravel", c_float * 4),
        ("drs", c_float),
        ("tc", c_float),
        ("heading", c_float),
        ("pitch", c_float),
        ("roll", c_float),
        ("cgHeight", c_float),
        ("carDamage", c_float * 5),
        ("numberOfTyresOut", c_int32),
        ("pitLimiterOn", c_int32),
        ("abs", c_float),
        ("kersCharge", c_float),
        ("kersInput", c_float),
        ("autoShifterOn", c_int32),
        ("rideHeight", c_float * 2),
        ("turboBoost", c_float),
        ("ballast", c_float),
        ("airDensity", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("localAngularVel", c_float * 3),
        ("finalFF", c_float),
        ("performanceMeter", c_float),
        ("engineBrake", c_int32),
        ("ersRecoveryLevel", c_int32),
        ("ersPowerLevel", c_int32),
        ("ersHeatCharging", c_int32),
        ("ersIsCharging", c_int32),
        ("kersCurrentKJ", c_float),
        ("drsAvailable", c_int32),
        ("drsEnabled", c_int32),
        ("brakeTemp", c_float * 4),
        ("clutch", c_float),
        ("tyreTempI", c_float * 4),
        ("tyreTempM", c_float * 4),
        ("tyreTempO", c_float * 4),
        ("isAIControlled", c_int32),
        ("tyreContactPoint", c_float * 12),
        ("tyreContactNormal", c_float * 12),
        ("tyreContactHeading", c_float * 12),
        ("brakeBias", c_float),
        ("localVelocity", c_float * 3),
    ]


class Graphics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("status", c_int32),
        ("session", c_int32),
        ("currentTime", _wstr(15)),
        ("lastTime", _wstr(15)),
        ("bestTime", _wstr(15)),
        ("split", _wstr(15)),
        ("completedLaps", c_int32),
        ("position", c_int32),
        ("iCurrentTime", c_int32),
        ("iLastTime", c_int32),
        ("iBestTime", c_int32),
        ("sessionTimeLeft", c_float),
        ("distanceTraveled", c_float),
        ("isInPit", c_int32),
        ("currentSectorIndex", c_int32),
        ("lastSectorTime", c_int32),
        ("numberOfLaps", c_int32),
        ("tyreCompound", _wstr(33)),
        ("replayTimeMultiplier", c_float),
        ("normalizedCarPosition", c_float),
        ("carCoordinates", c_float * 3),
        ("penaltyTime", c_float),
        ("flag", c_int32),
        ("idealLineOn", c_int32),
        ("isInPitLane", c_int32),
        ("surfaceGrip", c_float),
    ]


class Static(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("smVersion", _wstr(15)),
        ("acVersion", _wstr(15)),
        ("numberOfSessions", c_int32),
        ("numCars", c_int32),
        ("carModel", _wstr(33)),
        ("track", _wstr(33)),
        ("playerName", _wstr(33)),
        ("playerSurname", _wstr(33)),
        ("playerNick", _wstr(33)),
        ("sectorCount", c_int32),
        ("maxTorque", c_float),
        ("maxPower", c_float),
        ("maxRpm", c_int32),
        ("maxFuel", c_float),
        ("suspensionMaxTravel", c_float * 4),
        ("tyreRadius", c_float * 4),
        ("maxTurboBoost", c_float),
        ("deprecated_1", c_float),
        ("deprecated_2", c_float),
        ("penaltiesEnabled", c_int32),
        ("aidFuelRate", c_float),
        ("aidTireRate", c_float),
        ("aidMechanicalDamage", c_float),
        ("aidAllowTyreBlankets", c_int32),
        ("aidStability", c_float),
        ("aidAutoClutch", c_int32),
        ("aidAutoBlip", c_int32),
        ("hasDRS", c_int32),
        ("hasERS", c_int32),
        ("hasKERS", c_int32),
        ("kersMaxJ", c_float),
        ("engineBrakeSettingsCount", c_int32),
        ("ersPowerControllerCount", c_int32),
        ("trackSPlineLength", c_float),
        ("trackConfiguration", _wstr(33)),
    ]


class _View:
    """Read-only view of an existing named file mapping."""

    def __init__(self, name: str, size: int):
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenFileMappingW.restype = wintypes.HANDLE
        k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.DWORD, ctypes.c_size_t]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k32.VirtualQuery.restype = ctypes.c_size_t
        self._k32 = k32
        self.size = size
        self.handle = k32.OpenFileMappingW(0x0004, False, name)  # FILE_MAP_READ
        self.ptr = None
        if not self.handle:
            raise FileNotFoundError(name)
        self.ptr = k32.MapViewOfFile(self.handle, 0x0004, 0, 0, 0)
        if not self.ptr:
            self.close()
            raise FileNotFoundError(name)

        class _MBI(ctypes.Structure):
            _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                        ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                        ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                        ("Type", wintypes.DWORD)]

        mbi = _MBI()
        if k32.VirtualQuery(self.ptr, ctypes.byref(mbi), ctypes.sizeof(mbi)) and mbi.RegionSize < size:
            self.close()
            raise SourceError(f"{name} is smaller than expected ({mbi.RegionSize} < {size} bytes)")

    def read_into(self, struct) -> None:
        ctypes.memmove(ctypes.addressof(struct), self.ptr, ctypes.sizeof(struct))

    def close(self) -> None:
        if self.ptr:
            self._k32.UnmapViewOfFile(self.ptr)
            self.ptr = None
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


class SharedMemorySource:
    name = "shared_memory"
    STALE_AFTER = 4.0   # seconds of frozen physics while "live" = game gone

    def __init__(self, poll_hz: float = 60.0):
        if sys.platform != "win32":
            raise SourceError("Shared memory only exists on the Windows PC running Assetto Corsa. "
                              "Run the coach there, or use source = \"udp\" to connect over the network.")
        self.period = 1.0 / poll_hz
        self._views: dict[str, _View] = {}
        self._phy, self._gfx, self._static = Physics(), Graphics(), Static()
        self._info: SessionInfo | None = None
        self._radius = [0.3] * 4
        self._next = 0.0
        self._last_packet = -1
        self._last_change = 0.0
        self._static_check = 0.0

    # -- connection ---------------------------------------------------------------------
    def open(self) -> SessionInfo | None:
        self.close()
        try:
            for name, struct in (("physics", Physics), ("graphics", Graphics), ("static", Static)):
                self._views[name] = _View(f"Local\\acpmf_{name}", ctypes.sizeof(struct))
        except FileNotFoundError:
            self.close()
            return None
        self._views["static"].read_into(self._static)
        self._views["graphics"].read_into(self._gfx)
        if not decode_wstr(self._static.carModel) or self._gfx.status == AC_OFF:
            self.close()
            return None
        # make sure the game is actually running (the page may be held open by another app)
        self._views["physics"].read_into(self._phy)
        first = self._phy.packetId
        time.sleep(0.5)
        self._views["physics"].read_into(self._phy)
        self._views["graphics"].read_into(self._gfx)
        if self._phy.packetId == first and self._gfx.status == AC_LIVE:
            self.close()
            return None
        self._info = self._read_info()
        self._last_change = time.monotonic()
        self._static_check = self._last_change
        return self._info

    def _read_info(self) -> SessionInfo:
        st = self._static
        self._radius = [r if r > 0.05 else 0.3 for r in st.tyreRadius]
        name = " ".join(p for p in (decode_wstr(st.playerName), decode_wstr(st.playerSurname)) if p)
        return SessionInfo(
            car=decode_wstr(st.carModel), track=decode_wstr(st.track),
            track_config=decode_wstr(st.trackConfiguration),
            track_length=float(st.trackSPlineLength), rpm_limit=float(st.maxRpm),
            driver=name, source=self.name,
        )

    def close(self) -> None:
        for view in self._views.values():
            view.close()
        self._views.clear()

    # -- sampling -----------------------------------------------------------------------
    def read(self) -> Sample | None:
        now = time.monotonic()
        if self._next > now:
            time.sleep(self._next - now)
            now = time.monotonic()
        self._next = max(self._next + self.period, now)

        phy, gfx = self._phy, self._gfx
        self._views["physics"].read_into(phy)
        self._views["graphics"].read_into(gfx)

        if now - self._static_check > 2.0:
            self._static_check = now
            self._views["static"].read_into(self._static)
            info = self._read_info()
            if (info.car, info.track_id) != (self._info.car, self._info.track_id):
                raise SessionChanged(f"{info.car} @ {info.track_id}")

        if gfx.status == AC_OFF:
            raise SourceClosed("game left the session")
        if gfx.status != AC_LIVE:          # paused or watching a replay
            self._last_change = now
            return None
        if phy.packetId == self._last_packet:
            if now - self._last_change > self.STALE_AFTER:
                raise SourceClosed("telemetry stopped")
            return None
        self._last_packet = phy.packetId
        self._last_change = now
        return self._sample(now)

    def _sample(self, now: float) -> Sample:
        phy, gfx = self._phy, self._gfx
        v = phy.speedKmh / 3.6
        slips = [0.0] * 4
        if v > 3.0:
            for i in range(4):
                slips[i] = abs(phy.wheelAngularSpeed[i]) * self._radius[i] / v - 1.0
        return Sample(
            t=now, lap_time=gfx.iCurrentTime / 1000.0, laps=gfx.completedLaps,
            last_lap=max(gfx.iLastTime, 0) / 1000.0, pos=gfx.normalizedCarPosition,
            speed=phy.speedKmh, gas=phy.gas, brake=phy.brake, clutch=phy.clutch,
            gear=phy.gear - 1, rpm=float(phy.rpms), steer=phy.steerAngle,
            g_lat=phy.accG[0], g_lon=phy.accG[2],
            x=gfx.carCoordinates[0], z=gfx.carCoordinates[2],
            slip_fl=slips[0], slip_fr=slips[1], slip_rl=slips[2], slip_rr=slips[3],
            tyres_out=phy.numberOfTyresOut, in_pit=int(bool(gfx.isInPitLane or gfx.isInPit)),
            sector=gfx.currentSectorIndex,
        )
