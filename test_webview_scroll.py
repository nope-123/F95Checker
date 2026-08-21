#!/usr/bin/env python
# Run: python test_webview_scroll.py
# Needs PyQt6 + QtWebEngine (like test_webview_block.py, unlike test_blocklist.py),
# runs offscreen and serves its pages from 127.0.0.1, so it touches no network.
import http.server
import os
import sys
import threading

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from modules.webview_window import (
    BrowserWindow,
    config_qt_flags,
)

config_qt_flags(debug=False, software=True)

from PyQt6 import (  # noqa: E402
    QtCore,
    QtGui,
    QtWidgets,
)

# Quitting closes every visible window, so a case that ends with two tabs open reaches
# the browser's own "close them all?" guard and hangs on a modal. Answer it.
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
)

TARGET = 20
# Text with nothing fixing its height, so the document's length depends entirely on the
# width it is laid out at -- like every forum thread. overflow-anchor is off because
# Chromium's scroll anchoring happens to paper over the relayout on a page as plain as
# this one, and a real thread full of images and script does not get that lucky. What
# is under test is the width the page is laid out at, not how well Chromium recovers
POSTS = "".join(
    f'<div id="post-{i}" style="border:1px solid #888">post {i} ' + "word " * 150 + "</div>"
    for i in range(40)
)
THREAD = (
    "<html><head><style>html{overflow-anchor:none}</style></head>"
    f"<body style='margin:0'>{POSTS}</body></html>"
).encode()

WIDTH, HEIGHT = 1200, 900


def serve(routes: dict):
    """Serve a path -> (status, headers, body) table on 127.0.0.1 and return the port.
    Header values may contain {port}. Threaded and never shut down, exactly as in
    test_webview_block.py and for the same reason."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(status)
            for header, value in headers.items():
                self.send_header(header, value.format(port=self.server.server_port))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port


def thread_server():
    """A thread page plus the /post-N link that redirects into it, the way XenForo
    answers a permalink to a single post."""
    return serve({
        "/thread": (200, {"Content-Type": "text/html"}, THREAD),
        "/post": (302, {"Location": "http://127.0.0.1:{port}/thread#post-%d" % TARGET}, b""),
    })


def browser():
    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(WIDTH, HEIGHT)
    window.show()
    return app, window


def probe(app, tab, script: str, ms: int):
    """Run the browser for ms, then ask one tab's page a question and quit."""
    seen = {}
    def ask():
        def got(result):
            seen["result"] = result
            app.quit()
        tab.page.runJavaScript(script, got)
    QtCore.QTimer.singleShot(ms, ask)
    app.exec()
    return seen.get("result")


def test_background_tab_is_laid_out_at_the_page_size():
    """A tab you never looked at still has to render at the size it will be shown at.
    QStackedLayout only gives geometry to the tab on screen, so left alone the page
    lays out at QWidget's 100x30 default."""
    port = thread_server()
    app, window = browser()
    window.new_tab(f"http://127.0.0.1:{port}/thread")
    background = window.new_tab(f"http://127.0.0.1:{port}/thread", background=True)

    width = probe(app, background, "window.innerWidth", 3000)
    assert width == WIDTH, f"a background tab laid its page out {width}px wide, not {WIDTH}"


def test_a_post_link_opened_in_a_background_tab_keeps_its_place():
    """The bug this file exists for: middle click a post link, switch to the tab, and
    the thread is scrolled to its very end instead of to the post."""
    port = thread_server()
    app, window = browser()
    window.new_tab(f"http://127.0.0.1:{port}/thread")
    background = window.new_tab(f"http://127.0.0.1:{port}/post", background=True)
    # Switching is what used to lose the place: the page relayouts at the real width,
    # the document comes out many times shorter, and the scroll clamps to the bottom
    QtCore.QTimer.singleShot(3000, lambda: window.tabs.setCurrentIndex(
        window.tab_list.index(background)
    ))

    seen = probe(app, background, """
        JSON.stringify({
            scroll: Math.round(window.scrollY),
            post: Math.round(document.getElementById('post-%d').getBoundingClientRect().top),
            end: Math.round(document.documentElement.scrollHeight - window.innerHeight),
        })
    """ % TARGET, 6000)
    import json
    at = json.loads(seen)
    assert abs(at["post"]) <= 2, f"the tab did not stop on the post: {at}"
    assert at["scroll"] != at["end"], f"the tab ran to the end of the thread: {at}"


if __name__ == "__main__":
    tests = {
        "size": test_background_tab_is_laid_out_at_the_page_size,
        "anchor": test_a_post_link_opened_in_a_background_tab_keeps_its_place,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
