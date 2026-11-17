#!/usr/bin/env python
# Run: python test_webview_video.py
# Needs PyQt6 + QtWebEngine, runs offscreen and serves its pages and videos from
# 127.0.0.1, so it touches no network and launches no browser.
import base64
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from webview_testkit import (
    isolate_settings,
    serve,
    until,
)
from modules import webview_window
from modules.webview_window import (
    BrowserWindow,
    config_qt_flags,
)

config_qt_flags(debug=False, software=True)

from PyQt6 import (  # noqa: E402
    QtGui,
    QtWidgets,
)

# Same stub as test_webview_block.py: a case that ends with two tabs open would
# otherwise hang on the browser's "close them all?" guard
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
)

# A fifth of a second of 16x16 black, made with ffmpeg. H.264 is what nearly every
# forum .mp4 is and what Qt's prebuilt WebEngine cannot decode; VP9 is what it can
H264_MP4 = base64.b64decode(
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMwbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAA"
    "AMgAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAgAAAlt0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAMgAAAAAAAAAAAAA"
    "AAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0"
    "AAAAAAAAAAEAAADIAAAAAAABAAAAAAHTbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAoAAAACABVxAAAAAAALWhk"
    "bHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABfm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAA"
    "AAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAT5zdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5h"
    "dmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFExhdmM2My4xLjEwMiBsaWJ4MjY0"
    "AAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAFA8SJZYAQAGaOvjyyLA/fj4"
    "AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAHCoAAAAAAAAABhzdHRzAAAAAAAAAAEAAAACAAAEAAAAABRz"
    "dHNzAAAAAAAAAAEAAAABAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAACAAAAAQAAABxzdHN6AAAAAAAAAAAAAAACAAAC"
    "xQAAAAwAAAAUc3RjbwAAAAAAAAABAAADYAAAAGF1ZHRhAAAAWW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJh"
    "cHBsAAAAAAAAAAAAAAAALGlsc3QAAAAkqXRvbwAAABxkYXRhAAAAAQAAAABMYXZmNjMuMS4xMDIAAAAIZnJlZQAA"
    "AtltZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NSByMzIyMyAwNDgwY2IwIC0gSC4y"
    "NjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3Jn"
    "L3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MCBhbmFseXNlPTB4MzoweDEx"
    "MyBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0xIG1lX3JhbmdlPTE2IGNo"
    "cm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hy"
    "b21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5y"
    "PTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZy"
    "YW1lcz0zIGJfcHlyYW1pZD0yIGJfYWRhcHQ9MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZWlnaHRiPTEgb3Blbl9nb3A9"
    "MCB3ZWlnaHRwPTIga2V5aW50PTI1MCBrZXlpbnRfbWluPTEwIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCBy"
    "Y19sb29rYWhlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02"
    "OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAAAD2WIhAA7//73Tr8Cm1TCYQAAAAhBmiFsQ3/+"
    "4A=="
)
VP9_WEBM = base64.b64decode(
    "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAIKEU2bdLpNu4tTq4QVSalmU6yB"
    "oU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEyTbuMU6uEHFO7a1OsggH07AEAAAAAAABZAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjMuMS4xMDJXQYxMYXZmNjMuMS4xMDJEiYhAaQAAAAAAABZU"
    "rmvXrgEAAAAAAABO14EBc8WI4t6IEOQN8y2cgQAitZyDdW5kiIEAhoVWX1ZQOYOBASPjg4QF9eEA4JCwgRC6gRCa"
    "gQJVsIRVuYEBVe6BAOwBAAAAAAAAAgAAElTDZ/5zc59jwIBnyJlFo4dFTkNPREVSRIeMTGF2ZjYzLjEuMTAyc3PZ"
    "Y8CLY8WI4t6IEOQN8y1nyKRFo4dFTkNPREVSRIeXTGF2YzYzLjEuMTAyIGxpYnZweC12cDlnyKFFo4hEVVJBVElP"
    "TkSHkzAwOjAwOjAwLjIwMDAwMDAwMAAfQ7Z1uueBAKOggQAAgIJJg0IAAPAA9gA4JBwYSgAAMGAAABC///1IjACj"
    "k4EAZACGAECSnABQAAADIAAAQkAcU7trkbuPs4EAt4r3gQHxggG18IED"
)


def page(body: str, frame: str = ""):
    """A page holding body, and /frame holding frame for it to embed, with {at}
    standing for where the videos are. Not f95zone: this is for any site"""
    routes = {
        "/v/a.mp4": (200, {"Content-Type": "video/mp4"}, H264_MP4),
        "/v/c.mp4": (200, {"Content-Type": "video/mp4"}, H264_MP4),
        "/v/b.webm": (200, {"Content-Type": "video/webm"}, VP9_WEBM),
    }
    port = serve(routes)  # reads the dict per request, so the pages can go in after
    at = f"http://127.0.0.1:{port}/v/"
    for path, html in (("/page", body), ("/frame", frame)):
        routes[path] = (200, {"Content-Type": "text/html"}, f"<html><body>{html}</body></html>".replace("{at}", at).encode())
    app = QtWidgets.QApplication(sys.argv)
    isolate_settings()
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    # Shown, though offscreen: Chromium defers media in a page that has never been
    # visible, so a hidden window would never load the videos at all
    window.show()
    tab = window.new_tab(f"http://127.0.0.1:{port}/page")
    return app, window, tab, at


def js(tab, code: str):
    out = []
    tab.page.runJavaScript(code, out.append)
    until(lambda: out)
    return out[0] if out else None


def test_a_video_this_browser_cannot_play_becomes_a_link():
    """Qt's prebuilt WebEngine has no H.264, so an .mp4 is a player that never plays.
    It becomes a link to the file instead, whether the url is the video's own src or
    a <source> in it, the way f95zone posts one (markup copied from a real post).
    The WebM plays fine, so it keeps its player"""
    app, window, tab, at = page(
        "<video id='src' src='{at}a.mp4'></video>"
        "<div class='bbMediaWrapper bbMediaWrapper--inline'><div class='bbMediaWrapper-inner'>"
        "<video controls data-xf-init='video-init'><source src='{at}c.mp4' />"
        "<div class='bbMediaWrapper-fallback'>Your browser is not able to display this video.</div>"
        "</video></div></div>"
        "<video id='webm' src='{at}b.webm'></video>"
    )
    links = lambda: js(tab, "[...document.querySelectorAll('a')].map(a => a.href).join(' ')") or ""
    assert until(lambda: f"{at}a.mp4" in links() and f"{at}c.mp4" in links()), f"not swapped for links: {links()!r}"
    # Decoded is the proof it had its chance to fail and did not
    assert until(lambda: (js(tab, "document.getElementById('webm')?.readyState ?? -1") or 0) >= 1), \
        "the WebM lost its player, or never loaded"
    left = js(tab, "[...document.querySelectorAll('video')].map(v => v.id).join(' ')")
    assert left == "webm", f"videos left on the page: {left!r}"


def test_the_link_opens_the_video_in_the_incognito_browser():
    """A real browser can play it, so the link goes to one, launched private. From an
    embedded player's frame too, which is where video hosts keep theirs. Only that
    link: an ordinary one to a video is the page's own and launches nothing"""
    launched = []
    webview_window.subprocess.Popen = lambda args, **_: launched.append(args)
    app, window, tab, at = page(
        "<a id='plain' href='{at}c.mp4'>plain</a><iframe src='/frame'></iframe>",
        frame="<video src='{at}a.mp4'></video>",
    )
    window.video_browser = ["browser.exe", "--incognito"]
    link = "document.querySelector('iframe').contentDocument.querySelector('a')"
    assert until(lambda: js(tab, f"!!{link}")), "the embedded video was not swapped for a link"
    js(tab, f"{link}.click(); true")
    assert until(lambda: launched), "no browser launched"
    assert launched == [["browser.exe", "--incognito", f"{at}a.mp4"]], launched
    js(tab, "document.getElementById('plain').click(); true")
    assert until(lambda: tab.view.url().path() == "/v/c.mp4"), "the plain link went nowhere"
    assert len(launched) == 1, f"a plain link launched the browser: {launched}"


if __name__ == "__main__":
    tests = {
        "swap": test_a_video_this_browser_cannot_play_becomes_a_link,
        "open": test_the_link_opens_the_video_in_the_incognito_browser,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
