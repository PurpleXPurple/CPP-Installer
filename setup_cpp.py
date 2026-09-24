import os
import sys
import ctypes
import platform
import subprocess
import winreg
import shutil
import datetime
from pathlib import Path

MINGW_BIN = Path("C:/mingw64/bin")
MINGW_ROOT = Path("C:/mingw64")
WORKSPACE = Path.home() / "cpp-workspace"
BACKUP_DIR = Path.home() / ".mingw_path_backups"

REQUIRED_TOOLS = ["gcc.exe", "g++.exe", "gdb.exe", "ld.exe", "ar.exe", "windres.exe"]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def header(msg: str) -> None:
    print()
    print("=" * 60)
    print(f"  {msg}")
    print("=" * 60)


def step(msg: str) -> None:
    print(f"[*] {msg}")


def ok(msg: str) -> None:
    print(f"[+] {msg}")


def fail(msg: str) -> None:
    print(f"[!] {msg}")


def run(cmd: list, timeout: int = 30) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags)


def check_installation() -> bool:
    header("STEP 1 — MinGW installation check")
    if not MINGW_ROOT.exists():
        fail(f"{MINGW_ROOT} does not exist.")
        fail("Run install.py first to download and extract MinGW-w64.")
        return False
    if not MINGW_BIN.exists():
        fail(f"{MINGW_BIN} does not exist.")
        fail("The archive may not have extracted correctly. Re-run install.py.")
        return False
    missing = [t for t in REQUIRED_TOOLS if not (MINGW_BIN / t).exists()]
    if missing:
        fail(f"Missing tools in {MINGW_BIN}: {', '.join(missing)}")
        fail("Re-run install.py to repair the extraction.")
        return False
    ok(f"MinGW root: {MINGW_ROOT}")
    ok(f"All {len(REQUIRED_TOOLS)} required tools found.")
    return True


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


def normalize(path_entry: str) -> str:
    return path_entry.strip().rstrip("\\").lower()


def diagnose_path() -> dict:
    header("STEP 2 — PATH diagnosis")
    user_path = read_user_path()
    system_path = read_system_path()
    target = normalize(str(MINGW_BIN))

    user_parts = [p for p in user_path.split(";") if p.strip()]
    system_parts = [p for p in system_path.split(";") if p.strip()]

    in_user = any(normalize(p) == target for p in user_parts)
    in_system = any(normalize(p) == target for p in system_parts)

    print(f"  Registry HKCU Path entries : {len(user_parts)}")
    print(f"  Registry HKLM Path entries : {len(system_parts)}")
    print(f"  {MINGW_BIN} in HKCU Path     : {'yes' if in_user else 'no'}")
    print(f"  {MINGW_BIN} in HKLM Path     : {'yes' if in_system else 'no'}")
    print(f"  HKCU Path length           : {len(user_path)} chars")

    if len(user_path) > 2000:
        fail("User PATH is unusually long. Some installers truncate at 2048.")

    return {
        "user_path": user_path,
        "system_path": system_path,
        "user_parts": user_parts,
        "system_parts": system_parts,
        "in_user": in_user,
        "in_system": in_system,
    }


def backup_path(user_path: str, system_path: str) -> Path:
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
        fail("Cannot write to HKCU\\Environment — permission denied.")
        winreg.CloseKey(key)
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
        ok(f"{MINGW_BIN} is already in HKCU Path.")
        return True

    backup_path(diag["user_path"], diag["system_path"])

    # Rebuild user PATH: preserve existing entries, ensure MinGW bin is first
    cleaned = [p.strip() for p in diag["user_parts"] if p.strip()]
    target_norm = normalize(str(MINGW_BIN))
    cleaned = [p for p in cleaned if normalize(p) != target_norm]
    cleaned.insert(0, str(MINGW_BIN))

    # Remove duplicate entries while preserving order
    seen = set()
    deduped = []
    for p in cleaned:
        key = normalize(p)
        if key and key not in seen:
            seen.add(key)
            deduped.append(p)

    new_path = ";".join(deduped)
    step(f"New HKCU Path ({len(new_path)} chars) with {len(deduped)} entries.")
    step(f"Putting {MINGW_BIN} at the front of user PATH.")

    if not write_user_path(new_path):
        return False

    broadcast_change()
    ok("Registry updated and WM_SETTINGCHANGE broadcast sent.")
    return True


def kill_explorer_refresh() -> None:
    step("Refreshing Explorer environment (optional).")
    try:
        run(["taskkill", "/F", "/IM", "explorer.exe"], timeout=10)
        import time
        time.sleep(1.5)
        subprocess.Popen(["explorer.exe"], close_fds=True)
        ok("Explorer restarted to pick up PATH changes.")
    except Exception:
        pass


def verify_gpp_in_shell() -> bool:
    header("STEP 4 — Shell verification")
    env = os.environ.copy()
    current = env.get("PATH", "")
    if normalize(str(MINGW_BIN)) not in [normalize(p) for p in current.split(";")]:
        env["PATH"] = f"{MINGW_BIN};{current}"

    try:
        r = subprocess.run(
            ["g++", "--version"],
            capture_output=True, text=True, timeout=15,
            env=env, shell=False,
        )
        if r.returncode == 0:
            first = r.stdout.strip().split("\n")[0]
            ok(f"g++ responds: {first}")
            return True
    except FileNotFoundError:
        pass
    except Exception as exc:
        fail(f"g++ check failed: {exc}")

    # Try direct path
    try:
        r = run([str(MINGW_BIN / "g++.exe"), "--version"], timeout=15)
        if r.returncode == 0:
            first = r.stdout.strip().split("\n")[0]
            ok(f"Direct g++ works: {first}")
            fail("g++ works via full path but not via PATH lookup.")
            return True
    except Exception as exc:
        fail(f"Direct g++ failed: {exc}")

    return False


def setup_workspace() -> Path:
    header("STEP 5 — Workspace setup")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    ok(f"Workspace: {WORKSPACE}")

    hello_cpp = WORKSPACE / "hello.cpp"
    if not hello_cpp.exists():
        hello_cpp.write_text(
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
        ok(f"Created {hello_cpp}")

    build_ps1 = WORKSPACE / "build.ps1"
    if not build_ps1.exists():
        build_ps1.write_text(
            'param([string]$Src = "hello.cpp", [string]$Out = "hello.exe")\n'
            '$ErrorActionPreference = "Stop"\n'
            'g++ $Src -o $Out -static-libgcc -static-libstdc++ -O2 -Wall -Wextra\n'
            'if ($LASTEXITCODE -eq 0) { Write-Host "[+] Build OK: $Out" }\n'
            'else { Write-Host "[!] Build failed" -ForegroundColor Red; exit 1 }\n',
            encoding="utf-8",
        )
        ok(f"Created {build_ps1}")

    build_bat = WORKSPACE / "build.bat"
    if not build_bat.exists():
        build_bat.write_text(
            '@echo off\n'
            'if "%~1"=="" (set SRC=hello.cpp) else (set SRC=%~1)\n'
            'if "%~2"=="" (set OUT=hello.exe) else (set OUT=%~2)\n'
            'g++ %SRC% -o %OUT% -static-libgcc -static-libstdc++ -O2 -Wall -Wextra\n'
            'if %ERRORLEVEL% neq 0 (echo [!] Build failed & exit /b 1)\n'
            'echo [+] Build OK: %OUT%\n',
            encoding="utf-8",
        )
        ok(f"Created {build_bat}")

    return WORKSPACE


def prove_toolchain(workspace: Path) -> bool:
    header("STEP 6 — Compile and run end-to-end test")
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
        ok(f"Compiled to {exe} ({exe.stat().st_size} bytes)")
    except Exception as exc:
        fail(f"Compile exception: {exc}")
        return False

    step("Running hello.exe ...")
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            fail(f"Execution failed with code {r.returncode}")
            return False
        ok("Program output:")
        for line in r.stdout.strip().split("\n"):
            print(f"      {line}")
    except Exception as exc:
        fail(f"Execution exception: {exc}")
        return False

    return True


def final_instructions(path_fixed: bool, toolchain_ok: bool) -> None:
    header("SUMMARY")

    if path_fixed:
        ok("PATH registry entry is correct.")
    else:
        fail("PATH could not be updated.")

    if toolchain_ok:
        ok("Toolchain compile + run test passed.")
    else:
        fail("Toolchain test failed.")

    print()
    print("  NEXT STEPS")
    print("  " + "-" * 56)
    if path_fixed:
        print("  1. Close every open terminal (PowerShell, CMD, VS Code).")
        print("  2. Open a NEW PowerShell window.")
        print("  3. Verify with:")
        print("         g++ --version")
        print("  4. Your workspace is ready:")
        print(f"         {WORKSPACE}")
        print("  5. Build the test program:")
        print(f"         cd {WORKSPACE}")
        print("         .\\build.ps1")
        print("         .\\hello.exe")
    else:
        print("  PATH could not be updated. Run this script as Administrator,")
        print("  or manually add the following to your user PATH:")
        print(f"         {MINGW_BIN}")

    print()
    print("  If 'g++' still isn't found after opening a new shell, restart Windows.")
    print("  OneDrive folder redirection can delay environment propagation.")
    print()


def main() -> None:
    print("=" * 60)
    print("  setup_cpp.py — MinGW-w64 PATH repair and bootstrap")
    print("=" * 60)
    print(f"  Host      : {platform.platform()}")
    print(f"  Python    : {sys.version.split()[0]}")
    print(f"  Admin     : {'yes' if is_admin() else 'no'}")
    print(f"  MinGW bin : {MINGW_BIN}")

    if not check_installation():
        sys.exit(1)

    diag = diagnose_path()
    path_fixed = fix_path(diag)

    if path_fixed and not diag["in_user"]:
        kill_explorer_refresh()

    toolchain_ok = verify_gpp_in_shell()
    workspace = setup_workspace()
    end_to_end_ok = prove_toolchain(workspace)

    final_instructions(path_fixed, toolchain_ok and end_to_end_ok)

    if path_fixed and end_to_end_ok:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
