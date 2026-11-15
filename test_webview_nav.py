#!/usr/bin/env python
# Run: python test_webview_nav.py
# Needs PyQt6 + QtWebEngine (like test_webview_scroll.py), runs offscreen and serves
# its pages from 127.0.0.1, so it touches no network.
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

PAGES = {
    "/a": b"<html><body><a id='go' href='/b'>go</a> page a</body></html>",
    "/b": b"<html><body>page b</body></html>",
}


def serve():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path, b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port


def test_the_back_and_forward_buttons_navigate():
    """view.back()/forward() go through triggerAction, which does nothing at all here,
    so the buttons drive the history object instead. Same page, both directions."""
    port = serve()
    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(900, 600)
    window.show()
    tab = window.new_tab(f"http://127.0.0.1:{port}/a")
    buttons = window.controls.buttons
    seen = {}

    def at(ms, call):
        QtCore.QTimer.singleShot(ms, call)

    at(2000, lambda: tab.page.runJavaScript("document.getElementById('go').click()"))
    at(4000, lambda: buttons.back.click())
    at(6500, lambda: seen.update(back=tab.view.url().path()) or buttons.forward.click())
    at(9000, lambda: seen.update(forward=tab.view.url().path()) or app.quit())
    app.exec()

    assert seen.get("back") == "/a", f"back left the tab on {seen.get('back')}"
    assert seen.get("forward") == "/b", f"forward left the tab on {seen.get('forward')}"


def test_an_address_typed_without_a_scheme_loads():
    """What you type is not a url yet: QUrl("google.com") is a relative path that
    WebEngine turns into about:blank. The bar has to read it the way a browser does."""
    from PyQt6.QtTest import QTest
    from webview_testkit import until
    port = serve()
    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.show()
    tab = window.new_tab("about:blank")
    bar = window.controls.buttons.url
    bar.setText(f"127.0.0.1:{port}/b")
    QTest.keyClick(bar, QtCore.Qt.Key.Key_Return)
    assert until(lambda: tab.view.url().path() == "/b"), f"typing it went to {tab.view.url().toString()!r}"


if __name__ == "__main__":
    tests = {
        "backforward": test_the_back_and_forward_buttons_navigate,
        "typed": test_an_address_typed_without_a_scheme_loads,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
