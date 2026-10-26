"""Bounded argv-only subprocesses, POSIX groups and Windows kill-on-close jobs."""
from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
from dataclasses import dataclass

from .models import OrchError


def safe_env(auth=False):
    keep = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "LANG", "LC_ALL", "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    if auth and "CODEX_HOME" in os.environ:
        env["CODEX_HOME"] = os.environ["CODEX_HOME"]
    return env


class WindowsJob:
    def __init__(self, pid):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.k.CreateJobObjectW.restype = w.HANDLE
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.k.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = self.k.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            if not self.handle or not process or not self.k.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not self.k.AssignProcessToJobObject(self.handle, process):
                raise OrchError(f"Windows Job Object assignment failed: {ctypes.get_last_error()}")
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                self.k.CloseHandle(process)

    def close(self):
        if self.handle:
            self.k.TerminateJobObject(self.handle, 1)
            self.k.CloseHandle(self.handle)
            self.handle = None


@dataclass
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    stopped: bool = True


async def run_process(argv, cwd, *, stdin="", timeout=60, cancelled=lambda: False, auth=False, on_start=None, output_limit=2_000_000):
    if not argv or any(not isinstance(x, str) for x in argv) or argv[0].lower().endswith((".cmd", ".bat")):
        raise OrchError("Executable argv required; shell shims rejected")
    opts = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=safe_env(auth), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **opts)
    job = None
    async def read(stream):
        output = bytearray()
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return bytes(output).decode("utf-8", errors="replace")
            output.extend(chunk)
            if len(output) > output_limit:
                raise OrchError("Subprocess output limit exceeded")
    readers = []
    try:
        if os.name == "nt":
            job = WindowsJob(proc.pid)
        if on_start:
            on_start(proc.pid)
        readers = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr))]
        proc.stdin.write(stdin.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        start = asyncio.get_running_loop().time()
        while proc.returncode is None:
            if cancelled():
                raise asyncio.CancelledError("Cancellation requested")
            if asyncio.get_running_loop().time() - start > timeout:
                raise TimeoutError("Process deadline exceeded")
            for r in readers:
                if r.done() and r.exception():
                    raise r.exception()
            await asyncio.sleep(0.05)
        stdout, stderr = await asyncio.wait_for(asyncio.gather(*readers), 2)
        return ProcessResult(proc.returncode, stdout, stderr)
    finally:
        if job:
            job.close()
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.returncode is None:
            proc.kill()
        await proc.wait()
        for r in readers:
            if not r.done():
                r.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
