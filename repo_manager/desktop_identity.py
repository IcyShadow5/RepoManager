"""Stable Windows taskbar identity shared by desktop presentations."""
import ctypes
import logging
import os
import sys
from pathlib import Path

WINDOWS_APP_USER_MODEL_ID = "RepoManager.RepoManager"
WINDOWS_DEVELOPMENT_APP_ID = "RepoManager.RepoManager.Development"


def set_windows_app_user_model_id(app_id=WINDOWS_APP_USER_MODEL_ID):
    if os.name != "nt":
        return False
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(app_id)
        if result != 0:
            raise OSError(f"Windows application identity failed: HRESULT {result:#x}")
        return True
    except (OSError, AttributeError):
        logging.getLogger("repomanager").exception("could not set Windows application identity")
        return False


class _Guid(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        import uuid
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", ctypes.c_uint32)]


class _PropertyValue(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3),
                ("pointer", ctypes.c_void_p), ("padding", ctypes.c_void_p)]


def set_windows_window_identity(window_id):
    """Give Shell an explicit icon source for an unregistered portable app.

    Process AppID and a Qt window icon alone leave Shell's relaunch identity
    dependent on finding a matching installed shortcut. A portable build has
    no such shortcut; its own EXE contains the canonical icon resource.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    store = ctypes.c_void_p()
    initialized = False
    try:
        ole = ctypes.windll.ole32
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        ole.CoInitializeEx.restype = ctypes.c_long
        result = ole.CoInitializeEx(None, 2)
        initialized = result >= 0
        # RPC_E_CHANGED_MODE means Qt already owns a different COM apartment.
        if result < 0 and result != -2147417850:
            raise OSError(f"COM initialization failed: HRESULT {result:#x}")
        iid = _Guid.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
        getter = ctypes.windll.shell32.SHGetPropertyStoreForWindow
        getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
        getter.restype = ctypes.c_long
        result = getter(window_id, ctypes.byref(iid), ctypes.byref(store))
        if result < 0:
            raise OSError(f"Window property store failed: HRESULT {result:#x}")
        table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        setter = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
            ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropertyValue))(table[6])
        fmtid = _Guid.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3")
        executable = str(Path(sys.executable).resolve())
        # Set relaunch properties before AppID, as required by Windows Shell.
        properties = ((2, f'"{executable}"'), (3, executable + ",0"),
                      (4, "RepoManager"), (5, WINDOWS_APP_USER_MODEL_ID))
        for pid, text in properties:
            buffer = ctypes.create_unicode_buffer(text)
            value = _PropertyValue(31, (ctypes.c_ushort * 3)(),
                                   ctypes.cast(buffer, ctypes.c_void_p), None)
            key = _PropertyKey(fmtid, pid)
            result = setter(store, ctypes.byref(key), ctypes.byref(value))
            if result < 0:
                raise OSError(f"Window identity property {pid} failed: HRESULT {result:#x}")
        return True
    except (OSError, AttributeError) as exc:
        logging.getLogger("repomanager").error("could not set Windows window identity: %s", exc)
        return False
    finally:
        if store.value:
            table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(table[2])(store)
        if initialized:
            ctypes.windll.ole32.CoUninitialize()
