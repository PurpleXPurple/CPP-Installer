import os
import sys
import ctypes
import platform
import subprocess
import winreg
import datetime
import importlib.util
from pathlib import Path

# ---------------------------------------------------------------
# Constants
# ---------------------------------------------------------------
MINGW_ROOT = Path("C:/mingw64")
MINGW_BIN = MINGW_ROOT / "bin"
WORKSPACE = Path.home() / "cpp-workspace"
BACKUP_DIR = Path.home() / ".mingw_path_backups"

REQUIRED_TOOLS = ["gcc.exe", "g++.exe", "gdb.exe", "ld.exe", "ar.exe", "windres.exe"]

INSTALL_PY = Path(__file__).parent / "install.py"


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
# Utilities
# ---------------------------------------------------------------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run(cmd: list, timeout: int = 30, env: dict = None) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=flags, env=env,
    )


def install_is_complete() -> bool:
    if not MINGW_BIN.exists():
        return False
    return all((MINGW_BIN / t).exists() for t in REQUIRED_TOOLS)


# ---------------------------------------------------------------
# STEP 1 — ensure install.py has run
# ---------------------------------------------------------------
def run_install_py() -> bool:
    header("STEP 1 — Running install.py")
    if not INSTALL_PY.exists():
        fail(f"install.py not found at {INSTALL_PY}")
        fail("Place setup_cpp.py next to install.py.")
        return False

    step(f"Invoking: {INSTALL_PY}")
    try:
        r = subprocess.run(
            [sys.executable, str(INSTALL_PY)],
            timeout=1800, check=False,
        )
    except KeyboardInterrupt:
        fail("install.py interrupted by user.")
        return False
    except subprocess.TimeoutExpired:
        fail("install.py timed out after 30 minutes.")
        return False
    except Exception as exc:
        fail(f"install.py invocation failed: {exc}")
        return False

    if r.returncode != 0:
        fail(f"install.py exited with code {r.returncode}")
        return False

    return install_is_complete()


def ensure_installation() -> bool:
    header("STEP 1 — MinGW installation check")
    print(f"  MinGW root : {MINGW_ROOT}")
    print(f"  MinGW bin  : {MINGW_BIN}")

    if install_is_complete():
        ok(f"All {len(REQUIRED_TOOLS)} required tools found.")
        return True

    warn("MinGW-w64 not installed or incomplete.")
    step("Delegating to install.py ...")

    if not run_install_py():
        fail("install.py could not complete the installation.")
        return False

    if not install_is_complete():
        fail("install.py finished but required tools are still missing.")
        return False

    ok("MinGW-w64 installation verified.")
    return True


# ---------------------------------------------------------------
# STEP 2 — PATH diagnosis
# ---------------------------------------------------------------
def read_user_path() -> str:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value = ""
        winreg.CloseKey(key)
        return value
    except Exception:
        return ""


def read_system_path() -> str:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_READ,
        )
        try:
            value, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value = ""
        winreg.CloseKey(key)
        return value
    except Exception:
        return ""


def normalize(p: str) -> str:
    return p.strip().rstrip("\\").lower()


def diagnose_path() -> dict:
    header("STEP 2 — PATH diagnosis")
    user_path = read_user_path()
    system_path = read_system_path()
    target = normalize(str(MINGW_BIN))

    user_parts = [p for p in user_path.split(";") if p.strip()]
    system_parts = [p for p in system_path.split(";") if p.strip()]
    in_user = any(normalize(p) == target for p in user_parts)
    in_system = any(normalize(p) == target for p in system_parts)

    print(f"  HKCU Path entries : {len(user_parts)}")
    print(f"  HKLM Path entries : {len(system_parts)}")
    print(f"  MinGW in HKCU     : {'yes' if in_user else 'no'}")
    print(f"  MinGW in HKLM     : {'yes' if in_system else 'no'}")
    print(f"  HKCU Path length  : {len(user_path)} chars")

    if len(user_path) > 2000:
        warn("User PATH is unusually long — some tools truncate at 2048.")

    return {
        "user_path": user_path,
        "system_path": system_path,
        "user_parts": user_parts,
        "system_parts": system_parts,
        "in_user": in_user,
        "in_system": in_system,
    }


# ---------------------------------------------------------------
# STEP 3 — PATH repair
# ---------------------------------------------------------------
def backup_paths(user_path: str, system_path: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"path_backup_{stamp}.txt"
    with open(backup_file, "w", encoding="utf-8") as f:
        f.write(f"HKCU Path:\n{user_path}\n\nHKLM Path:\n{system_path}\n")
    ok(f"PATH backed up to {backup_file}")
    return backup_file


def write_user_path(new_path: str) -> bool:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        )
    except FileNotFoundError:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment")
    try:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
        winreg.CloseKey(key)
        return True
    except PermissionError:
        fail("Permission denied writing HKCU\\Environment.")
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
        return False
    except Exception as exc:
        fail(f"Registry write failed: {exc}")
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
        return False


def broadcast_change() -> None:
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x1A, 0, "Environment", 0x0002, 5000, None,
        )
    except Exception:
        pass


def fix_path(diag: dict) -> bool:
    header("STEP 3 — PATH repair")

    if diag["in_user"]:
        ok(f"{MINGW_BIN} already in HKCU Path.")
        return True

    backup_paths(diag["user_path"], diag["system_path"])

    target_norm = normalize(str(MINGW_BIN))
    cleaned = [p.strip() for p in diag["user_parts"] if p.strip()]
    cleaned = [p for p in cleaned if normalize(p) != target_norm]
    cleaned.insert(0, str(MINGW_BIN))

    seen, deduped = set(), []
    for p in cleaned:
        key = normalize(p)
        if key and key not in seen:
            seen.add(key)
            deduped.append(p)

    new_path = ";".join(deduped)
    step(f"New HKCU Path: {len(new_path)} chars, {len(deduped)} entries, MinGW first.")

    if not write_user_path(new_path):
        return False

    broadcast_change()
    ok("Registry updated, WM_SETTINGCHANGE broadcast sent.")
    return True


def refresh_explorer() -> None:
    step("Restarting Explorer to pick up PATH change.")
    try:
        run(["taskkill", "/F", "/IM", "explorer.exe"], timeout=10)
        import time
        time.sleep(1.5)
        subprocess.Popen(["explorer.exe"], close_fds=True)
        ok("Explorer restarted.")
    except Exception:
        pass


# ---------------------------------------------------------------
# STEP 4 — shell verification
# ---------------------------------------------------------------
def verify_gpp_in_shell() -> bool:
    header("STEP 4 — Shell verification")
    env = os.environ.copy()
    env["PATH"] = f"{MINGW_BIN};{env.get('PATH', '')}"

    try:
        r = subprocess.run(["g++", "--version"], capture_output=True, text=True,
                           timeout=15, env=env, shell=False)
        if r.returncode == 0:
            ok(f"g++ responds: {r.stdout.strip().splitlines()[0]}")
            return True
    except FileNotFoundError:
        pass
    except Exception as exc:
        fail(f"g++ via PATH failed: {exc}")

    try:
        r = run([str(MINGW_BIN / "g++.exe"), "--version"], timeout=15)
        if r.returncode == 0:
            ok(f"g++ direct works: {r.stdout.strip().splitlines()[0]}")
            warn("PATH lookup still failing — new shell needed.")
            return True
    except Exception as exc:
        fail(f"g++ direct failed: {exc}")

    return False


# ---------------------------------------------------------------
# STEP 5 — workspace bootstrap
# ---------------------------------------------------------------
def setup_workspace() -> Path:
    header("STEP 5 — Workspace setup")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    ok(f"Workspace: {WORKSPACE}")

    hello = WORKSPACE / "hello.cpp"
    if not hello.exists():
        hello.write_text(
            '#include <iostream>\n'
            '#include <vector>\n'
            '#include <string>\n'
            '\n'
            'int main() {\n'
            '    std::vector<std::string> langs = {"C", "C++", "MinGW-w64"};\n'
            '    for (const auto& lang : langs) {\n'
            '        std::cout << "Hello from " << lang << "!\\n";\n'
            '    }\n'
            '    std::cout << "Compiler: " << __VERSION__ << "\\n";\n'
            '    return 0;\n'
            '}\n',
            encoding="utf-8",
        )
        ok(f"Created {hello}")

    ps1 = WORKSPACE / "build.ps1"
    if not ps1.exists():
        ps1.write_text(
            'param([string]$Src = "hello.cpp", [string]$Out = "hello.exe")\n'
            '$ErrorActionPreference = "Stop"\n'
            'g++ $Src -o $Out -static-libgcc -static-libstdc++ -O2 -Wall -Wextra\n'
            'if ($LASTEXITCODE -eq 0) { Write-Host "[+] Build OK: $Out" }\n'
            'else { Write-Host "[!] Build failed" -ForegroundColor Red; exit 1 }\n',
            encoding="utf-8",
        )
        ok(f"Created {ps1}")

    bat = WORKSPACE / "build.bat"
    if not bat.exists():
        bat.write_text(
            '@echo off\n'
            'if "%~1"=="" (set SRC=hello.cpp) else (set SRC=%~1)\n'
            'if "%~2"=="" (set OUT=hello.exe) else (set OUT=%~2)\n'
            'g++ %SRC% -o %OUT% -static-libgcc -static-libstdc++ -O2 -Wall -Wextra\n'
            'if %ERRORLEVEL% neq 0 (echo [!] Build failed & exit /b 1)\n'
            'echo [+] Build OK: %OUT%\n',
            encoding="utf-8",
        )
        ok(f"Created {bat}")

    return WORKSPACE


# ---------------------------------------------------------------
# STEP 6 — end-to-end test
# ---------------------------------------------------------------
def prove_toolchain(workspace: Path) -> bool:
    header("STEP 6 — End-to-end compile + run")
    src = workspace / "hello.cpp"
    exe = workspace / "hello.exe"
    if exe.exists():
        exe.unlink()

    env = os.environ.copy()
    env["PATH"] = f"{MINGW_BIN};{env.get('PATH', '')}"

    step("Compiling hello.cpp ...")
    try:
        r = subprocess.run(
            [str(MINGW_BIN / "g++.exe"), str(src), "-o", str(exe),
             "-static-libgcc", "-static-libstdc++", "-O2"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if r.returncode != 0:
            fail("Compilation failed:")
            print(r.stderr[:600])
            return False
        ok(f"Compiled: {exe.name} ({exe.stat().st_size} bytes)")
    except Exception as exc:
        fail(f"Compile exception: {exc}")
        return False

    step("Running hello.exe ...")
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            fail(f"Execution returned {r.returncode}")
            return False
        ok("Program output:")
        for line in r.stdout.strip().splitlines():
            print(f"      {line}")
    except Exception as exc:
        fail(f"Execution exception: {exc}")
        return False

    return True


# ---------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------
def final_instructions(path_fixed: bool, toolchain_ok: bool) -> None:
    header("SUMMARY")
    ok("PATH registry entry correct.") if path_fixed else fail("PATH update failed.")
    ok("Toolchain compile + run passed.") if toolchain_ok else fail("Toolchain test failed.")

    print()
    print("  NEXT STEPS")
    print("  " + "-" * 56)
    if path_fixed:
        print("  1. Close every terminal (PowerShell, CMD, VS Code).")
        print("  2. Open a NEW PowerShell window.")
        print("  3. Verify:")
        print("         g++ --version")
        print("  4. Workspace:")
        print(f"         {WORKSPACE}")
        print("  5. Build:")
        print(f"         cd {WORKSPACE}")
        print("         .\\build.ps1")
        print("         .\\hello.exe")
    else:
        print("  PATH not updated. Run as Administrator, or manually append:")
        print(f"         {MINGW_BIN}")
    print()


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------
def main() -> None:
    print("=" * 60)
    print("  setup_cpp.py — MinGW-w64 bootstrap")
    print("=" * 60)
    print(f"  Host      : {platform.platform()}")
    print(f"  Python    : {sys.version.split()[0]}")
    print(f"  Admin     : {'yes' if is_admin() else 'no'}")
    print(f"  MinGW bin : {MINGW_BIN}")

    # 1. Ensure MinGW is installed (delegates to install.py if not)
    if not ensure_installation():
        sys.exit(1)

    # 2. Diagnose and repair PATH
    diag = diagnose_path()
    path_fixed = fix_path(diag)

    # 3. Refresh Explorer if PATH changed
    if path_fixed and not diag["in_user"]:
        refresh_explorer()

    # 4. Verify compiler is reachable
    toolchain_ok = verify_gpp_in_shell()

    # 5. Bootstrap a workspace
    workspace = setup_workspace()

    # 6. Prove the toolchain works end-to-end
    end_to_end_ok = prove_toolchain(workspace)

    # 7. Summary
    final_instructions(path_fixed, toolchain_ok and end_to_end_ok)

    sys.exit(0 if (path_fixed and end_to_end_ok) else 1)


if __name__ == "__main__":
    main()
