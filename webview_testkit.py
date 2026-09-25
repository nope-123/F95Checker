"""What the test_webview_*.py files share. Not a test itself: each of those imports it."""
import http.server
import tempfile
import threading

from PyQt6 import QtCore
from PyQt6.QtTest import QTest


def isolate_settings():
    """browser.ini to a throwaway folder. It remembers vertical tabs and the open tabs,
    so a machine that uses either would otherwise lay pages out narrower, fail the top
    strip assertions, or have its real session read and overwritten"""
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, tempfile.mkdtemp(),
    )


def settings():
    return QtCore.QSettings(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope,
        "f95checker", "browser",
    )


def until(check, ms=10000):
    """QTest.qWaitFor, which PyQt6 does not expose. Polls rather than sleeping a
    fixed time, so a page that loads fast costs nothing"""
    deadline = QtCore.QDeadlineTimer(ms)
    while not check() and not deadline.hasExpired():
        QTest.qWait(20)
    return check()


def serve(routes: dict):
    """Serve a path -> (status, headers, body) table on 127.0.0.1 and return the port.
    Header values may contain {port}. Threaded, and never shut down: a single threaded
    server sits blocked in a connection the browser keeps alive, and shutdown() then
    waits on it forever."""
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
