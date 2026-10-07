"""Windows Agent process trees, assigned to a job before execution begins."""
import ctypes
from ctypes import wintypes
import subprocess
import threading
import time


class BasicLimits(ctypes.Structure):
    _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSet", ctypes.c_size_t),
                ("MaximumWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperations", "WriteOperations", "OtherOperations",
        "ReadBytes", "WriteBytes", "OtherBytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("Basic", BasicLimits), ("Io", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("PeriodUserTime", ctypes.c_int64), ("PeriodKernelTime", ctypes.c_int64),
                ("PageFaults", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TerminatedProcesses", wintypes.DWORD)]


def native_api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = arguments
        function.restype = result
    return api


class WindowsProcess:
    """Popen-like observation; terminal means the entire contained job is empty."""
    def __init__(self, executable, command_line, *, env, cwd, creationflags=0,
                 active_process_limit=0, std_handles=None):
        import _winapi

        self._api = native_api()
        self._lock = threading.RLock()
        self._process = None
        self._job = None
        self._children = {}
        self.returncode = None
        self.args = command_line
        self.pid = None
        self.process_limit_exceeded = False
        self._active_process_limit = active_process_limit
        thread = None
        try:
            self._job = self._api.CreateJobObjectW(None, None)
            if not self._job:
                self._job = None
                raise ctypes.WinError(ctypes.get_last_error())
            limits = ExtendedLimits()
            limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if active_process_limit:
                limits.Basic.LimitFlags |= 0x8  # JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                limits.Basic.ActiveProcessLimit = active_process_limit
            if not self._api.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            # CPython's Windows creation boundary retains both handles. Popen
            # closes the primary thread handle, which would prevent ResumeThread.
            startup = subprocess.STARTUPINFO()
            if std_handles is not None:
                startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = std_handles
                startup.lpAttributeList = {"handle_list": list(std_handles)}
                creationflags |= 0x80000  # EXTENDED_STARTUPINFO_PRESENT
            self._process, thread, self.pid, _ = _winapi.CreateProcess(
                executable, command_line, None, None, std_handles is not None,
                creationflags | 0x4, env, cwd, startup)
            if not self._api.AssignProcessToJobObject(self._job, self._process):
                raise ctypes.WinError(ctypes.get_last_error())
            if self._api.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            if self._process is not None:
                _winapi.TerminateProcess(self._process, 1)
                _winapi.WaitForSingleObject(self._process, 5000)
            self.close()
            raise
        finally:
            if thread is not None:
                _winapi.CloseHandle(thread)

    def poll(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            if self._job is None or self._process is None:
                raise OSError("Agent process-tree observation is unavailable")
            accounting = Accounting()
            if not self._api.QueryInformationJobObject(self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if self._active_process_limit and accounting.TotalProcesses > self._active_process_limit:
                self.process_limit_exceeded = True
            if accounting.ActiveProcesses:
                self._observe_children(max(accounting.ActiveProcesses, 16))
                return None
            # Job accounting can reach zero before process cleanup releases its
            # files. Keep observed descendants' handles until they are signaled.
            for handle in (self._process, *self._children.values()):
                state = self._api.WaitForSingleObject(handle, 0)
                if state == 258:
                    return None
                if state != 0:
                    raise ctypes.WinError(ctypes.get_last_error())
            code = wintypes.DWORD()
            if not self._api.GetExitCodeProcess(self._process, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.returncode = code.value
            self.close()
            return self.returncode

    def _observe_children(self, capacity):
        while True:
            class ProcessIds(ctypes.Structure):
                _fields_ = [("Assigned", wintypes.DWORD), ("Count", wintypes.DWORD),
                            ("Ids", ctypes.c_size_t * capacity)]
            listing = ProcessIds()
            if self._api.QueryInformationJobObject(self._job, 3, ctypes.byref(listing), ctypes.sizeof(listing), None):
                break
            error = ctypes.get_last_error()
            if error != 234 or capacity >= 1048576:
                raise ctypes.WinError(error)
            capacity *= 2
        for pid in listing.Ids[:listing.Count]:
            if pid == self.pid or pid in self._children:
                continue
            handle = self._api.OpenProcess(0x101000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 87:  # The process ended before its handle was opened.
                    continue
                raise ctypes.WinError(error)
            belongs = wintypes.BOOL()
            if not self._api.IsProcessInJob(handle, self._job, ctypes.byref(belongs)):
                error = ctypes.get_last_error()
                self._api.CloseHandle(handle)
                raise ctypes.WinError(error)
            if belongs.value:
                self._children[pid] = handle
            else:
                self._api.CloseHandle(handle)

    def terminate(self):
        with self._lock:
            if self.poll() is None and not self._api.TerminateJobObject(self._job, 1):
                raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def wait(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(0.01)

    def close(self):
        with self._lock:
            for pid, handle in list(self._children.items()):
                if not self._api.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                del self._children[pid]
            for field in ("_job", "_process"):
                handle = getattr(self, field)
                if handle is not None:
                    if not self._api.CloseHandle(handle):
                        raise ctypes.WinError(ctypes.get_last_error())
                    setattr(self, field, None)

    def __del__(self):
        try:
            self.close()
        except (OSError, AttributeError):
            # Native handles may already be gone during interpreter teardown.
            pass
