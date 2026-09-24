import os
import re
import sys
import time
import random
import ctypes
import platform
import subprocess
import shutil
import tempfile
import winreg
import datetime
from ctypes import wintypes
from pathlib import Path

from curl_cffi import requests
import py7zr

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
RELEASE = "16.2.0-rt_v14-rev1"
RT_PART = "rt_v14-rev1"

ARCHIVES = {
    "x86_64": f"x86_64-16.2.0-release-posix-seh-ucrt-{RT_PART}.7z",
    "i686":   f"i686-16.2.0-release-posix-dwarf-ucrt-{RT_PART}.7z",
}

GITHUB_BASE = f"https://github.com/niXman/mingw-builds-binaries/releases/download/{RELEASE}"

SOURCES = [
    ("GitHub direct", GITHUB_BASE),
    ("ghfast.top",    f"https://ghfast.top/{GITHUB_BASE}"),
    ("ghproxy.net",   f"https://ghproxy.net/{GITHUB_BASE}"),
    ("SDU mirror",    "https://mirrors.sdu.edu.cn/github-release/niXman_mingw-builds-binaries/16.2.0-rt_v14-rev1"),
]

INSTALL_ROOT = Path("C:/mingw64")
BIN_DIR = INSTALL_ROOT / "bin"
TEMP_DIR = Path(tempfile.gettempdir())
ARCHIVE_PATH = None

REQUIRED_BINARIES = ["gcc.exe", "g++.exe", "gdb.exe", "ld.exe", "ar.exe", "windres.exe"]
CORE_HEADERS = ["stdio.h", "stdlib.h", "iostream", "vector"]

IMPERSONATE_POOL = ["chrome", "chrome136", "chrome131", "edge101", "safari18_0", "firefox133"]
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]


# ---------------------------------------------------------------
# Logging
# ---------------------------------------------------------------
def header(msg: str) -> None:
    print()
    print("=" * 60)
    print(f"  {msg}")
    print("=" * 60)


def step(msg: str) -> None:
    print(f"[*] {msg}")


def ok(msg: str) -> None:
    print(f"[+] {msg}")


def warn(msg: str) -> None:
    print(f"[~] {msg}")


def fail(msg: str) -> None:
    print(f"[!] {msg}")


# ---------------------------------------------------------------
# Admin elevation
# ---------------------------------------------------------------
class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_long_paths() -> None:
    """Enable NTFS long path support (requires admin)."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
            0, winreg.KEY_READ | winreg.KEY_WRITE,
        )
        current, _ = winreg.QueryValueEx(key, "LongPathsEnabled") if True else (0, None)
        if current == 1:
            ok("Long path support already enabled.")
        else:
            winreg.SetValueEx(key, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
            ok("Long path support enabled (reboot recommended for full effect).")
        winreg.CloseKey(key)
    except FileNotFoundError:
        try:
            key = winreg.CreateKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\FileSystem",
            )
            winreg.SetValueEx(key, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
            winreg.CloseKey(key)
            ok("Long path support enabled.")
        except Exception as exc:
            warn(f"Long paths setup skipped: {exc}")
    except PermissionError:
        warn("Cannot enable long paths (need admin). Continuing.")
    except Exception as exc:
        warn(f"Long paths setup skipped: {exc}")


def relaunch_as_admin() -> None:
    """Relaunch this script elevated, wait for it, exit with its code."""
    header("ADMIN REQUIRED")
    print("[*] Requesting elevation via UAC prompt ...")

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NO_CONSOLE = 0x00008000
    SW_NORMAL = 1

    script = str(Path(__file__).resolve())
    exe = sys.executable
    params = " ".join(f'"{a}"' for a in sys.argv[1:])

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpParameters = f'"{script}" {params}'.strip()
    sei.lpDirectory = str(Path(__file__).parent)
    sei.nShow = SW_NORMAL

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        fail(f"Elevation failed (error {err}).")
        print("    UAC was declined, or shell32 rejected the request.")
        sys.exit(1)

    if not sei.hProcess:
        fail("Elevated process handle missing.")
        sys.exit(1)

    print("[*] Elevated process running. Waiting for completion ...")
    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, -1)

    exit_code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)

    print(f"[*] Elevated process finished with code {exit_code.value}.")
    sys.exit(exit_code.value)


def ensure_admin() -> None:
    """If not admin, relaunch elevated. Never returns in the parent process."""
    if is_admin():
        ok("Running with administrator privileges.")
        return
    warn("Not running as admin — relaunching with elevation.")
    relaunch_as_admin()


# ---------------------------------------------------------------
# System detection
# ---------------------------------------------------------------
def detect_architecture() -> str:
    if "PROCESSOR_ARCHITEW6432" in os.environ:
        return "x86_64"
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    if machine in ("x86", "i386", "i686"):
        if sys.maxsize > 2**32:
            return "x86_64"
        return "i686"
    return "i686"


def windows_version() -> str:
    try:
        return platform.platform()
    except Exception:
        return "Windows"


# ---------------------------------------------------------------
# Spoofing helpers
# ---------------------------------------------------------------
def _spoofed_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/octet-stream,text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://github.com/niXman/mingw-builds-binaries/releases",
        "Connection": "keep-alive",
        "DNT": str(random.choice([0, 1])),
    }


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------
# Download
# ---------------------------------------------------------------
def _attempt_download(url: str, dest: Path, max_attempts: int = 2) -> bool:
    for attempt in range(1, max_attempts + 1):
        impersonate = random.choice(IMPERSONATE_POOL)
        headers = _spoofed_headers()
        try:
            print(f"    [attempt {attempt}/{max_attempts}] impersonate={impersonate}")
            response = requests.get(
                url, stream=True, impersonate=impersonate,
                headers=headers, timeout=30, allow_redirects=True,
            )
            response.raise_for_status()

            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            last_print = 0.0
            start = time.time()

            with open(dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_print > 0.2 or downloaded == total:
                        last_print = now
                        speed = downloaded / max(now - start, 0.001)
                        if total > 0:
                            pct = (downloaded / total) * 100
                            bar_len = 30
                            filled = int(bar_len * downloaded / total)
                            bar = "#" * filled + "-" * (bar_len - filled)
                            print(
                                f"\r      [{bar}] {pct:5.1f}%  "
                                f"{_fmt_bytes(downloaded)} / {_fmt_bytes(total)}  "
                                f"@ {_fmt_bytes(int(speed))}/s",
                                end="", flush=True,
                            )
                        else:
                            print(f"\r      {_fmt_bytes(downloaded)}", end="", flush=True)

            response.close()
            print()
            if dest.exists() and dest.stat().st_size > 0:
                return True
            print("      empty file after download")
            return False
        except Exception as exc:
            print(f"\n      failed: {exc}")
            if attempt < max_attempts:
                backoff = 1.5 + random.random() * 2
                print(f"      backing off {backoff:.1f}s ...")
                time.sleep(backoff)
    return False


def download_file(filename: str, dest: Path) -> None:
    print(f"[*] Downloading {filename}")
    print(f"[*] Target: {dest}")
    print()

    tried = set()
    for label, base in SOURCES:
        url = f"{base}/{filename}"
        if url in tried:
            continue
        tried.add(url)
        print(f"[*] Source: {label}")
        print(f"    URL: {url}")
        if _attempt_download(url, dest):
            size = dest.stat().st_size
            ok(f"Downloaded via {label} ({_fmt_bytes(size)})")
            return
        print(f"    [-] {label} exhausted")
        time.sleep(1.0 + random.random())

    fail("All sources failed.")
    print(f"    Verify the release exists: https://github.com/niXman/mingw-builds-binaries/releases/tag/{RELEASE}")
    sys.exit(1)


# ---------------------------------------------------------------
# Extraction — robust with staging, attribute clearing, retries
# ---------------------------------------------------------------
def _clear_readonly(path: Path) -> None:
    """Strip read-only, hidden, and system attributes recursively."""
    try:
        subprocess.run(
            ["attrib", "-R", "-H", "-S", str(path), "/S", "/D"],
            capture_output=True, timeout=300, check=False,
        )
    except Exception:
        pass


def _rmtree_force(path: Path) -> None:
    """Remove a tree, clearing read-only attributes first, with retries."""
    if not path.exists():
        return
    _clear_readonly(path)
    for attempt in range(1, 6):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except PermissionError:
            time.sleep(0.5 * attempt)
            _clear_readonly(path)
        except Exception:
            time.sleep(0.5 * attempt)
    shutil.rmtree(path, ignore_errors=True)


def _move_with_retry(src: Path, dest: Path, attempts: int = 8) -> bool:
    """Move src → dest, retrying on Windows file-lock and permission errors."""
    for i in range(1, attempts + 1):
        try:
            if dest.exists():
                if dest.is_dir():
                    _rmtree_force(dest)
                else:
                    try:
                        dest.chmod(0o666)
                    except Exception:
                        pass
                    dest.unlink()
            shutil.move(str(src), str(dest))
            return True
        except PermissionError as exc:
            if i == attempts:
                print(f"      move failed after {attempts} attempts: {exc}")
                return False
            time.sleep(0.6 * i)
        except Exception as exc:
            if i == attempts:
                print(f"      move error: {exc}")
                return False
            time.sleep(0.6 * i)
    return False


def _flatten_tree(root: Path) -> None:
    """If root contains a single subdirectory, hoist its contents up one level."""
    entries = list(root.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        step(f"Flattening nested directory: {inner.name}")
        for item in inner.iterdir():
            dest = root / item.name
            if not _move_with_retry(item, dest):
                warn(f"Could not move {item.name} — leaving in place.")
        try:
            inner.rmdir()
        except OSError:
            pass


def extract_archive(archive_path: Path, target_dir: Path) -> None:
    """Extract to staging, clear attributes, then move to final target."""
    print(f"[*] Extracting to {target_dir} (via staging) ...")

    if target_dir.exists():
        step(f"Removing existing {target_dir} ...")
        _rmtree_force(target_dir)

    staging = Path(tempfile.mkdtemp(prefix="mingw_staging_"))
    print(f"[*] Staging directory: {staging}")

    # --- Extract to staging, retry on permission error ---
    try:
        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            archive.extractall(path=staging)
    except PermissionError as exc:
        warn(f"Extraction hit a permission error: {exc}")
        step("Clearing attributes and retrying ...")
        _clear_readonly(staging)
        time.sleep(1.0)
        try:
            with py7zr.SevenZipFile(archive_path, mode="r") as archive:
                archive.extractall(path=staging)
        except Exception as exc2:
            fail(f"Retry failed: {exc2}")
            _rmtree_force(staging)
            sys.exit(1)
    except Exception as exc:
        fail(f"Extraction failed: {exc}")
        _rmtree_force(staging)
        sys.exit(1)

    ok("Staged extraction complete.")

    # --- Flatten and clear attributes ---
    _flatten_tree(staging)
    step("Clearing read-only attributes on staged files ...")
    _clear_readonly(staging)

    # --- Move staging → target ---
    step(f"Moving staged files to {target_dir} ...")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if not _move_with_retry(staging, target_dir):
        fail("Could not move staging directory to target.")
        print(f"    Staging left at: {staging}")
        print("    You may need to reboot to release file locks, then move it manually.")
        sys.exit(1)

    ok("Extraction complete.")


# ---------------------------------------------------------------
# PATH
# ---------------------------------------------------------------
def _broadcast_environment_change() -> None:
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x1A, 0, "Environment", 0x0002, 5000, None,
        )
    except Exception:
        pass


def add_to_user_path(bin_path: Path) -> None:
    step(f"Adding {bin_path} to user PATH ...")
    path_str = str(bin_path)
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        )
    except FileNotFoundError:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment")
    try:
        current, _ = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        current = ""
    parts = [p.strip() for p in current.split(";") if p.strip()]
    if path_str in parts:
        ok("PATH already contains the MinGW bin directory.")
    else:
        parts.append(path_str)
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
        ok("User PATH updated in registry.")
    winreg.CloseKey(key)
    _broadcast_environment_change()


# ---------------------------------------------------------------
# Verification suite
# ---------------------------------------------------------------
class VerificationResult:
    def __init__(self, name: str, passed: bool, detail: str = "", fix: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.fix = fix

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        line = f"  [{status}] {self.name}"
        if self.detail:
            line += f"  -> {self.detail}"
        if not self.passed and self.fix:
            line += f"\n        FIX: {self.fix}"
        return line


def run_cmd(cmd: list, timeout: int = 30) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags)


def verify_binaries_exist() -> VerificationResult:
    missing = [b for b in REQUIRED_BINARIES if not (BIN_DIR / b).exists()]
    if missing:
        return VerificationResult("Required binaries present", False,
                                  f"missing: {', '.join(missing)}",
                                  "Archive may be corrupt. Re-download.")
    return VerificationResult("Required binaries present", True, f"all {len(REQUIRED_BINARIES)} found")


def verify_versions() -> list:
    results = []
    for tool in ["gcc", "g++", "gdb", "ld"]:
        exe = BIN_DIR / f"{tool}.exe"
        if not exe.exists():
            results.append(VerificationResult(f"{tool} --version", False, "binary not found"))
            continue
        try:
            r = run_cmd([str(exe), "--version"], timeout=15)
            if r.returncode == 0:
                first = r.stdout.strip().split("\n")[0]
                results.append(VerificationResult(f"{tool} --version", True, first[:80]))
            else:
                results.append(VerificationResult(f"{tool} --version", False,
                                                  f"exit {r.returncode}: {r.stderr[:100]}"))
        except Exception as e:
            results.append(VerificationResult(f"{tool} --version", False, str(e)))
    return results


def verify_compile_c() -> VerificationResult:
    test_dir = Path(tempfile.mkdtemp(prefix="mingw_test_c_"))
    src, exe = test_dir / "test.c", test_dir / "test_c.exe"
    src.write_text('#include <stdio.h>\nint main(){printf("C_OK");return 0;}\n')
    try:
        r = run_cmd([str(BIN_DIR / "gcc.exe"), str(src), "-o", str(exe)], timeout=30)
        if r.returncode != 0:
            return VerificationResult("C compile + run", False, f"compile error: {r.stderr[:200]}")
        run = run_cmd([str(exe)], timeout=10)
        if "C_OK" in run.stdout:
            return VerificationResult("C compile + run", True, "output verified")
        return VerificationResult("C compile + run", False, f"wrong output: {run.stdout[:100]}")
    except Exception as e:
        return VerificationResult("C compile + run", False, str(e))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def verify_compile_cpp() -> VerificationResult:
    test_dir = Path(tempfile.mkdtemp(prefix="mingw_test_cpp_"))
    src, exe = test_dir / "test.cpp", test_dir / "test_cpp.exe"
    src.write_text(
        '#include <iostream>\n#include <vector>\n#include <string>\n'
        'int main(){std::vector<std::string> v={"a","b"};'
        'std::cout<<"CPP_OK:"<<v.size();return 0;}\n'
    )
    try:
        r = run_cmd([str(BIN_DIR / "g++.exe"), str(src), "-o", str(exe)], timeout=30)
        if r.returncode != 0:
            return VerificationResult("C++ compile + run", False, f"compile error: {r.stderr[:200]}")
        run = run_cmd([str(exe)], timeout=10)
        if "CPP_OK:2" in run.stdout:
            return VerificationResult("C++ compile + run", True, "STL + iostream verified")
        return VerificationResult("C++ compile + run", False, f"wrong output: {run.stdout[:100]}")
    except Exception as e:
        return VerificationResult("C++ compile + run", False, str(e))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def verify_static_link() -> VerificationResult:
    test_dir = Path(tempfile.mkdtemp(prefix="mingw_test_static_"))
    src, exe = test_dir / "s.cpp", test_dir / "s.exe"
    src.write_text('#include <iostream>\nint main(){std::cout<<"STATIC_OK";return 0;}\n')
    try:
        r = run_cmd([str(BIN_DIR / "g++.exe"), str(src), "-o", str(exe),
                     "-static-libgcc", "-static-libstdc++"], timeout=30)
        if r.returncode != 0:
            return VerificationResult("Static linking", False, f"error: {r.stderr[:200]}")
        run = run_cmd([str(exe)], timeout=10)
        if "STATIC_OK" in run.stdout:
            return VerificationResult("Static linking", True, "no runtime DLL deps")
        return VerificationResult("Static linking", False, f"wrong output: {run.stdout[:100]}")
    except Exception as e:
        return VerificationResult("Static linking", False, str(e))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def verify_headers() -> VerificationResult:
    include_dir = INSTALL_ROOT / "include"
    if not include_dir.exists():
        return VerificationResult("Core headers present", False, "include/ missing",
                                  "Re-extract archive.")
    missing = [h for h in CORE_HEADERS if not list(include_dir.rglob(h))]
    if missing:
        return VerificationResult("Core headers present", False, f"missing: {', '.join(missing)}",
                                  "Archive incomplete. Re-download.")
    return VerificationResult("Core headers present", True, f"{len(CORE_HEADERS)} verified")


def verify_libs() -> VerificationResult:
    lib_dir = INSTALL_ROOT / "lib"
    if not lib_dir.exists():
        return VerificationResult("Core libraries present", False, "lib/ missing", "Re-extract.")
    expected = ["libgcc.a", "libstdc++.a", "libmingw32.a"]
    missing = [l for l in expected if not list(lib_dir.glob(l))]
    if missing:
        return VerificationResult("Core libraries present", False, f"missing: {', '.join(missing)}",
                                  "Use -static-libgcc -static-libstdc++ or re-extract.")
    return VerificationResult("Core libraries present", True, f"{len(expected)} verified")


def verify_runtime_dlls() -> VerificationResult:
    dlls = ["libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll"]
    missing = [d for d in dlls if not list(BIN_DIR.glob(d))]
    if missing:
        return VerificationResult("Runtime DLLs in bin/", False, f"missing: {', '.join(missing)}",
                                  "Use -static-libgcc -static-libstdc++.")
    return VerificationResult("Runtime DLLs in bin/", True, "all present")


def verify_path_from_registry() -> VerificationResult:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ)
        path_val, _ = winreg.QueryValueEx(key, "Path")
        winreg.CloseKey(key)
    except Exception as e:
        return VerificationResult("PATH registry entry", False, str(e), "Re-run installer.")
    parts = [p.strip().rstrip("\\").lower() for p in path_val.split(";") if p.strip()]
    if str(BIN_DIR).rstrip("\\").lower() in parts:
        return VerificationResult("PATH registry entry", True, "bin/ is in user PATH")
    return VerificationResult("PATH registry entry", False, "bin/ not found in PATH",
                              'Add manually: setx PATH "%PATH%;C:\\mingw64\\bin"')


def verify_no_conflicting_gcc() -> VerificationResult:
    try:
        r = run_cmd(["where", "gcc"], timeout=10)
        if r.returncode != 0 or not r.stdout.strip():
            return VerificationResult("No conflicting gcc in PATH", True, "no other gcc detected")
        lines = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
        first = lines[0].lower() if lines else ""
        if str(BIN_DIR).lower() in first:
            return VerificationResult("No conflicting gcc in PATH", True, "our gcc is first")
        return VerificationResult("No conflicting gcc in PATH", False,
                                  f"another gcc is earlier: {lines[0]}",
                                  "Move C:\\mingw64\\bin to the front of PATH.")
    except Exception as e:
        return VerificationResult("No conflicting gcc in PATH", False, str(e))


def run_full_verification() -> list:
    header("VERIFICATION SUITE")
    results = []
    results.append(verify_binaries_exist())
    results.extend(verify_versions())
    results.append(verify_headers())
    results.append(verify_libs())
    results.append(verify_runtime_dlls())
    results.append(verify_path_from_registry())
    results.append(verify_compile_c())
    results.append(verify_compile_cpp())
    results.append(verify_static_link())
    results.append(verify_no_conflicting_gcc())
    for r in results:
        print(r)
    passed = sum(1 for r in results if r.passed)
    print(f"\n  Result: {passed}/{len(results)} checks passed")
    return results


# ---------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------
def auto_fix(results: list) -> list:
    fixes = []
    failed = [r for r in results if not r.passed]
    if not failed:
        return fixes
    header("AUTOMATIC FIX ENGINE")

    if any("binaries" in r.name.lower() or "headers" in r.name.lower() for r in failed):
        step("Re-extracting archive ...")
        try:
            extract_archive(ARCHIVE_PATH, INSTALL_ROOT)
            fixes.append("Re-extracted archive.")
        except Exception as e:
            fixes.append(f"Re-extraction failed: {e}")

    if any("PATH" in r.name for r in failed):
        _broadcast_environment_change()
        fixes.append("Re-broadcast WM_SETTINGCHANGE. Open a new terminal.")

    if any("DLL" in r.name or "Runtime" in r.name for r in failed):
        fixes.append("Compile with -static-libgcc -static-libstdc++ to avoid DLL deps.")

    if any("conflicting" in r.name.lower() for r in failed):
        fixes.append("Move C:\\mingw64\\bin to the front of your PATH.")

    for f in fixes:
        print(f"  -> {f}")
    return fixes


# ---------------------------------------------------------------
# Error analysis
# ---------------------------------------------------------------
ERROR_KNOWLEDGE_BASE = [
    (r"is not recognized as an internal or external command",
     "PATH not propagated", "Restart terminal."),
    (r"No such file or directory.*stdio\.h",
     "Missing headers", "Re-extract archive."),
    (r"undefined reference to",
     "Library not linked", "Add -L and -l flags."),
    (r"cannot find -l(\w+)",
     "Library not found", "Verify lib/ contains required .a files."),
    (r"libgcc_s_seh-1\.dll.*not found",
     "Runtime DLL missing", "Compile with -static-libgcc -static-libstdc++."),
    (r"libstdc\+\+-6\.dll.*not found",
     "C++ runtime DLL missing", "Compile with -static-libstdc++."),
    (r"libwinpthread-1\.dll.*not found",
     "pthread DLL missing", "Compile with -static or add bin/ to PATH."),
    (r"error 0xc0000139",
     "UCRT entry point mismatch", "Use matching UCRT toolchain."),
    (r"fatal error:.*No such file or directory",
     "Include path issue", "Use -I flag; verify include/."),
    (r"ld\.exe: cannot open output file",
     "Output locked", "Close running exe; run as admin."),
    (r"Permission denied",
     "Permission issue", "Run as admin; check AV."),
    (r"not a valid Win32 application",
     "Architecture mismatch", "Use matching arch build (x86_64 vs i686)."),
]


def analyze_compile_error(stderr: str) -> list:
    out = []
    for pat, cause, fix in ERROR_KNOWLEDGE_BASE:
        if re.search(pat, stderr, re.I):
            out.append({"cause": cause, "fix": fix})
    return out


def report_error_analysis(results: list) -> None:
    header("ERROR ANALYSIS")
    found = False
    for r in results:
        if r.passed:
            continue
        if "compile" in r.name.lower() or "error" in r.detail.lower():
            found = True
            print(f"\n  Issue: {r.name}")
            print(f"  Detail: {r.detail[:300]}")
            for m in analyze_compile_error(r.detail):
                print(f"  Cause: {m['cause']}")
                print(f"  Fix:   {m['fix']}")
    if not found:
        print("  No compile errors to analyze.")


# ---------------------------------------------------------------
# Report
# ---------------------------------------------------------------
def generate_report(arch: str, results: list, fixes: list) -> str:
    lines = [
        "=" * 60,
        "  MINGW-W64 INSTALLATION REPORT",
        "=" * 60,
        f"  Date    : {datetime.datetime.now().isoformat()}",
        f"  OS      : {windows_version()}",
        f"  Python  : {sys.version.split()[0]} ({'64-bit' if sys.maxsize > 2**32 else '32-bit'})",
        f"  Arch    : {arch}",
        f"  Install : {INSTALL_ROOT}",
        f"  Admin   : {'yes' if is_admin() else 'no'}",
        "",
        "  CHECK RESULTS",
        "  " + "-" * 40,
    ]
    for r in results:
        lines.append(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name}")
        if r.detail:
            lines.append(f"         {r.detail}")
    lines.append(f"\n  {sum(1 for r in results if r.passed)}/{len(results)} checks passed")
    if fixes:
        lines += ["", "  APPLIED FIXES", "  " + "-" * 40]
        lines += [f"  -> {f}" for f in fixes]
    lines += ["", "=" * 60]
    return "\n".join(lines)


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------
def main() -> None:
    global ARCHIVE_PATH

    print("=" * 55)
    print("  MinGW-w64 Automatic Installer + Verifier")
    print("=" * 55)
    print(f"  OS      : {windows_version()}")
    print(f"  Python  : {sys.version.split()[0]} ({'64-bit' if sys.maxsize > 2**32 else '32-bit'})")
    print(f"  Admin   : {'yes' if is_admin() else 'no'}")
    print()

    # Elevate first — nothing else runs until we're admin
    ensure_admin()

    # Now we have admin rights — enable long paths, then proceed
    enable_long_paths()
    print()

    arch = detect_architecture()
    archive_name = ARCHIVES[arch]
    ARCHIVE_PATH = TEMP_DIR / archive_name

    print(f"[*] Architecture : {arch}")
    print(f"[*] Archive      : {archive_name}")
    print()

    download_file(archive_name, ARCHIVE_PATH)
    extract_archive(ARCHIVE_PATH, INSTALL_ROOT)
    add_to_user_path(BIN_DIR)

    results = run_full_verification()
    report_error_analysis(results)
    fixes = auto_fix(results)

    if fixes:
        step("Re-running verification after fixes ...")
        results = run_full_verification()

    report = generate_report(arch, results, fixes)
    print()
    print(report)

    report_path = Path.home() / "mingw_install_report.txt"
    try:
        report_path.write_text(report, encoding="utf-8")
        ok(f"Report saved to: {report_path}")
    except Exception:
        pass

    try:
        ARCHIVE_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n[!] Completed with {len(failed)} unresolved issue(s).")
        print("    Open a NEW terminal and run: g++ --version")
        sys.exit(1)
    else:
        print("\n[+] Installation fully verified. Open a new terminal and run: g++ --version")
        sys.exit(0)


if __name__ == "__main__":
    main()
