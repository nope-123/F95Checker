#!/usr/bin/env python
# Run: python test_webview_block.py
# Needs PyQt6 + QtWebEngine (so unlike test_blocklist.py it is not dependency free),
# runs offscreen and touches no network: the page is set with setHtml() and the
# navigations it attempts are all to .invalid hosts that never resolve.
import os
import pathlib
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from modules.webview_window import (
    BrowserWindow,
    config_qt_flags,
    make_blocker,
)

config_qt_flags(debug=False, software=True)

from PyQt6 import (  # noqa: E402
    QtCore,
    QtGui,
    QtWidgets,
)

PAGE = "https://opener.invalid/file"
BLOCKED = "https://blocked.invalid/d?zid=1"
CROSS_SITE = "https://elsewhere.invalid/d?zid=1"
SAME_SITE = "https://cdn.opener.invalid/file.zip"


def run(target: str):
    """Load a page that hijacks a click into a top level navigation, as an ad gated
    download host does, and report where the tabs ended up."""
    listfile = pathlib.Path(tempfile.gettempdir()) / "f95checker_test_blocklist.txt"
    # make_blocker degrades to None on an empty list, so the file needs one entry
    listfile.write_text("# test list\nblocked.invalid\n")

    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.blocker = make_blocker(str(listfile))
    assert window.blocker is not None, "blocker failed to build"
    window.profile.setUrlRequestInterceptor(window.blocker)
    tab = window.new_tab()

    urls = []
    def finish():
        urls.extend(t.view.url().url() for t in window.tab_list)
        app.quit()
    tab.page.setHtml(
        "<html><body>file<script>setTimeout(function(){"
        f"location.href={target!r};" "}, 100)</script></body></html>",
        QtCore.QUrl(PAGE),
    )
    QtCore.QTimer.singleShot(3000, finish)
    app.exec()
    return urls


def test_blocked_host_cannot_take_the_tab():
    assert run(BLOCKED) == [PAGE], "the tab was hijacked to a blocked host"


def test_cross_site_gets_a_background_tab():
    # Not on any list, so this is the half no blocklist can cover: the page keeps its
    # place and the navigation still happens, just not in the tab you were reading
    assert run(CROSS_SITE) == [PAGE, CROSS_SITE], "cross site navigation was not moved to its own tab"


def test_same_site_navigates_in_place():
    # A subdomain of the page you are on is the page itself moving, not a hijack, and
    # it must not spawn a tab -- that would be every ordinary link on the site
    assert run(SAME_SITE) == [SAME_SITE], "same site navigation was moved out of its tab"


def test_download_tab_closes_itself():
    """The rest of the flow a download host puts you through: the file is on another
    host, so the link landed in its own tab (the case above) -- and that tab must not be
    left sitting there blank once the file has been handed to the download manager."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/file.bin":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="file.bin"')
                body = b"payload"
            else:
                self.send_response(200)
                self.send_header("Set-Cookie", "sess=onlyforme; Path=/")
                self.send_header("Content-Type", "text/html")
                body = b"<html><body>the page you were reading</body></html>"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass

    # Threading, and never shut down: a single threaded server sits blocked in a
    # connection the browser keeps alive, and shutdown() then waits on it forever
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port

    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    seen = {}
    def downloaded(download):
        seen["tabs_at_download"] = len(window.tab_list)
        # what create()'s handler does for a download manager handoff
        download.cancel()
        window.close_download_tab(download)
    window.profile.downloadRequested.connect(downloaded)
    tab = window.new_tab(f"http://localhost:{port}/")
    # The tab the cross site download link opened, still blank
    opened = window.new_tab()
    # page.download() rather than clicking the link: offscreen Chromium refuses to raise
    # a download off a navigation, and this hands the handler the same request either way
    QtCore.QTimer.singleShot(1000, lambda: opened.page.download(
        QtCore.QUrl(f"http://127.0.0.1:{port}/file.bin")))

    def finish():
        seen["tabs"] = len(window.tab_list)
        seen["url"] = tab.view.url().url()
        # Read long after the cookie store's signal returned, which is the whole point:
        # keeping the QNetworkCookie Qt hands that signal, rather than copying it into
        # the jar, is an access violation here and takes the browser down with it
        seen["cookies"] = window.cookies.header(QtCore.QUrl(f"http://localhost:{port}/file.bin"))
        seen["elsewhere"] = window.cookies.header(QtCore.QUrl("https://cdn.example.com/file.zip"))
        app.quit()
    QtCore.QTimer.singleShot(5000, finish)
    app.exec()

    assert seen.get("cookies") == "sess=onlyforme", f"the session did not survive: {seen.get('cookies')!r}"
    # The security half of matching: whatever host a file lives on is not the site the
    # session belongs to, and must never be handed it
    assert seen.get("elsewhere") == "", f"the session leaked off site: {seen.get('elsewhere')!r}"
    assert seen.get("tabs_at_download") == 2, f"the download did not get its own tab: {seen}"
    assert seen["tabs"] == 1, "the tab the download came from was left behind"
    assert seen["url"] == f"http://localhost:{port}/", f"the page lost its place: {seen['url']}"


if __name__ == "__main__":
    tests = {
        "blocked": test_blocked_host_cannot_take_the_tab,
        "cross": test_cross_site_gets_a_background_tab,
        "same": test_same_site_navigates_in_place,
        "download": test_download_tab_closes_itself,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
