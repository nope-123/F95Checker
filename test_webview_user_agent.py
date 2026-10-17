#!/usr/bin/env python
# Run: python test_webview_user_agent.py
# Needs PyQt6 + QtWebEngine, runs offscreen and touches no network. Reads the pipe
# the way webview.get_user_agent() does, because a failure there is swallowed: the
# app just keeps sending F95Checker's own user agent to the forum
import os
import pathlib
import subprocess
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
root = pathlib.Path(__file__).parent

if sys.argv[1:] == ["child"]:
    # Its own process, like the webview-daemon: one QApplication per process
    from modules import webview
    webview.user_agent(
        debug=False,
        software=True,
        private=True,
        buttons=False,
        extension=False,
        icon=str(root / "resources/icons/icon.png"),
        icon_font=str(next(root.glob("resources/fonts/materialdesignicons-webfont.*.ttf"))),
        style_bg="#000000",
        style_accent="#ffffff",
        style_text="#ffffff",
        style_text_dim="#808080",
        style_corner_radius="0px",
        proxy_config=None,
        blocklist_file=None,
        download_manager=("", ""),
    )
else:
    proc = subprocess.run([sys.executable, __file__, "child"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"user_agent action failed:\n{proc.stderr}"
    assert "Mozilla/" in proc.stdout, f"no user agent on the pipe: {proc.stdout!r}\n{proc.stderr}"
    print("ok:", proc.stdout.strip())
