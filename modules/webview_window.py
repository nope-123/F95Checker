import base64
import json
import os
import pathlib
import shlex
import subprocess
import sys
import threading

from PyQt6 import (
    QtCore,
    QtGui,
    QtNetwork,
    QtWebChannel,
    QtWidgets,
)
from PyQt6.QtNetwork import QNetworkProxy

from common.structs import ChildPipe


def config_qt_flags(debug: bool, software: bool):
    # Linux had issues with blank login pages and broken contexts, software mode
    # helped out and might also prevent problems on other platforms
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join((
        "--no-sandbox",
        *(("--disable-gpu",) if software else ()),
        *((
            "--enable-logging",
            "--log-level=0",
        ) if debug else (
            "--disable-logging",
        )),
    ))
    if software: os.environ["QMLSCENE_DEVICE"] = "softwarecontext"


def apply_proxy(proxy_config: dict | None):
    if not proxy_config or proxy_config["type"] == QNetworkProxy.ProxyType.NoProxy:
        return None
    proxy = QNetworkProxy()
    proxy.setType(QNetworkProxy.ProxyType[proxy_config["type"]])
    proxy.setHostName(proxy_config["host"])
    proxy.setPort(proxy_config["port"])
    if proxy_config["username"]:
        proxy.setUser(proxy_config["username"])
    if proxy_config["password"]:
        proxy.setPassword(proxy_config["password"])
    QNetworkProxy.setApplicationProxy(proxy)
    if proxy_config["username"]:
        return (proxy_config["username"], proxy_config["password"])
    return None


def load_extension(path: str):
    qwebchanneljsfile = QtCore.QFile(":/qtwebchannel/qwebchannel.js")
    qwebchanneljsfile.open(QtCore.QFile.OpenModeFlag.ReadOnly)
    qwebchanneljs = qwebchanneljsfile.readAll().data().decode('utf-8')
    qwebchanneljsfile.close()
    return qwebchanneljs + pathlib.Path(path).read_text()


def make_rpcproxy():
    from external import async_thread
    import aiohttp
    async_thread.setup()
    class RPCProxy(QtCore.QObject):
        __slots__ = ("session",)
        def __init__(self):
            super().__init__()
            self.session = aiohttp.ClientSession(loop=async_thread.loop, cookie_jar=aiohttp.DummyCookieJar(loop=async_thread.loop))
        @QtCore.pyqtSlot(QtCore.QVariant, QtCore.QVariant, QtCore.QVariant, result=QtCore.QVariant)
        def handle(self, method, path, body):
            if body is not None:
                if not isinstance(body, str):
                    return {}
                body = body.encode()
            from common import meta
            async def _handle():
                try:
                    async with self.session.request(method, meta.rpc_url + path, data=body) as req:
                        return {"status": req.status, "body": base64.b64encode(await req.read()).decode()}
                except aiohttp.ClientError:
                    return {}
            return async_thread.wait(_handle())
    return RPCProxy()


class CookieJar(QtNetwork.QNetworkCookieJar):
    """The browser's cookies, kept so a download can be handed over with its session.

    The cookie store can only be watched, never asked, so the jar mirrors what it
    announces -- and Qt's own jar is the thing to mirror it into. It already matches on
    domain, path and the secure flag, which is what keeps a file host from being handed
    the forum session, and it copies each cookie in as it arrives: the QNetworkCookie
    these signals carry is a temporary, and reading the wrapper after the slot returns
    is an access violation that takes the whole browser down.
    """

    def __init__(self, store, parent):
        super().__init__(parent)
        store.cookieAdded.connect(self.insertCookie)
        store.cookieRemoved.connect(self.deleteCookie)
        store.loadAllCookies()  # makes it announce what was already on disk too

    def header(self, url: QtCore.QUrl):
        name_value = QtNetwork.QNetworkCookie.RawForm.NameAndValueOnly
        return "; ".join(
            bytes(cookie.toRawForm(name_value)).decode(errors="replace")
            for cookie in self.cookiesForUrl(url)
        )


class WebTab:

    def __init__(self, window: "BrowserWindow", extension: str, icon: QtGui.QIcon):
        from PyQt6 import (
            QtWebEngineCore,
            QtWebEngineWidgets,
        )
        self.window = window
        self.extension = extension
        self.icon = icon
        self.loading = False
        self.probe = False
        self.opener = None
        # Find state lives on the tab: the window has one bar, and it mirrors
        # whichever tab is current
        self.find_open = False
        self.find_query = ""
        self.find_status = ""
        self.view = QtWebEngineWidgets.QWebEngineView(window.profile, window)
        # Attributes stuffed onto the view, kept for the minimal-mode entry points
        self.view.page = self.view.page()
        self.view.history = self.view.page.history()
        self.view.cookieStore = window.profile.cookieStore()
        self.page = self.view.page

        WebAttribute = QtWebEngineCore.QWebEngineSettings.WebAttribute
        settings = self.view.page.settings()
        settings.setAttribute(WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(WebAttribute.ScrollAnimatorEnabled, True)
        # Both default to False, which silently breaks navigator.clipboard, so
        # forum "copy link" buttons and pasting into the editor do nothing
        settings.setAttribute(WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(WebAttribute.JavascriptCanPaste, True)

        self.page.setBackgroundColor(window.background_color)
        self.view.loadStarted.connect(self.load_started)
        self.view.loadProgress.connect(self.load_progress)
        self.view.loadFinished.connect(self.load_finished)
        self.view.urlChanged.connect(self.url_changed)
        self.view.titleChanged.connect(self.title_changed)
        self.page.newWindowRequested.connect(self.new_window_requested)
        self.page.navigationRequested.connect(self.navigation_requested)
        if window.proxy_auth:
            self.page.proxyAuthenticationRequired.connect(self.proxy_authenticate)
        if extension:
            # One channel per page, all sharing the window's single RPCProxy
            self.channel = QtWebChannel.QWebChannel(self.view)
            self.channel.registerObject('rpcproxy', window.rpcproxy)
            self.page.setWebChannel(self.channel)
            self.view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            self.view.customContextMenuRequested.connect(self.context_menu)

    def new_window_requested(self, request):
        from modules.blocklist import same_site
        if not self.window.tabs_enabled:
            # The one-shot windows must stay one page: a popup tab there would be
            # invisible and unclosable, and the login and redirect flows watch this
            # one view for the navigation they are waiting on. Load it in place
            return self.view.setUrl(request.requestedUrl())
        # A popup straight to a host we already block is an ad. Opening a tab for it and
        # then blocking its contents is the worst of both: you get a broken tab AND lose
        # your place. Drop it before the tab exists, which is what a real blocker does
        if (blocker := self.window.blocker) and blocker.blocks(request.requestedUrl().host()):
            return
        # Always background, never focus. Ad-gated download hosts fire a popup on the
        # same click that starts the download, so focusing it steals the page out from
        # under you. Ctrl/middle-clicked links were already background anyway
        tab = self.window.new_tab(background=True, opener=self)
        # Same deal as a redirect that leaves the site: a tab you never asked for earns
        # its place by handing over a file, and an ad popup that only renders a page
        # does not get to sit there. A ctrl or middle click is you asking for the tab by
        # name, and arrives as a background tab request rather than a page's own window
        # A popup that stays on the site you are on is that site's own flow, not an ad:
        # ads live on somebody else's domain, that is what makes them ads. Google Drive
        # opens its "can't scan this file for viruses, download anyway?" page this way,
        # and probing closed it before anyone could click the button
        from PyQt6 import QtWebEngineCore
        tab.probe = (
            request.destination() is not QtWebEngineCore.QWebEngineNewWindowRequest.DestinationType.InNewBackgroundTab
            and "f95zone.to" not in self.view.url().host()
            and not same_site(request.requestedUrl().host(), self.view.url().host())
        )
        # openIn() preserves the opener relationship, setUrl() does not
        request.openIn(tab.page)

    def navigation_requested(self, request):
        from PyQt6 import QtWebEngineCore
        from modules.blocklist import same_site
        NavigationType = QtWebEngineCore.QWebEngineNavigationRequest.NavigationType
        # Gated on tabs for the same reason new_window_requested is: the resolver and
        # login windows are one view watching for the very navigation this would move
        if not request.isMainFrame() or not self.window.tabs_enabled:
            return
        # Typed stays exempt throughout: the address bar is the user's own instruction
        # and their way out, so it must never silently do nothing
        if request.navigationType() is NavigationType.TypedNavigation:
            return
        url = request.url()
        # The popup check and interceptRequest both miss the ad. An ad chain enters
        # through a domain no list knows and only lands on a listed host a redirect or
        # two later -- by which point it is a top level navigation, and interceptRequest
        # lets every one of those through by design. This is the one place that sees the
        # whole chain, hop by hop. Rejecting also beats blocking the request: the tab
        # stays on the page it was showing rather than becoming an error page
        if (blocker := self.window.blocker) and blocker.blocks(url.host()):
            request.reject()
            # A popup tab whose first navigation was the ad has no page to fall back
            # on, so it would sit there empty. It is never the last tab: the opener is
            # still open, and closing that one would take the whole window with it
            if not self.view.history.count() and len(self.window.tab_list) > 1:
                self.window.close_tab(self.window.tab_list.index(self))
            return
        # F95zone serves attachments inline -- a .rpy comes back as text/plain -- so the
        # browser renders the file in the tab instead of downloading it, and the download
        # manager never hears about it: the handoff hangs off downloadRequested, which a
        # rendered page never fires. Nothing on that host is a page, so ask for the file
        # rather than navigate to it. The tab a target=_blank attachment link opened is
        # left blank and closes itself from the download handler, like any download tab
        if url.host().startswith("attachments.f95zone."):
            request.reject()
            self.page.download(url)
            return
        # Ad hosts are registered faster than any blocklist adds them, so the list can
        # only ever be half the answer. The other half is that no page gets to take the
        # tab you are reading: a click that leaves this site opens a background tab
        # instead, exactly like the popups do. Costs a legit cross-site link one click
        # on the new tab, and costs an ad the page it was trying to steal.
        # Redirects count, and they are how the ad actually arrives: the download button
        # points at a tracker url on the host you are already on, so the click itself is
        # same site and only the 302 out of it leaves. Comparing against the page the tab
        # is showing is still the right question there, because a redirect has not
        # committed anything yet. Form posts, back/forward and reload stay exempt: those
        # are a login or your own history, not a page taking the tab
        if request.navigationType() not in (
            NavigationType.LinkClickedNavigation,
            NavigationType.RedirectNavigation,
            NavigationType.OtherNavigation,
        ):
            return
        # None of it off f95zone, which is the site this browser exists for and is not
        # the kind of site that steals your tab. Its masked links are a page you land on
        # and click through, so the hop that leaves is an ordinary click from an f95zone
        # url: nothing about the navigation tells it apart from any destination you
        # asked for. Blocked hosts were turned away above, so this exempts a
        # destination, never a known ad
        if "f95zone.to" in self.view.url().host():
            return
        # Nothing to lose your place in until this tab has a page of its own
        if self.view.history.count() and not same_site(url.host(), self.view.url().host()):
            request.reject()
            tab = self.window.new_tab(url.url(), background=True, opener=self)
            # A link you clicked keeps its tab, because you asked for it by name. A
            # redirect you never saw does not: it gets the tab on approval, and only a
            # file justifies it
            tab.probe = request.navigationType() is NavigationType.RedirectNavigation

    def proxy_authenticate(self, _: QtCore.QUrl, authenticator: QtNetwork.QAuthenticator, __: str):
        username, password = self.window.proxy_auth
        authenticator.setUser(username)
        authenticator.setPassword(password)

    def context_menu(self, pos: QtCore.QPoint):
        menu = self.view.createStandardContextMenu()
        data = self.view.lastContextMenuRequest()
        if (url := data.linkUrl().url()):
            if "f95zone.to/threads/" in url:
                add = QtGui.QAction(self.icon, "Add this link to F95Checker", menu)
                add.triggered.connect(lambda _: self.add_game(url))
                menu.addAction(add)
        elif "f95zone.to/threads/" in (url := self.view.url().url()):
            add = QtGui.QAction(self.icon, "Add this page to F95Checker", menu)
            add.triggered.connect(lambda _: self.add_game(url))
            menu.addAction(add)
        menu.exec(self.view.mapToGlobal(pos))

    @property
    def is_current(self):
        return self.window.current_tab is self

    def load(self, url: str):
        self.view.setUrl(QtCore.QUrl(url))

    def inject(self, suffix: str = ""):
        # Only on f95zone: integrated.js decorates thread links and does nothing
        # anywhere else, but it declares its helpers with a top-level `var`, which
        # throws on a hardened page like mega.nz that makes its global object
        # non-extensible -- and that page then shows the error to the user
        if self.extension and "f95zone.to" in self.view.url().host():
            self.page.runJavaScript(self.extension + suffix)

    def add_game(self, url: str):
        # Injected on demand rather than relying on inject(): the page you are on can
        # be anything, since you can right click an f95zone thread link on any site
        if self.extension:
            self.page.runJavaScript(self.extension + f"\naddGame({url!r});")

    def load_started(self):
        self.loading = True
        self.window.sync_controls()
        self.window.set_progress(self, 1)
        self.inject()

    def load_progress(self, value: int):
        self.window.sync_controls()
        self.window.set_progress(self, max(1, value))
        self.inject()

    def load_finished(self, ok: bool = False):
        self.loading = False
        self.window.sync_controls()
        self.window.set_progress(self, 0)
        self.inject("\nupdateIcons();")
        if self.find_open and self.find_query:
            # Highlights die with the old document, so a tab with the bar open searches
            # the page that replaced it. Background tabs too -- set_status keeps a tab
            # that is not current from writing the counter. Before the probe check
            # below, which can delete this tab's view out from under findText
            self.window.find.run(self)
        # A redirect off site is allowed a tab only long enough to hand over a file,
        # which is how a download link that hops to a CDN reaches its file at all. This
        # one rendered a page instead, so it is the ad the redirect was really for and
        # there is nothing here anyone asked to see. A download leaves loadFinished
        # false, and closes its own tab from the download handler
        if ok and self.probe and len(self.window.tab_list) > 1:
            self.window.close_tab(self.window.tab_list.index(self))

    def url_changed(self, url: QtCore.QUrl):
        if self.is_current:
            self.window.set_url_text(url.url())
        self.window.sidebar.refresh()  # the row's tooltip, and what search matches

    def title_changed(self, title: str):
        self.window.tab_title_changed(self, title)

    def reload(self):
        if self.loading:
            self.view.stop()
            self.load_finished()
        else:
            self.view.reload()
            self.load_started()


class FindBar(QtWidgets.QWidget):
    """Ctrl+F bar, floated over the top right of the page. One per window: only one
    tab is ever on screen, so the bar mirrors whichever tab is current."""

    MARGIN = 8

    def __init__(self, window: "BrowserWindow"):
        # Parented to the tab widget, not to a view: a view is backed by its own
        # composited surface, and the tab widget is anyway exactly the rectangle the
        # page fills. Never laid out, so it keeps wherever move() puts it
        super().__init__(window.tabs)
        self.window = window
        self.setObjectName("findbar")
        self.setLayout(QtWidgets.QHBoxLayout(self))
        self.layout().setContentsMargins(4, 4, 4, 4)
        self.layout().setSpacing(2)
        self.setAutoFillBackground(True)  # or the page shows through the bar

        self.query = QtWidgets.QLineEdit(self)
        self.query.setPlaceholderText("Find")
        self.query.setFixedWidth(180)
        self.status = QtWidgets.QLabel("", self)
        # Fixed width, because a longer count would need the bar itself to grow and
        # nothing resizes a widget that no layout owns
        self.status.setFixedWidth(64)
        self.status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # Not named close: that is QWidget.close, and shadowing it breaks closing
        self.prev = QtWidgets.QPushButton("\U000f005d", self)  # nf-md-arrow_up
        self.next = QtWidgets.QPushButton("\U000f0045", self)  # nf-md-arrow_down
        self.done = QtWidgets.QPushButton("\U000f0156", self)  # nf-md-close
        # Fixed, like status above: a single glyph left to its own size hint reports
        # Qt's native push-button minimum, about 80px each
        for button in (self.prev, self.next, self.done):
            button.setFixedWidth(30)
        for widget in (self.query, self.status, self.prev, self.next, self.done):
            self.layout().addWidget(widget)

        self.done.clicked.connect(lambda _=None: self.dismiss())
        self.query.textChanged.connect(lambda _: self.search())
        self.prev.clicked.connect(lambda _=None: self.search(backward=True))
        self.next.clicked.connect(lambda _=None: self.search(backward=False))
        self.query.installEventFilter(self)
        self.hide()

    def search(self, backward: bool = False):
        """Search the current tab for whatever is in the box"""
        tab = self.window.current_tab
        if not tab:
            return
        tab.find_query = self.query.text()
        self.run(tab, backward)

    def run(self, tab: "WebTab", backward: bool = False):
        """Search one named tab, which is not always the one on screen: a background
        tab re-runs its query after a page load"""
        from PyQt6 import QtWebEngineCore
        if not tab.find_query:
            # Qt does not call the callback for an empty string, so the counter has to
            # be cleared here or it keeps whatever the last real search left in it
            tab.page.findText("")
            self.set_status(tab, "")
            return
        FindFlag = QtWebEngineCore.QWebEnginePage.FindFlag
        tab.page.findText(
            tab.find_query,
            FindFlag.FindBackward if backward else FindFlag(0),
            lambda result: self.set_status(
                tab, f"{result.activeMatch()}/{result.numberOfMatches()}"
            ),
        )

    def set_status(self, tab: "WebTab", text: str):
        tab.find_status = text
        if tab.is_current:  # a background tab must never drive the chrome
            self.status.setText(text)

    def place(self):
        """Top right of the page area, in the tab widget's coordinates. The page is a
        grandchild -- it sits inside the tab widget's own stack -- so its offset has to
        be mapped rather than read off its geometry, which is relative to that stack."""
        area = self.window.tabs
        page = area.currentWidget()
        top = page.mapTo(area, QtCore.QPoint(0, 0)).y() if page else 0
        self.adjustSize()
        # Clamped, or a tab widget narrower than the bar pushes it off the left edge
        self.move(max(0, area.width() - self.width() - self.MARGIN), top + self.MARGIN)

    def reposition(self):
        """Deferred place(), for layout changes. When the tab bar appears its own Show
        arrives before Qt has moved the page down, so placing now would use a layout one
        pass out of date and leave the bar sitting on the tab bar."""
        QtCore.QTimer.singleShot(0, self.place)

    def activate(self):
        """Ctrl+F: show over the current tab, focused, with its last query selected so
        typing replaces it."""
        tab = self.window.current_tab
        if not tab:
            return
        # Set before follow(), so its find_open check takes the visible branch
        tab.find_open = True
        self.follow(tab)
        self.query.setFocus()
        self.query.selectAll()

    def follow(self, tab: "WebTab"):
        """Mirror a tab that just became current, or that Ctrl+F just opened the bar
        on. The query is restored either way, even onto the hidden box: nothing reads
        it while hidden, but it should never disagree with the tab it mirrors. Deliberately
        does not re-run the search: highlights belong to the page and survive the view
        being hidden, and findText with an unchanged query advances to the next match, so
        re-running would silently walk a tab off the match it was showing."""
        # Blocked, or restoring the query would look like typing and search
        self.query.blockSignals(True)
        self.query.setText(tab.find_query)
        self.query.blockSignals(False)
        if not tab.find_open:
            self.hide()
            return
        self.status.setText(tab.find_status)
        self.place()
        self.show()
        self.raise_()

    def dismiss(self):
        """Esc or the close button: drop the highlights, keep find_query so the next
        Ctrl+F on this tab starts from it."""
        tab = self.window.current_tab
        if tab:
            tab.find_open = False
            tab.find_status = ""
            tab.page.findText("")
            tab.view.setFocus()
        self.status.setText("")
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self.query and event.type() is QtCore.QEvent.Type.KeyPress:
            # key() is a plain int in PyQt6, so this compares by value, unlike the
            # QEvent.Type and MouseButton checks elsewhere in this file
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self.dismiss()
                return True
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                # Not returnPressed: that signal carries no modifiers, and Shift+Enter
                # for the previous match is the binding every browser has
                self.search(backward=bool(
                    event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                ))
                return True
        return super().eventFilter(obj, event)


class TabSidebar(QtWidgets.QWidget):
    """Vertical tabs: a search box over a list with one row per tab. Only ever a view
    of window.tab_list, never a second copy of it -- nothing here reorders or drops a
    row. What you do to a row becomes the call the top strip would have made, and the
    list redraws from tab_list once that lands, so the two strips cannot disagree."""

    def __init__(self, window: "BrowserWindow"):
        super().__init__(window)
        self.window = window
        self.drag_row = None  # the row a left press landed on, until the release
        self.setLayout(QtWidgets.QVBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.search = QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText("Search tabs")
        self.search.setClearButtonEnabled(True)
        self.list = QtWidgets.QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)  # hovering a row moves the close button onto it
        self.list.viewport().setAcceptDrops(True)
        self.layout().addWidget(self.search)
        self.layout().addWidget(self.list)
        # One close button moved to whichever row is hovered: a widget per row would
        # have to be rebuilt on every redraw
        self.closer = QtWidgets.QToolButton(self.list.viewport())
        self.closer.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TabCloseButton))
        self.closer.setAutoRaise(True)
        self.closer.row = -1
        self.closer.hide()

        self.closer.clicked.connect(lambda _=None: self.window.close_tab(self.closer.row))
        self.list.currentRowChanged.connect(self.row_changed)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.search.installEventFilter(self)
        # The viewport, not the list: an item view gets its mouse and drop events there
        self.list.viewport().installEventFilter(self)

    @property
    def query(self):
        return self.search.text().strip().casefold()

    def refresh(self):
        """Redraw from tab_list. Rows are reused rather than rebuilt, so a long list
        keeps its scroll position through every title change"""
        tabs = self.window.tab_list
        self.closer.hide()  # its row may have just moved or gone
        # Blocked, or selecting the current tab's row would look like a click on it
        self.list.blockSignals(True)
        while self.list.count() > len(tabs):
            self.list.takeItem(self.list.count() - 1)
        while self.list.count() < len(tabs):
            self.list.addItem("")
        query = self.query
        for row, tab in enumerate(tabs):
            title, url = tab.view.title() or "New tab", tab.view.url().toString()
            item = self.list.item(row)
            item.setText(title)
            item.setToolTip(f"{title}\n{url}")
            # Only the row: the tab itself is untouched
            item.setHidden(bool(query) and query not in title.casefold() and query not in url.casefold())
        self.list.setCurrentRow(self.window.tabs.currentIndex())
        self.list.blockSignals(False)

    def row_changed(self, row: int):
        if row >= 0:
            self.window.tabs.setCurrentIndex(row)

    def focus_search(self):
        """Ctrl+Shift+A. Only with the sidebar out: there is no box to focus otherwise"""
        if self.window.vertical:
            self.search.setFocus()
            self.search.selectAll()

    def leave_search(self):
        self.search.clear()
        if tab := self.window.current_tab:
            tab.view.setFocus()

    def hover(self, pos: QtCore.QPoint):
        if not (item := self.list.itemAt(pos)):
            self.closer.hide()
            return
        rect = self.list.visualItemRect(item)
        side = rect.height()
        self.closer.setGeometry(rect.right() - side + 1, rect.top(), side, side)
        self.closer.row = self.list.row(item)
        self.closer.show()

    def drop_index(self, pos: QtCore.QPoint):
        """Where a link dropped here opens: in front of the row under the pointer or
        behind it, whichever half it landed on, and last below every row"""
        if not (item := self.list.itemAt(pos)):
            return len(self.window.tab_list)
        return self.list.row(item) + (pos.y() > self.list.visualItemRect(item).center().y())

    def eventFilter(self, obj, event):
        Type = QtCore.QEvent.Type
        if obj is self.search and event.type() is Type.KeyPress:
            # key() is a plain int in PyQt6, so compared by value, as in FindBar
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self.leave_search()
                return True
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                rows = (row for row in range(self.list.count()) if not self.list.item(row).isHidden())
                # No match keeps the query, so it can be fixed rather than retyped
                if (first := next(rows, None)) is not None:
                    self.window.tabs.setCurrentIndex(first)
                    self.leave_search()
                return True
        elif obj is self.list.viewport():
            if event.type() is Type.MouseButtonPress:
                if event.button() is QtCore.Qt.MouseButton.MiddleButton:
                    return True  # or the view selects the row, switching to a tab being closed
                if event.button() is QtCore.Qt.MouseButton.LeftButton:
                    item = self.list.itemAt(event.position().toPoint())
                    # No reordering while filtered: with rows hidden, "between these
                    # two" is ambiguous
                    self.drag_row = self.list.row(item) if item and not self.query else None
                # Not swallowed: the view still selects the row, which switches to it
            elif event.type() is Type.MouseMove:
                if not event.buttons() & QtCore.Qt.MouseButton.LeftButton:
                    self.hover(event.position().toPoint())
                    return False
                # Reordered live under the pointer, as the top strip does, rather than
                # by drag and drop, which the list finishes by deleting the dragged row
                item = self.list.itemAt(event.position().toPoint())
                if self.drag_row is not None and item and (to := self.list.row(item)) != self.drag_row:
                    self.window.tabs.tabBar().moveTab(self.drag_row, to)  # tabMoved redraws
                    self.drag_row = to
                return True  # never the view's own drag-select, which switches tabs as it passes them
            elif event.type() is Type.MouseButtonRelease:
                self.drag_row = None
                if event.button() is QtCore.Qt.MouseButton.MiddleButton:
                    if item := self.list.itemAt(event.position().toPoint()):
                        self.window.close_tab(self.list.row(item))
                    return True
            elif event.type() is Type.Leave:
                self.closer.hide()
            elif event.type() in (Type.DragEnter, Type.DragMove, Type.Drop) and event.mimeData().hasUrls():
                event.acceptProposedAction()
                if event.type() is Type.Drop:
                    self.window.open_dropped(
                        event.mimeData().urls(), self.drop_index(event.position().toPoint()),
                    )
                return True
        return super().eventFilter(obj, event)


class BrowserWindow(QtWidgets.QWidget):
    # Qt only recognizes signals declared as class attributes
    url_received = QtCore.pyqtSignal(str, dict)

    def __init__(
        self, *,
        buttons: bool,
        tabs: bool,
        private: bool,
        icon: QtGui.QIcon,
        background_color: QtGui.QColor,
        extension: str,
        rpcproxy,
        proxy_auth: tuple[str, str] | None,
        title: str | None,
    ):
        super().__init__()
        from PyQt6 import QtWebEngineCore
        self.buttons_enabled = buttons
        self.tabs_enabled = tabs
        self.background_color = background_color
        self.icon = icon
        self.extension = extension
        self.rpcproxy = rpcproxy
        self.proxy_auth = proxy_auth
        self.title_fixed = bool(title)
        # install_blocker sets this only when there is a list to install, so it has to
        # exist up front: new_window_requested reads it on every popup, and an
        # AttributeError raised inside a Qt slot aborts the process and every open tab
        self.blocker = None
        self.download_manager_warned = False  # the launch-failed box is once per window
        self.tab_list = []
        self.vertical = False
        # Kept by the browser itself, not the main app: a setting there would be four
        # of upstream's files for a preference only the browser reads and writes.
        # IniFormat, so it sits in the app's own data folder rather than the registry
        self.settings = QtCore.QSettings(
            QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope,
            "f95checker", "browser",
        )
        self.profile = QtWebEngineCore.QWebEngineProfile(None if private else "F95Checker", self)
        self.cookies = CookieJar(self.profile.cookieStore(), self)

        self.setWindowIcon(icon)
        if title:
            self.setWindowTitle(title)
        self.setLayout(QtWidgets.QVBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        self.controls = QtWidgets.QWidget(self)
        self.controls.setObjectName("controls")
        self.controls.setLayout(QtWidgets.QVBoxLayout(self.controls))
        self.controls.layout().setContentsMargins(0, 0, 0, 0)
        self.controls.layout().setSpacing(0)
        self.controls.buttons = b = QtWidgets.QWidget(self.controls)
        b.setLayout(QtWidgets.QHBoxLayout(b))
        b.layout().setContentsMargins(0, 0, 0, 0)
        b.layout().setSpacing(0)
        b.back = QtWidgets.QPushButton("󰁍", b)
        b.forward = QtWidgets.QPushButton("󰁔", b)
        b.reload = QtWidgets.QPushButton("󰑐", b)
        b.url = QtWidgets.QLineEdit(b)
        b.extension = QtWidgets.QPushButton(icon, "", b)
        b.vertical = QtWidgets.QPushButton("", b)  # set_vertical picks the glyph
        for widget in (b.back, b.forward, b.reload, b.url, b.extension, b.vertical):
            b.layout().addWidget(widget)
        if buttons:
            self.controls.layout().addWidget(b)
        else:
            # Parented but never laid out, so showChildren() would still show it
            # and leave an invisible url bar reachable with Tab
            b.setVisible(False)
        self.controls.progress = QtWidgets.QProgressBar(self.controls)
        self.controls.progress.setTextVisible(False)
        self.controls.progress.setFixedHeight(2)
        self.controls.progress.setMaximum(100)
        self.controls.layout().addWidget(self.controls.progress)

        # Nav controls act on whichever tab is current
        # history, not view.back()/forward(): those go through triggerAction, which
        # silently does nothing here, while the history object navigates for real
        b.back.clicked.connect(lambda _=None: self.current_tab.view.history.back())
        b.forward.clicked.connect(lambda _=None: self.current_tab.view.history.forward())
        b.reload.clicked.connect(lambda _=None: self.current_tab.reload())
        b.url.returnPressed.connect(lambda: self.current_tab.load(b.url.text()))
        if extension:
            b.extension.clicked.connect(
                lambda _=None: self.current_tab.add_game(self.current_tab.view.url().url())
            )
        else:
            b.extension.setVisible(False)
        b.vertical.clicked.connect(lambda _=None: self.toggle_vertical())
        b.vertical.setVisible(tabs)  # the one-page windows have no tabs to lay out

        self.tabs = QtWidgets.QTabWidget(self)
        # Built right away, before the tab bar's own event filter goes in below: that
        # filter's Show/Hide branch dereferences self.find, and an AttributeError
        # raised inside a Qt slot aborts the process and every open tab with it
        self.find = FindBar(self)
        # Same reason: tab_changed and tabMoved redraw it
        self.sidebar = TabSidebar(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        # Squeeze tabs to fit like a browser does rather than scroll them: QTabBar never
        # scrolls during a drag, so a tab scrolled off the edge is one you can't drop onto
        self.tabs.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        # Qt has no middle-click-to-close, so filter the tab bar's own events. A filter
        # rather than a QTabBar subclass: nothing to construct before the tabs exist, and
        # close_tab is already on self
        self.tabs.tabBar().installEventFilter(self)
        self.tabs.tabBar().setAcceptDrops(True)  # a link dropped there opens, see eventFilter
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        # Every index in here is a tab bar index, so dragging a tab has to move
        # tab_list with it or current_tab and close_tab start hitting the wrong one
        self.tabs.tabBar().tabMoved.connect(
            lambda frm, to: self.tab_list.insert(to, self.tab_list.pop(frm))
        )
        # Connected second, so it runs once tab_list is already back in step
        self.tabs.tabBar().tabMoved.connect(lambda _, __: self.sidebar.refresh())
        # Window resize reaches the tab widget; the tab bar appearing or going away
        # does not, and it moves the page area under the bar
        self.tabs.installEventFilter(self)

        if buttons and tabs:
            # Only with the chrome: the minimal windows have no tab bar to show
            for keys, handler in (
                ("Ctrl+T", lambda: self.new_tab("about:blank")),
                ("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                ("Ctrl+Tab", self.next_tab),
                ("Ctrl+Shift+,", self.toggle_vertical),  # Edge's
                ("Ctrl+Shift+A", self.sidebar.focus_search),  # Chrome's tab search
            ):
                QtGui.QShortcut(QtGui.QKeySequence(keys), self).activated.connect(handler)

        if buttons:
            # Not gated on tabs like the shortcuts above: those act on tabs, find acts
            # on a page. This keeps it out of the chrome-less login and cookie windows
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self).activated.connect(self.find.activate)

        # Dragging the divider resizes the sidebar. Stretch only on the page, so a window
        # resize goes to the page and the sidebar keeps the width it was given
        self.splitter = QtWidgets.QSplitter(self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self.settings.value("sidebar_width", 220, type=int), 1])
        self.splitter.splitterMoved.connect(
            lambda _, __: self.settings.setValue("sidebar_width", self.splitter.sizes()[0])
        )
        # Never written here: that is toggle_vertical's job, so a login window, which
        # always shows its one page flat, cannot clobber the choice
        self.set_vertical(buttons and tabs and self.settings.value("vertical_tabs", False, type=bool))

        self.layout().addWidget(self.controls, stretch=0)
        self.layout().addWidget(self.splitter, stretch=1)

    @property
    def current_tab(self):
        i = self.tabs.currentIndex()
        return self.tab_list[i] if 0 <= i < len(self.tab_list) else None

    @property
    def webview(self):
        # Kept so cookies()/css_redirect()/xpath_redirect() need no changes
        return self.current_tab.view

    def new_tab(self, url: str = None, background: bool = False, opener: WebTab = None):
        tab = WebTab(self, self.extension, self.icon)
        tab.opener = opener
        # A link opens beside the page it came from, not at the far end of the strip,
        # and a run of them off one page stays in the order they were clicked. Ctrl+T
        # and links sent from the main app have no page to sit beside, so they append
        index = len(self.tab_list)
        if opener in self.tab_list:
            index = self.tab_list.index(opener) + 1
            while index < len(self.tab_list) and self.tab_list[index].opener is opener:
                index += 1
        index = self.tabs.insertTab(index, tab.view, "New tab")
        self.tab_list.insert(index, tab)
        # A stacked layout only gives geometry to the tab on screen, so a tab that opens
        # behind one lays its page out at QWidget's 100x30 default. A post link scrolls
        # to its anchor at that width, and switching to the tab relayouts a document many
        # times shorter -- landing you at the end of the thread instead of on the post
        if (shown := self.tabs.currentWidget()) is not tab.view:
            tab.view.resize(shown.size())
        if not background:
            self.tabs.setCurrentIndex(index)
            # The chrome attaches to the window before any view does, so without
            # this the back button wins initial focus and the page gets no keys
            tab.view.setFocus()
        if url:
            tab.load(url)
        self.sidebar.refresh()
        return tab

    def eventFilter(self, obj, event):
        # Only the tab widget and its own tab bar are filtered. A window resize reaches
        # the first, the tab bar coming or going moves the page area without resizing
        # it at all, and reposition() is a deferred no-op wherever nothing moved
        if event.type() in (
            QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Hide,
        ):
            self.find.reposition()
        elif event.type() is QtCore.QEvent.Type.MouseButtonRelease and obj is self.tabs.tabBar():
            if event.button() is QtCore.Qt.MouseButton.MiddleButton:
                # tabAt returns -1 off the end of the strip, where a click closes nothing
                if (index := obj.tabAt(event.position().toPoint())) >= 0:
                    self.close_tab(index)
                    return True
        elif obj is self.tabs.tabBar() and event.type() in (
            QtCore.QEvent.Type.DragEnter, QtCore.QEvent.Type.DragMove, QtCore.QEvent.Type.Drop,
        ) and event.mimeData().hasUrls():
            event.acceptProposedAction()
            if event.type() is QtCore.QEvent.Type.Drop:
                # Opens where it lands, like a browser's tab strip: in front of the tab
                # under the pointer or behind it, whichever half it was dropped on
                pos = event.position().toPoint()
                to = len(self.tab_list)
                if (at := obj.tabAt(pos)) >= 0:
                    to = at + (pos.x() > obj.tabRect(at).center().x())
                self.open_dropped(event.mimeData().urls(), to)
            return True
        return super().eventFilter(obj, event)

    def open_dropped(self, urls: list[QtCore.QUrl], to: int):
        """Links dropped on either strip, each a new tab from index `to` on"""
        for url in urls:
            tab = self.new_tab(url.toString())
            # tabMoved keeps tab_list in step, same as a drag
            self.tabs.tabBar().moveTab(self.tab_list.index(tab), to)
            to += 1

    def set_vertical(self, on: bool):
        """Tabs down the side or along the top. Only swaps which strip shows: the tabs,
        their order and the current one are untouched"""
        self.vertical = on
        self.sidebar.setVisible(on)
        # The top strip is always there otherwise, like a browser's, so a link always
        # has somewhere to be dropped. The one-page windows never get one
        self.tabs.tabBar().setVisible(self.tabs_enabled and not on)
        button = self.controls.buttons.vertical
        button.setText("\U000f1513" if on else "\U000f10aa")  # nf-md-dock_top / nf-md-dock_left
        button.setToolTip(self.vertical_label)
        self.sidebar.refresh()

    def toggle_vertical(self):
        self.set_vertical(not self.vertical)
        self.settings.setValue("vertical_tabs", self.vertical)

    @property
    def vertical_label(self):
        # Edge's wording
        return "Turn off vertical tabs" if self.vertical else "Turn on vertical tabs"

    def close_tab(self, index: int):
        if len(self.tab_list) <= 1:
            self.close()
            return
        tab = self.tab_list.pop(index)
        self.tabs.removeTab(index)
        # removeTab only hides the view and leaves it parented to the tab widget,
        # so deleting just the page would leak the view and leave it holding a
        # dangling page. The page is a child of the view, so the view takes both.
        # Deferred because this runs inside QTabBar's mouse handler
        tab.view.deleteLater()
        # Removing a tab in front of the current one moves no current tab, so nothing
        # else would redraw
        self.sidebar.refresh()

    def close_download_tab(self, download):
        # A download link that points off site now gets a tab of its own (see
        # navigation_requested, and popups have always worked this way), and a tab that
        # only ever produced a download has nothing left to show. Call this at the end
        # of the download handler, never the start: closing deletes the page the
        # download belongs to, and the save dialog spins the event loop long enough
        # for the deferred delete to actually run
        page = download.page()
        for index, tab in enumerate(self.tab_list):
            if tab.page is page:
                if not tab.view.history.count() and len(self.tab_list) > 1:
                    self.close_tab(index)
                return

    def next_tab(self):
        if len(self.tab_list) > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % len(self.tab_list))

    def tab_changed(self, _: int):
        # The whole chrome follows whichever tab is now current
        tab = self.current_tab
        self.sync_controls()
        self.sidebar.refresh()
        if not tab:
            return
        self.find.follow(tab)
        self.set_url_text(tab.view.url().url())
        self.set_progress(tab, 1 if tab.loading else 0)
        if not self.title_fixed:
            self.setWindowTitle(tab.view.title())

    def tab_title_changed(self, tab: WebTab, title: str):
        if tab in self.tab_list:
            self.tabs.setTabText(self.tab_list.index(tab), title[:30])
        self.sidebar.refresh()
        if tab.is_current and not self.title_fixed:
            self.setWindowTitle(title)

    def set_url_text(self, url: str):
        self.controls.buttons.url.setText(url)
        self.controls.buttons.url.setCursorPosition(0)

    def sync_controls(self):
        tab = self.current_tab
        if not tab:
            return
        self.controls.buttons.back.setEnabled(tab.view.history.canGoBack())
        self.controls.buttons.forward.setEnabled(tab.view.history.canGoForward())
        self.controls.buttons.reload.setText("󰅖" if tab.loading else "󰑐")

    def set_progress(self, tab: WebTab, value: int):
        if tab is not self.current_tab:
            return  # a background tab must never drive the chrome
        self.controls.progress.setValue(value)
        self.controls.progress.repaint()

    def closeEvent(self, close: QtGui.QCloseEvent):
        # The one choke point: title bar, Alt+F4, and close_tab on the last tab. That
        # last one can't prompt -- it only reaches close() with one tab left.
        # tabs_enabled too, not just the count: test_webview_close.py's chrome-less case
        if self.tabs_enabled and len(self.tab_list) > 1:
            button = QtWidgets.QMessageBox.StandardButton
            answer = QtWidgets.QMessageBox.question(
                self, "Close browser",
                f"This window has {len(self.tab_list)} tabs open. Close them all?",
                button.Yes | button.No,
                button.No,  # a stray Enter must not discard a window full of tabs
            )
            if answer is not button.Yes:
                # Falling through would delete every view under a window still on screen
                close.ignore()
                return
        close.accept()
        for tab in self.tab_list:
            tab.view.deleteLater()
        self.tab_list.clear()


def watch_stdin(window: BrowserWindow):
    # A thread, not QSocketNotifier: that one ignores non-socket handles on
    # Windows. Widgets are GUI-thread only, so hand the url over as a signal
    def reader():
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if url := message.get("open"):
                    window.url_received.emit(url, message.get("cookies") or {})
            except Exception:
                continue  # one bad line must never kill the reader for good
    threading.Thread(target=reader, daemon=True).start()


def make_blocker(path: str):
    # Reading and parsing the ~4MB / ~220k-entry list happens once here, at
    # window construction, so interceptRequest below stays a single set lookup
    # per request
    from PyQt6 import QtWebEngineCore
    from modules.blocklist import blocked, parse_blocklist
    try:
        hosts = parse_blocklist(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None  # not downloaded yet, or unreadable: degrade to no blocking
    if not hosts:
        return None
    main_frame = QtWebEngineCore.QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame

    class Blocker(QtWebEngineCore.QWebEngineUrlRequestInterceptor):
        def blocks(self, host: str):
            # Also asked before a popup gets a tab, not just per request
            return blocked(host, hosts)

        def interceptRequest(self, info):
            # Never block the top-level document: navigating *to* a blocked
            # host must fail normally (DNS/404/etc), not with a silent blank block
            if info.resourceType() is not main_frame and self.blocks(info.requestUrl().host()):
                info.block(True)

    return Blocker()


def create(
    *,
    title: str = None,
    buttons: bool = True,
    tabs: bool = True,
    size: tuple[int, int] = None,
    pos: tuple[int, int] = None,
    debug: bool,
    software: bool,
    private: bool,
    icon: str,
    icon_font: str,
    extension: str,
    style_bg: str,
    style_accent: str,
    style_text: str,
    style_text_dim: str,
    style_corner_radius: str,
    proxy_config: dict | None,
    blocklist_file: str | None,
    download_manager: tuple[str, str],
):
    config_qt_flags(debug, software)
    proxy_auth = apply_proxy(proxy_config)

    # Must happen before the QApplication exists, and never at module scope:
    # the whole point of the webview subprocess is keeping QtWebEngine out of
    # the main process
    from PyQt6 import (  # noqa: F401
        QtWebEngineCore,
        QtWebEngineWidgets,
    )

    app = QtWidgets.QApplication(sys.argv)
    app.pipe = ChildPipe()
    app.setWheelScrollLines(app.wheelScrollLines() * 2)
    icon_font = QtGui.QFontDatabase.applicationFontFamilies(QtGui.QFontDatabase.addApplicationFont(icon_font))[0]
    icon = QtGui.QIcon(icon)

    if extension:
        extension = load_extension(extension)
        rpcproxy = make_rpcproxy()
    else:
        extension = ""
        rpcproxy = None

    app.window = BrowserWindow(
        buttons=buttons,
        tabs=tabs,
        private=private,
        icon=icon,
        background_color=QtGui.QColor(style_bg),
        extension=extension,
        rpcproxy=rpcproxy,
        proxy_auth=proxy_auth,
        title=title,
    )
    # Gated on tabs, never buttons: cookies(), css_redirect() and xpath_redirect() all
    # pass tabs=False unconditionally, but the resolvers keep the chrome on their default
    # path, and blocking a request there could stall the very redirect they wait on
    if blocklist_file and tabs and (blocker := make_blocker(blocklist_file)):
        app.window.blocker = blocker  # PyQt does not own it; a GC'd interceptor stops blocking
        app.window.profile.setUrlRequestInterceptor(blocker)
    if size:
        app.window.resize(*size)
    if pos:
        app.window.move(*pos)

    def save_dialog(download: QtWebEngineCore.QWebEngineDownloadRequest):
        old_path = pathlib.Path(download.downloadDirectory()) / download.downloadFileName()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(app.window, "Save File", str(old_path), "*" + old_path.suffix)
        if path:
            new_path = pathlib.Path(path)
            download.setDownloadDirectory(str(new_path.parent))
            download.setDownloadFileName(new_path.name)
            download.accept()

    def download_requested(download: QtWebEngineCore.QWebEngineDownloadRequest):
        # download_manager crossed the process boundary as JSON, so this is a
        # list, not a tuple, but unpacking doesn't care
        # Stripped because a path pasted into the settings field very easily carries a
        # leading or trailing space, and CreateProcess then fails with a bare
        # FileNotFoundError that used to look exactly like "no manager configured"
        executable, arguments = (part.strip() for part in download_manager)
        # An external manager can only fetch a real URL off the network. A blob: or
        # data: url exists solely inside this page, so handing one over downloads
        # nothing at all -- which is how MEGA and anything else that decrypts or
        # builds its file in javascript hands the browser its result
        handoff = executable and download.url().scheme() in ("http", "https")
        if handoff:
            url = download.url().url()
            # IDM's command line cannot carry a cookie, so a file the host only serves
            # to the session that asked for it comes back as the "please log in" page.
            # Its extension gets around that by handing IDM the request rather than the
            # url, over a socket IDM listens on locally -- so do that instead, and fall
            # through to the command line if it is not there to answer
            if pathlib.Path(executable).name.lower() == "idman.exe":
                from modules import idm
                page = download.page().url() if download.page() else QtCore.QUrl()
                if idm.send_download(
                    url,
                    cookies=app.window.cookies.header(download.url()),
                    referer=page.url(),
                    user_agent=app.window.profile.httpUserAgent(),
                ):
                    download.cancel()
                    app.window.close_download_tab(download)
                    return
            # No shell, and {url} is substituted AFTER shlex.split, so nothing
            # in this web-controlled URL can be parsed as a quote or separator.
            # The executable stays out of shlex.split because POSIX rules eat
            # the backslashes in paths like C:\Program Files\...
            try:
                # arguments is free-text from settings; an unmatched quote makes
                # shlex.split raise ValueError, so it has to stay inside the try
                # too, or a bad template crashes this slot instead of falling
                # back -- and an exception escaping a signal slot takes every
                # open tab down with it
                args = [executable, *(arg.replace("{url}", url) for arg in shlex.split(arguments))]
                subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, ValueError) as exc:
                # Falling back silently makes a misconfigured manager indistinguishable
                # from no manager at all, so say it once per window, then still hand the
                # user their save dialog rather than losing the download
                if not app.window.download_manager_warned:
                    app.window.download_manager_warned = True
                    QtWidgets.QMessageBox.warning(
                        app.window, "Download manager failed",
                        f"Could not launch your download manager, so this download falls "
                        f"back to the save dialog.\n\n{type(exc).__name__}: {exc}\n\n"
                        f"Executable: {executable!r}\nArguments: {arguments!r}",
                    )
                save_dialog(download)  # bad path or bad template: user still gets their file
            else:
                download.cancel()
            app.window.close_download_tab(download)
            return
        save_dialog(download)
        app.window.close_download_tab(download)
    app.window.profile.downloadRequested.connect(download_requested)

    app.window.setStyleSheet(f"""
        #controls *, #findbar, #findbar * {{
            background: {style_bg};
            color: {style_text};
            font-size: 14pt;
            border-radius: 0px;
            border: 0px;
            margin: 0px;
            padding: 0px;
        }}
        #controls QProgressBar::chunk {{
            background: {style_accent};
        }}
        #controls QPushButton {{
            font-family: '{icon_font}';
            padding: 5px;
            padding-bottom: 3px;
        }}
        #findbar QPushButton {{
            /* Named here too, or these private use area glyphs only render where
               the platform's font fallback happens to land on the same family.
               No padding: unlike the chrome's, these buttons are a fixed width */
            font-family: '{icon_font}';
        }}
        #controls QPushButton:disabled {{
            color: {style_text_dim};
        }}
        #controls QLineEdit {{
            font-size: 12px;
            padding: 5px;
            padding-bottom: 3px;
        }}
        QMenu {{
            padding: 5px;
            background-color: {style_bg};
        }}
        QMenu::item {{
            margin: 1px;
            padding: 2px 7px 2px 7px;
            border-radius: {style_corner_radius};
            color: {style_text};
        }}
        QMenu::item:disabled {{
            color: {style_text_dim};
        }}
        QMenu::item:selected:enabled {{
            background-color: {style_accent};
        }}
        QMenu::icon {{
            padding-left: 7px;
        }}
        QMessageBox {{
            background: {style_bg};
        }}
        QMessageBox QLabel {{
            color: {style_text};
        }}
        QMessageBox QPushButton {{
            background: {style_bg};
            color: {style_text};
            /* Qt draws native chrome over a styled background until a border is set
               (#controls uses 0px above) */
            border: 1px solid {style_text_dim};
            border-radius: {style_corner_radius};
            /* Styling the button at all drops the native minimum button size with it, so
               these collapse to the width of the word inside them. min-width is on the
               contents rect, so this comes to roughly the 75px Windows would have used */
            min-width: 50px;
            padding: 5px 12px;
        }}
        QMessageBox QPushButton:hover {{
            background: {style_accent};
        }}
        QMessageBox QPushButton:focus {{
            /* Styling the border replaced the native focus ring -- put one back */
            border-color: {style_accent};
        }}
    """)

    app.window.new_tab()
    return app
