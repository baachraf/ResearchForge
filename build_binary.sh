#!/usr/bin/env bash
# ResearchForge — Nuitka build (Linux / macOS)
#
# The Windows counterpart is build_exe.bat. Nuitka does NOT cross-compile:
# run this ON the machine you want the binary for. The include/exclude list
# below is kept identical to build_exe.bat — only the platform flags differ.
#
# Prerequisites:
#   Linux : a C compiler (build-essential), patchelf, and the Qt runtime libs
#           (libegl1 libxkbcommon-x11-0 libxcb-cursor0 libgl1)
#   macOS : Xcode command line tools (xcode-select --install)
#   Both  : a venv with the project requirements + nuitka installed
#
# Usage: ./build_binary.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${VENV_PYTHON:-$HERE/venv/bin/python}"
BUILD_DIR="${BUILD_DIR:-$HERE/build}"

echo "=== ResearchForge — Nuitka Build (single binary) ==="

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv python not found at $VENV_PYTHON"
    echo "       create it first:  python3 -m venv venv && venv/bin/pip install -r requirements.txt nuitka"
    exit 1
fi

# Platform flags. Nuitka rejects --windows-* off Windows, and the app bundle /
# icon options are macOS-only, so they are selected here rather than inlined.
PLATFORM_ARGS=()
case "$(uname -s)" in
    Linux)
        PLATFORM_ARGS+=(--output-filename=ResearchForge)
        ;;
    Darwin)
        PLATFORM_ARGS+=(--macos-create-app-bundle --macos-app-name=ResearchForge)
        [ -f "$HERE/design/app_icone.icns" ] && \
            PLATFORM_ARGS+=(--macos-app-icon=design/app_icone.icns)
        ;;
    *)
        echo "ERROR: unsupported platform $(uname -s) — use build_exe.bat on Windows"
        exit 1
        ;;
esac

echo "Cleaning previous build..."
rm -rf "$BUILD_DIR/main.build" "$BUILD_DIR/main.dist" "$BUILD_DIR/main.onefile-build"

echo "Starting Nuitka compilation... (output: $BUILD_DIR)"
"$VENV_PYTHON" -m nuitka \
  --onefile \
  --enable-plugin=pyside6 \
  --output-dir="$BUILD_DIR" \
  "${PLATFORM_ARGS[@]}" \
  --include-data-dir=design=design \
  --include-data-dir=config=config \
  --include-data-dir=themes=themes \
  --include-module=ddgs \
  --include-module=pypdf \
  --include-module=fitz \
  --include-module=arxiv \
  --include-module=tqdm \
  --include-module=requests \
  --include-package=mcp \
  --include-package=researchforge_api \
  --include-module=mcp_server_researchforge \
  --nofollow-import-to=PySide6.QtWebEngine \
  --nofollow-import-to=PySide6.QtWebEngineCore \
  --nofollow-import-to=PySide6.QtWebEngineWidgets \
  --nofollow-import-to=PySide6.QtNetwork \
  --nofollow-import-to=PySide6.QtOpenGL \
  --nofollow-import-to=PySide6.QtOpenGLWidgets \
  --nofollow-import-to=PySide6.QtQml \
  --nofollow-import-to=PySide6.QtQuick \
  --nofollow-import-to=PySide6.QtQuickWidgets \
  --nofollow-import-to=PySide6.QtMultimedia \
  --nofollow-import-to=PySide6.QtMultimediaWidgets \
  --nofollow-import-to=PySide6.QtSql \
  --nofollow-import-to=PySide6.QtXml \
  --nofollow-import-to=PySide6.QtBluetooth \
  --nofollow-import-to=PySide6.QtPositioning \
  --nofollow-import-to=PySide6.QtSensors \
  --nofollow-import-to=PySide6.QtTest \
  --nofollow-import-to=PySide6.QtXmlPatterns \
  --nofollow-import-to=tkinter \
  --nofollow-import-to=unittest \
  --nofollow-import-to=test \
  --nofollow-import-to=setuptools \
  --nofollow-import-to=distutils \
  --nofollow-import-to=pip \
  --nofollow-import-to=numpy \
  --nofollow-import-to=pandas \
  --nofollow-import-to=scipy \
  --nofollow-import-to=PIL \
  --nofollow-import-to=matplotlib \
  --nofollow-import-to=cv2 \
  --nofollow-import-to=tensorflow \
  --nofollow-import-to=torch \
  --nofollow-import-to=IPython \
  --nofollow-import-to=jupyter \
  --nofollow-import-to=notebook \
  --assume-yes-for-downloads \
  --follow-imports \
  --lto=yes \
  --jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" \
  main.py

echo
echo "=== BUILD SUCCESS ==="
find "$BUILD_DIR" -maxdepth 1 \( -name 'ResearchForge' -o -name 'ResearchForge.app' \) -exec ls -ldh {} +
echo
echo "Smoke-test the GUI headlessly (Linux):  xvfb-run -a $BUILD_DIR/ResearchForge"
echo "Smoke-test the MCP server:              $BUILD_DIR/ResearchForge --mcp"
