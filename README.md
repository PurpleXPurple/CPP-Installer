# mingw-w64-autoinstall

[![Platform](https://img.shields.io/badge/platform-Windows%207%20%7C%2010%20%7C%2011-blue)]()
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

Fully automatic MinGW-w64 installer for Windows. Downloads, extracts, configures PATH, verifies, and self-repairs — in one script.

## Features

- **Automatic architecture detection** — picks `x86_64` or `i686` based on the real OS, not the Python interpreter.
- **Multi-source download** — GitHub direct, `ghfast.top`, `ghproxy.net`, and the SDU university mirror, with automatic failover.
- **TLS + UA spoofing** — rotates `curl_cffi` impersonation profiles (`chrome`, `edge`, `safari`, `firefox`) and browser headers per attempt.
- **Live progress bar** — shows percentage, bytes downloaded, and current throughput.
- **Registry-based PATH update** — edits `HKCU\Environment` directly, avoiding the 1024-char truncation bug in `setx`.
- **Full verification suite** — 12 checks including compile-and-run tests for C, C++ (with STL), and static linking.
- **Automatic fix engine** — re-extracts on corruption, re-broadcasts environment changes, and suggests targeted fixes for common compiler errors.
- **Diagnostic report** — writes `~/mingw_install_report.txt` at the end of every run.

## Requirements

- Windows 7, 10, or 11 (x86_64 or i686)
- Python 3.9+
- No admin rights required

## Install

```powershell
pip install -r requirements.txt
python install.py
```

Or just double-click run.bat.
After install

Open a new terminal and run:
```powershell
g++ --version
```

You should see g++ (x86_64-posix-seh-rev0, Built by MinGW-Builds project) 16.2.0 or similar.
Compile a test program
```
cpp

// hello.cpp
#include <iostream>
int main() {
    std::cout << "Hello, MinGW!\n";
    return 0;
}
```
