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
    QtWebEngineWidgets,  # noqa: F401 -- Qt 6.12 refuses it once the QApplication exists
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


def browser():
    """Settings in a throwaway folder: these cases switch to vertical tabs. Waits until
    the window is active, which show() is not yet, or nothing in it has focus to check"""
    from webview_testkit import isolate_settings, until
    app = QtWidgets.QApplication(sys.argv)
    isolate_settings()
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(900, 600)
    window.show()
    assert until(window.isActiveWindow), "the window never became active"
    return app, window


def shortcut(window, keys):
    return {s.key().toString(): s for s in window.findChildren(QtGui.QShortcut)}[keys]


def test_a_new_tab_waits_in_the_address_bar():
    """Ctrl+T, or a double click on the empty end of either tab strip, opens a tab
    with the address bar focused and empty, ready to type into"""
    from PyQt6.QtTest import QTest
    from webview_testkit import until
    app, window = browser()
    window.new_tab("about:blank#first")
    bar = window.controls.buttons.url
    left = QtCore.Qt.MouseButton.LeftButton
    strip, rows = window.tabs.tabBar(), window.sidebar.list.viewport()

    def side():
        window.set_vertical(True)
        until(lambda: rows.height() > 100)
        QTest.mouseDClick(rows, left, pos=QtCore.QPoint(10, rows.height() - 5))

    for way, open_it in (
        ("Ctrl+T", lambda: shortcut(window, "Ctrl+T").activated.emit()),
        ("the top strip", lambda: QTest.mouseDClick(strip, left, pos=QtCore.QPoint(strip.width() - 5, strip.height() // 2))),
        ("the vertical list", side),
    ):
        count = len(window.tab_list)
        window.current_tab.view.setFocus()
        open_it()
        assert len(window.tab_list) == count + 1, f"{way} opened {len(window.tab_list) - count} tabs"
        tab = window.current_tab
        assert tab is window.tab_list[-1], f"{way} did not switch to the new tab"
        # Once about:blank has committed, which writes the bar again
        until(lambda: tab.view.url().toString() == "about:blank")
        assert bar.hasFocus(), f"{way} left the focus on {QtWidgets.QApplication.focusWidget()}"
        assert bar.text() == "", f"{way} left {bar.text()!r} in the bar"


def test_f6_focuses_the_address_bar():
    """Selected as a whole, as in a browser, so typing replaces it"""
    from webview_testkit import until
    app, window = browser()
    tab = window.new_tab("about:blank#page")
    until(lambda: tab.view.url().toString() == "about:blank#page")
    bar = window.controls.buttons.url
    shortcut(window, "F6").activated.emit()
    assert bar.hasFocus(), f"F6 left the focus on {QtWidgets.QApplication.focusWidget()}"
    assert bar.selectedText() == "about:blank#page", f"F6 selected {bar.selectedText()!r}"


if __name__ == "__main__":
    tests = {
        "backforward": test_the_back_and_forward_buttons_navigate,
        "typed": test_an_address_typed_without_a_scheme_loads,
        "newtab": test_a_new_tab_waits_in_the_address_bar,
        "f6": test_f6_focuses_the_address_bar,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
