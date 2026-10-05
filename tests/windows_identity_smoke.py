"""Read back real Shell properties from a temporary native Qt window."""
import ctypes
import sys
from unittest import mock

from PySide6.QtGui import QGuiApplication, QWindow
from repo_manager import desktop_identity as identity


def read_property(window_id, pid):
    store = ctypes.c_void_p()
    iid = identity._Guid.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
    getter = ctypes.windll.shell32.SHGetPropertyStoreForWindow
    getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(identity._Guid), ctypes.POINTER(ctypes.c_void_p)]
    getter.restype = ctypes.c_long
    assert getter(window_id, ctypes.byref(iid), ctypes.byref(store)) >= 0
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
        ctypes.POINTER(identity._PropertyKey), ctypes.POINTER(identity._PropertyValue))(table[5])
    key = identity._PropertyKey(identity._Guid.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), pid)
    value = identity._PropertyValue()
    try:
        assert get_value(store, ctypes.byref(key), ctypes.byref(value)) >= 0
        return ctypes.wstring_at(value.pointer) if value.vt == 31 else None
    finally:
        ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))
        ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(table[2])(store)


def main():
    assert identity.set_windows_app_user_model_id(identity.WINDOWS_APP_USER_MODEL_ID)
    app = QGuiApplication([])
    window = QWindow()
    window.create()
    try:
        with mock.patch.object(sys, "frozen", True, create=True):
            assert identity.set_windows_window_identity(int(window.winId()))
        assert read_property(int(window.winId()), 5) == identity.WINDOWS_APP_USER_MODEL_ID
        assert read_property(int(window.winId()), 3) == sys.executable + ",0"
        assert read_property(int(window.winId()), 4) == "RepoManager"
        assert read_property(int(window.winId()), 2) == '"' + sys.executable + '"'
        print("WINDOWS IDENTITY properties=4 native_readback=PASS")
    finally:
        window.destroy()


if __name__ == "__main__":
    main()
