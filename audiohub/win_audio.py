"""Windows per-app audio: list app audio sessions, read levels, set volume/mute.

Must be imported from the worker thread: comtypes initialises COM on import,
and we want a multithreaded apartment there (the UI thread needs its own STA).
"""
import os
import sys
import time

sys.coinit_flags = 0  # COINIT_MULTITHREADED, read by comtypes on import

from pycaw.constants import DEVICE_STATE, EDataFlow, ERole  # noqa: E402
from pycaw.pycaw import AudioUtilities, IAudioMeterInformation  # noqa: E402

from . import app_names  # noqa: E402
from .app_names import tidy  # noqa: E402

EXPIRED = 2

NICE_NAMES = {
    "msedge": "Edge",
    "chrome": "Chrome",
    "firefox": "Firefox",
    "brave": "Brave",
    "vlc": "VLC",
    "mpc-hc64": "MPC-HC",
    "potplayermini64": "PotPlayer",
    "spotify": "Spotify",
    "discord": "Discord",
    "steamwebhelper": "Steam",
}


def app_key(exe_name: str) -> str:
    return os.path.splitext(exe_name)[0].lower()


def nice_name(key: str) -> str:
    """Short names for the browsers and players people know by them (Chrome, not
    'Google Chrome'); everything else is named from Windows, see app_names."""
    return NICE_NAMES.get(key) or tidy(key)


class WinAudio:
    def __init__(self):
        self._sessions = {}  # key -> [(session, meter)]
        self._names = {}
        self.exes = {}  # key -> exe path, for the row icon
        self._pending = {}  # key -> {"volume": float, "muted": bool} for apps with no stream yet
        self._device = None
        self._endpoint = None
        self._device_at = 0.0
        self._outputs = []  # [{"id", "name"}] of active output devices, refreshed with the default

    # ---- sessions -------------------------------------------------------
    def refresh_sessions(self):
        groups = {}
        for s in AudioUtilities.GetAllSessions():
            try:
                if not s.ProcessId or s.State == EXPIRED or s.Process is None:
                    continue
                key = app_key(s.Process.name())
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
            except Exception:
                continue  # process exited between listing and reading
            groups.setdefault(key, []).append((s, meter))
            if key not in self.exes:
                try:
                    self.exes[key] = s.Process.exe()
                except Exception:
                    self.exes[key] = None
            if key not in self._names:
                dn = s.DisplayName or ""
                if dn and not dn.startswith("@"):
                    self._names[key] = dn
                else:
                    self._names[key] = app_names.for_exe(key, self.exes.get(key),
                                                         NICE_NAMES.get(key))
        self._sessions = groups
        for key in list(self._pending):
            if key in groups:
                p = self._pending.pop(key)
                if "volume" in p:
                    self.set_volume(key, p["volume"])
                if "muted" in p:
                    self.set_mute(key, p["muted"])

    def read(self):
        """key -> {name, peak, volume, muted} for apps that currently have a stream."""
        out = {}
        for key, lst in self._sessions.items():
            peak, vols, muted = 0.0, [], True
            for s, meter in lst:
                try:
                    peak = max(peak, meter.GetPeakValue())
                    sav = s.SimpleAudioVolume
                    vols.append(sav.GetMasterVolume())
                    muted = muted and bool(sav.GetMute())
                except Exception:
                    pass
            if vols:
                out[key] = {"name": self._names.get(key, nice_name(key)),
                            "peak": peak, "volume": max(vols), "muted": muted}
        return out

    def set_volume(self, key, value):
        value = min(1.0, max(0.0, float(value)))
        lst = self._sessions.get(key)
        if not lst:
            self._pending.setdefault(key, {})["volume"] = value
            return
        for s, _ in lst:
            try:
                s.SimpleAudioVolume.SetMasterVolume(value, None)
            except Exception:
                pass

    def set_mute(self, key, muted):
        lst = self._sessions.get(key)
        if not lst:
            self._pending.setdefault(key, {})["muted"] = bool(muted)
            return
        for s, _ in lst:
            try:
                s.SimpleAudioVolume.SetMute(1 if muted else 0, None)
            except Exception:
                pass

    # ---- output device --------------------------------------------------
    def _ensure_device(self):
        now = time.monotonic()
        if self._endpoint is None or now - self._device_at > 3:  # pick up default-device changes
            self._device = AudioUtilities.GetSpeakers()
            self._endpoint = self._device.EndpointVolume
            self._device_at = now
            try:
                self._outputs = [{"id": d.id, "name": d.FriendlyName}
                                 for d in AudioUtilities.GetAllDevices(EDataFlow.eRender.value,
                                                                       DEVICE_STATE.ACTIVE.value)
                                 if d.FriendlyName]
            except Exception:
                self._outputs = []

    def master(self):
        try:
            self._ensure_device()
            ev = self._endpoint
            return {"device": self._device.FriendlyName,
                    "deviceId": self._device.id,
                    "outputs": self._outputs,
                    "volume": ev.GetMasterVolumeLevelScalar(),
                    "muted": bool(ev.GetMute())}
        except Exception:
            self._endpoint = None
            return {"device": "No output device", "deviceId": None, "outputs": [],
                    "volume": 0.0, "muted": True}

    def set_output(self, device_id):
        """Make this the default output, as picking it in Windows' sound settings does
        (for apps, media and calls). Windows has no public API for this; pycaw uses the
        undocumented IPolicyConfig that every audio switcher relies on."""
        if device_id not in {d["id"] for d in self._outputs}:
            raise ValueError(f"unknown output device {device_id!r}")
        AudioUtilities.SetDefaultDevice(
            device_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
        self._endpoint = None  # re-read at once: the master slider now drives the new device

    def set_master_volume(self, value):
        self._ensure_device()
        self._endpoint.SetMasterVolumeLevelScalar(min(1.0, max(0.0, float(value))), None)

    def set_master_mute(self, muted):
        self._ensure_device()
        self._endpoint.SetMute(1 if muted else 0, None)
