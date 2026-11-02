"""Native Windows display/suspend notifications; never changes power settings."""
import ctypes
from ctypes import wintypes
import logging
import uuid

logger = logging.getLogger(__name__)
CALLBACK = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)
DISPLAY_STATE = (ctypes.c_ubyte * 16).from_buffer_copy(
    uuid.UUID('6fe69556-704a-47a0-8f24-c28d936fda47').bytes_le)


class Subscription(ctypes.Structure):
    _fields_ = [('callback', CALLBACK), ('context', ctypes.c_void_p)]


class DisplaySetting(ctypes.Structure):
    _fields_ = [('guid', ctypes.c_ubyte * 16), ('length', wintypes.DWORD), ('value', wintypes.DWORD)]


class PowerMonitor:
    def __init__(self, on_change):
        self.on_change = on_change
        self._display_on = self._awake = True
        self._callback = CALLBACK(self._notify)
        self._parameters = Subscription(self._callback, None)
        self._display = ctypes.c_void_p()
        self._suspend = ctypes.c_void_p()
        self._api = ctypes.windll.powrprof
        self._api.PowerSettingRegisterNotification.argtypes = (
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        self._api.PowerSettingUnregisterNotification.argtypes = (ctypes.c_void_p,)
        self._api.PowerRegisterSuspendResumeNotification.argtypes = (
            wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        self._api.PowerUnregisterSuspendResumeNotification.argtypes = (ctypes.c_void_p,)

    def _notify(self, context, kind, setting):
        try:
            if kind == 0x8013 and setting:  # PBT_POWERSETTINGCHANGE
                data = ctypes.cast(setting, ctypes.POINTER(DisplaySetting)).contents
                if bytes(data.guid) != bytes(DISPLAY_STATE) or data.length != 4:
                    return 0
                self._display_on = data.value != 0  # Dimmed (2) remains usable.
            elif kind == 4:  # PBT_APMSUSPEND
                self._awake = False
            elif kind in (7, 18):  # Resume suspend / resume automatic
                self._awake = True
            else:
                return 0
            self.on_change(self._display_on and self._awake)
        except Exception:
            logger.exception('power notification')
        return 0

    def start(self):
        status = self._api.PowerSettingRegisterNotification(
            ctypes.byref(DISPLAY_STATE), 2, ctypes.byref(self._parameters), ctypes.byref(self._display))
        if not status:
            status = self._api.PowerRegisterSuspendResumeNotification(
                2, ctypes.byref(self._parameters), ctypes.byref(self._suspend))
        if status:
            self.stop()
            raise OSError(f'Windows power notification registration failed ({status})')

    def stop(self):
        for handle, unregister in (
            (self._display, self._api.PowerSettingUnregisterNotification),
            (self._suspend, self._api.PowerUnregisterSuspendResumeNotification),
        ):
            if handle.value:
                status = unregister(handle)
                if status:
                    logger.error('Windows power notification unregister failed (%s)', status)
                else:
                    handle.value = None
