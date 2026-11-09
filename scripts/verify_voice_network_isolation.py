"""Windows native-network-denied Kokoro verification, without changing a firewall.

Copies cached runtime/models into a fresh test directory, launches the actual
worker in an AppContainer with ZERO capabilities, and checks native Winsock denial.
No production files/ACLs change; no model download is performed.
"""

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(output, python, models, resume=False):
    if sys.platform != "win32":
        raise ValueError(
            "This verifier uses Windows AppContainer. On Linux run the worker in a network namespace; do not claim this Windows test verifies another OS."
        )
    from video_agent.voice_setup import validate_voice_models
    from video_agent.cache import file_hash

    output = output.resolve()
    if output.exists() and (not resume or (output / "report.json").exists()):
        raise ValueError(
            "Use a fresh output, or --resume-private-copy only for an unfinished verifier directory."
        )
    output.mkdir(parents=True, exist_ok=True)
    validate_voice_models(models)
    if not resume:
        base = Path(
            subprocess.check_output(
                [str(python), "-c", "import sys; print(sys.base_prefix)"], text=True
            ).strip()
        )
        print(
            "Copying a private verification runtime; production ACLs stay unchanged.",
            flush=True,
        )
        shutil.copytree(
            base,
            output / "python",
            ignore=shutil.ignore_patterns(
                "__pycache__", "site-packages", "include", "libs"
            ),
        )
        shutil.copytree(
            python.parent.parent / "Lib/site-packages",
            output / "site-packages",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for name in ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]:
            shutil.copy2(models / name, output / name)
    shutil.copy2(ROOT / "scripts/tts_worker.py", output / "tts_worker.py")
    (output / "tmp").mkdir(exist_ok=True)
    validate_voice_models(output)
    wrapper = r"""
import ctypes as c, json, pathlib, runpy, socket, struct, sys, tempfile
from ctypes import wintypes as w
root=pathlib.Path(__file__).parent
sys.path.insert(0,str(root/'site-packages'))
tempfile.tempdir=str(root/'tmp')
# Python's special Windows mkdir(0700) ACL omits AppContainer SIDs. In this
# private verification tree inherit the already restricted capability ACL.
import os
original_mkdir=os.mkdir
def private_mkdir(path,mode=0o777,*,dir_fd=None):
    if mode==0o700 and (root/'tmp') in pathlib.Path(path).resolve().parents:
        mode=0o777
    return original_mkdir(path,mode,dir_fd=dir_fd)
os.mkdir=private_mkdir
adv=c.WinDLL('advapi32',use_last_error=True); kernel=c.WinDLL('kernel32',use_last_error=True)
kernel.GetCurrentProcess.restype=w.HANDLE
adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
adv.GetTokenInformation.argtypes=[w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
token=w.HANDLE(); adv.OpenProcessToken(kernel.GetCurrentProcess(),8,c.byref(token))
flag=w.DWORD(); size=w.DWORD()
if not adv.GetTokenInformation(token,29,c.byref(flag),c.sizeof(flag),c.byref(size)) or not flag.value:
    raise RuntimeError('Worker did not enter AppContainer')
# socket import initializes Winsock. Probe the native DLL, not Python's socket wrapper.
ws=c.WinDLL('ws2_32'); ws.socket.restype=c.c_size_t
handle=ws.socket(2,1,6)
ws.connect.argtypes=[c.c_size_t,c.c_void_p,c.c_int]
address=c.create_string_buffer(struct.pack('H',2)+struct.pack('!H',9)+socket.inet_aton('192.0.2.1')+b'\0'*8)
result=ws.connect(handle,address,16); error=ws.WSAGetLastError()
ws.closesocket.argtypes=[c.c_size_t];ws.closesocket(handle)
proof=dict(appcontainer=True,capabilities=0,native_connect_result=result,native_connect_error=error)
(root/'network-proof.json').write_text(json.dumps(proof))
if result != -1 or error != 10013:
    raise RuntimeError('Native connection was not denied by network capability policy: '+str(proof))
sys.argv=[str(root/'tts_worker.py'),str(root)]
runpy.run_path(str(root/'tts_worker.py'),run_name='__main__')
"""
    # Keep a real NUL byte out of the generated script; the sockaddr padding must
    # be eight zero bytes rather than the two-character backslash/zero sequence.
    wrapper = wrapper.replace("b'\\\\0'", "b'\\0'")
    (output / "launch.py").write_text(wrapper)
    (output / "stdin.jsonl").write_text(
        json.dumps(
            dict(
                text="Cached local speech works without network access.",
                voice="af_heart",
                output=str(output / "speech.npy"),
            )
        )
        + '\n{"operation":"close"}\n'
    )

    class SA(c.Structure):
        _fields_ = [
            ("nLength", w.DWORD),
            ("lpSecurityDescriptor", c.c_void_p),
            ("bInheritHandle", w.BOOL),
        ]

    class SI(c.Structure):
        _fields_ = [
            ("cb", w.DWORD),
            ("lpReserved", w.LPWSTR),
            ("lpDesktop", w.LPWSTR),
            ("lpTitle", w.LPWSTR),
            ("dwX", w.DWORD),
            ("dwY", w.DWORD),
            ("dwXSize", w.DWORD),
            ("dwYSize", w.DWORD),
            ("dwXCountChars", w.DWORD),
            ("dwYCountChars", w.DWORD),
            ("dwFillAttribute", w.DWORD),
            ("dwFlags", w.DWORD),
            ("wShowWindow", w.WORD),
            ("cbReserved2", w.WORD),
            ("lpReserved2", c.c_void_p),
            ("hStdInput", w.HANDLE),
            ("hStdOutput", w.HANDLE),
            ("hStdError", w.HANDLE),
        ]

    class SX(c.Structure):
        _fields_ = [("StartupInfo", SI), ("lpAttributeList", c.c_void_p)]

    class PI(c.Structure):
        _fields_ = [
            ("hProcess", w.HANDLE),
            ("hThread", w.HANDLE),
            ("dwProcessId", w.DWORD),
            ("dwThreadId", w.DWORD),
        ]

    class CAPS(c.Structure):
        _fields_ = [
            ("AppContainerSid", c.c_void_p),
            ("Capabilities", c.c_void_p),
            ("CapabilityCount", w.DWORD),
            ("Reserved", w.DWORD),
        ]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    envdll = c.WinDLL("userenv", use_last_error=True)
    adv = c.WinDLL("advapi32", use_last_error=True)
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        c.POINTER(SA),
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    envdll.CreateAppContainerProfile.argtypes = [
        w.LPCWSTR,
        w.LPCWSTR,
        w.LPCWSTR,
        c.c_void_p,
        w.DWORD,
        c.POINTER(c.c_void_p),
    ]
    envdll.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    kernel.InitializeProcThreadAttributeList.argtypes = [
        c.c_void_p,
        w.DWORD,
        w.DWORD,
        c.POINTER(c.c_size_t),
    ]
    kernel.UpdateProcThreadAttribute.argtypes = [
        c.c_void_p,
        w.DWORD,
        c.c_size_t,
        c.c_void_p,
        c.c_size_t,
        c.c_void_p,
        c.c_void_p,
    ]
    kernel.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
    kernel.CreateProcessW.argtypes = [
        w.LPCWSTR,
        w.LPWSTR,
        c.c_void_p,
        c.c_void_p,
        w.BOOL,
        w.DWORD,
        c.c_void_p,
        w.LPCWSTR,
        c.POINTER(SX),
        c.POINTER(PI),
    ]
    name = "CFC.OfflineVoice." + uuid.uuid4().hex
    sid = c.c_void_p()
    sidtext = w.LPWSTR()
    hr = envdll.CreateAppContainerProfile(
        name, name, "CFC offline speech verification", None, 0, c.byref(sid)
    )
    if hr:
        raise RuntimeError(
            f"AppContainer creation unavailable: HRESULT {hr & 0xFFFFFFFF:08x}"
        )
    handles = []
    attributes = None
    try:
        if not adv.ConvertSidToStringSidW(sid, c.byref(sidtext)):
            raise c.WinError(c.get_last_error())
        # Only the newly created, explicitly named verification directory is changed.
        for args in [
            ["/grant", "*" + sidtext.value + ":(OI)(CI)F", "/T", "/Q"],
            ["/setintegritylevel", "(OI)(CI)L", "/T", "/Q"],
        ]:
            subprocess.run(
                ["icacls", str(output), *args],
                check=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        sa = SA(c.sizeof(SA), None, True)
        for path, access, mode in [
            (output / "stdin.jsonl", 0x80000000, 3),
            (output / "stdout.log", 0x40000000, 2),
        ]:
            h = kernel.CreateFileW(str(path), access, 3, c.byref(sa), mode, 0x80, None)
            if h == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            handles.append(h)
        size = c.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(
            attributes, 1, 0, c.byref(size)
        ):
            raise c.WinError(c.get_last_error())
        caps = CAPS(sid, None, 0, 0)
        if not kernel.UpdateProcThreadAttribute(
            attributes, 0, 0x20009, c.byref(caps), c.sizeof(caps), None, None
        ):
            raise c.WinError(c.get_last_error())
        startup = SX()
        startup.StartupInfo.cb = c.sizeof(SX)
        startup.StartupInfo.dwFlags = 0x100
        startup.StartupInfo.hStdInput = handles[0]
        startup.StartupInfo.hStdOutput = handles[1]
        startup.StartupInfo.hStdError = handles[1]
        startup.lpAttributeList = c.cast(attributes, c.c_void_p)
        executable = output / "python/python.exe"
        command = c.create_unicode_buffer(
            subprocess.list2cmdline(
                [str(executable), "-I", "-S", str(output / "launch.py")]
            )
        )
        variables = dict(
            os.environ,
            ORT_DISABLE_TELEMETRY="1",
            TEMP=str(output / "tmp"),
            TMP=str(output / "tmp"),
        )
        environment = c.create_unicode_buffer(
            "\0".join(f"{k}={v}" for k, v in sorted(variables.items())) + "\0\0"
        )
        process = PI()
        if not kernel.CreateProcessW(
            str(executable),
            command,
            None,
            None,
            True,
            0x80000 | 0x400 | 0x08000000,
            environment,
            str(output),
            c.byref(startup),
            c.byref(process),
        ):
            raise c.WinError(c.get_last_error())
        handles += [process.hProcess, process.hThread]
        if kernel.WaitForSingleObject(process.hProcess, 180000) != 0:
            raise RuntimeError(
                "Isolated voice probe timed out; inspect its saved PID/log before any retry"
            )
        code = w.DWORD()
        kernel.GetExitCodeProcess(process.hProcess, c.byref(code))
        lines = []
        for line in (output / "stdout.log").read_text(errors="replace").splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                pass
        if code.value or not (output / "network-proof.json").exists():
            raise RuntimeError(
                f"Isolated worker exited {code.value}: "
                + (output / "stdout.log").read_text(errors="replace")[-1200:]
            )
        proof = json.loads((output / "network-proof.json").read_text())
        import numpy as np

        if len(lines) != 2 or not all(line.get("ok") for line in lines):
            raise RuntimeError("Isolated voice error: " + str(lines))
        samples = np.load(output / "speech.npy", allow_pickle=False)
        assert (
            code.value == 0
            and len(lines) == 2
            and all(line.get("ok") for line in lines)
        ), lines
        assert len(samples) > 2400 and np.isfinite(samples).all()
        report = dict(
            status="passed",
            evidence="Actual cached Kokoro worker under Windows AppContainer, zero network capabilities; native Winsock connect denied with WSAEACCES 10013",
            network=proof,
            worker=lines[0],
            rate=lines[1]["rate"],
            audio_seconds=len(samples) / lines[1]["rate"],
            audio_rms=float(np.sqrt(np.mean(samples**2))),
            worker_sha256=file_hash(ROOT / "scripts/tts_worker.py"),
            no_model_downloads=True,
            pid=process.dwProcessId,
        )
        (output / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "worker"}, indent=2))
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)
        if attributes is not None:
            kernel.DeleteProcThreadAttributeList(attributes)
        envdll.DeleteAppContainerProfile(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--resume-private-copy", action="store_true")
    args = parser.parse_args()
    from video_agent.voice_setup import tts_python
    from video_agent.config import Settings

    try:
        verify(
            args.output,
            args.python or tts_python(),
            Settings().model_dir,
            args.resume_private_copy,
        )
    except Exception as error:
        print(
            f"Network-isolated verification BLOCKED/FAILED: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    main()
