#!/usr/bin/env python
# Run: python test_webview_block.py
# Needs PyQt6 + QtWebEngine (so unlike test_blocklist.py it is not dependency free),
# runs offscreen and touches no network: pages are either set with setHtml() and
# navigate to .invalid hosts that never resolve, or served from 127.0.0.1.
import http.server
import os
import pathlib
import sys
import tempfile
import threading

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

# Same stub as test_webview_find.py, for the same reason: app.quit() closes every
# visible top-level window, so a case that quits with two tabs open reaches the
# browser's own "close them all?" guard and hangs on a modal. The cases here get away
# without it today only because browser() never calls show() -- one added show() and
# the run would block forever. Cheaper to answer the dialog than to rely on that.
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
)

PAGE = "https://opener.invalid/file"
BLOCKED = "https://blocked.invalid/d?zid=1"
CROSS_SITE = "https://elsewhere.invalid/d?zid=1"
SAME_SITE = "https://cdn.opener.invalid/file.zip"


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


def browser():
    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    return app, window


def tabs_after(app, window, ms: int):
    """Run the browser for ms and report where every tab ended up, which is the whole
    question these cases ask: did the page keep its place, and did the navigation still
    happen somewhere."""
    urls = []
    def finish():
        urls.extend(tab.view.url().url() for tab in window.tab_list)
        app.quit()
    QtCore.QTimer.singleShot(ms, finish)
    app.exec()
    return urls


def run(target: str):
    """Load a page that hijacks a click into a top level navigation, as an ad gated
    download host does, and report where the tabs ended up."""
    listfile = pathlib.Path(tempfile.gettempdir()) / "f95checker_test_blocklist.txt"
    # make_blocker degrades to None on an empty list, so the file needs one entry
    listfile.write_text("# test list\nblocked.invalid\n")

    app, window = browser()
    window.blocker = make_blocker(str(listfile))
    assert window.blocker is not None, "blocker failed to build"
    window.profile.setUrlRequestInterceptor(window.blocker)
    tab = window.new_tab()

    tab.page.setHtml(
        "<html><body>file<script>setTimeout(function(){"
        f"location.href={target!r};" "}, 100)</script></body></html>",
        QtCore.QUrl(PAGE),
    )
    return tabs_after(app, window, 3000)


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


def test_redirect_cannot_take_the_tab():
    """How the ad actually reaches you on a download host: the button points at a
    tracker url on the host you are already on, so the click itself is same site and
    only the 302 out of it leaves. The click has to be allowed and the redirect
    caught -- and the redirect is the hop no blocklist knows the domain of yet."""
    port = serve({
        "/page": (200, {"Content-Type": "text/html"}, b"<html><body>the page you were reading</body></html>"),
        # 127.0.0.1 rather than localhost: same server, different site
        "/go": (302, {"Location": "http://127.0.0.1:{port}/ad"}, b""),
        "/ad": (200, {"Content-Type": "text/html"}, b"<html><body>ad</body></html>"),
    })
    app, window = browser()
    tab = window.new_tab(f"http://localhost:{port}/page")
    # The click on the download button, once the page it is on has loaded
    QtCore.QTimer.singleShot(2000, lambda: tab.page.runJavaScript(
        f"location.href='http://localhost:{port}/go';"))

    # The ad does not get to keep the tab it was given either: a redirect earns one
    # only by handing over a file, and this one rendered a page
    urls = tabs_after(app, window, 6000)
    assert urls == [f"http://localhost:{port}/page"], \
        f"a redirect took a tab off site: {urls}"


def test_masked_f95zone_link_stays_in_the_tab():
    """A masked f95zone link is a page you land on and click through to the host it
    stood for (see callbacks.redirect_masked_link, which resolves one by clicking
    a.host_link). So the hop that leaves f95zone looks exactly like a page sending the
    tab somewhere else, and it still has to stay in the tab you clicked it in."""
    port = serve({
        "/threads/1": (200, {"Content-Type": "text/html"}, b"<html><body>thread</body></html>"),
        # Same server, different site, and the port comes from the page rather than
        # serve()'s header substitution
        "/masked/1": (200, {"Content-Type": "text/html"}, (
            b"<html><body>click through<script>setTimeout(function(){"
            b"location.href='http://127.0.0.1:'+location.port+'/file';"
            b"}, 300)</script></body></html>")),
        "/file": (200, {"Content-Type": "text/html"}, b"<html><body>the host it stood for</body></html>"),
    })
    # Chromium does the resolving, so the page really is on f95zone.to as far as
    # everything under test can tell. Quoted because Chromium splits its command line
    # on spaces and the rule has two of them
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] += ' --host-resolver-rules="MAP f95zone.to 127.0.0.1"'
    app, window = browser()
    tab = window.new_tab(f"http://f95zone.to:{port}/threads/1")
    QtCore.QTimer.singleShot(2000, lambda: tab.page.runJavaScript(
        f"location.href='http://f95zone.to:{port}/masked/1';"))

    urls = tabs_after(app, window, 6000)
    assert urls == [f"http://127.0.0.1:{port}/file"], \
        f"a masked link was moved out of the tab it was clicked in: {urls}"


def test_popup_ad_does_not_keep_its_tab():
    """An ad gated download host fires a popup on the same click that starts the
    download. One that hands over a file keeps its tab until the download handler
    closes it; one that only renders an ad does not get to sit there."""
    port = serve({
        "/page": (200, {"Content-Type": "text/html"}, (
            b"<html><body>page<script>setTimeout(function(){"
            b"window.open('http://127.0.0.1:'+location.port+'/ad');"
            b"}, 300)</script></body></html>")),
        "/ad": (200, {"Content-Type": "text/html"}, b"<html><body>ad</body></html>"),
    })
    app, window = browser()
    window.new_tab(f"http://localhost:{port}/page")

    # Counted rather than sampled: the tab is meant to be gone by the end, so without
    # this the case would pass just as happily if the popup had never opened at all
    opened = []
    real_new_tab = window.new_tab
    def spy(*args, **kwargs):
        opened.append(tab := real_new_tab(*args, **kwargs))
        return tab
    window.new_tab = spy

    urls = tabs_after(app, window, 6000)
    assert len(opened) == 1, f"the popup never opened, so this proves nothing: {opened}"
    assert urls == [f"http://localhost:{port}/page"], f"a popup ad kept its tab: {urls}"


def test_download_tab_closes_itself():
    """The rest of the flow a download host puts you through: the file is on another
    host, so the link landed in its own tab (the case above) -- and that tab must not be
    left sitting there blank once the file has been handed to the download manager."""
    port = serve({
        "/": (200, {"Set-Cookie": "sess=onlyforme; Path=/", "Content-Type": "text/html"},
              b"<html><body>the page you were reading</body></html>"),
        "/file.bin": (200, {"Content-Type": "application/octet-stream",
                            "Content-Disposition": 'attachment; filename="file.bin"'}, b"payload"),
    })
    app, window = browser()
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
        "redirect": test_redirect_cannot_take_the_tab,
        "masked": test_masked_f95zone_link_stays_in_the_tab,
        "popup": test_popup_ad_does_not_keep_its_tab,
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
