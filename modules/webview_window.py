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
        self.view = QtWebEngineWidgets.QWebEngineView(window.profile, window)
        # Attributes stuffed onto the view, kept for the minimal-mode entry points
        self.view.page = self.view.page()
        self.view.history = self.view.page.history()
        self.view.profile = window.profile
        self.view.settings = self.view.page.settings()
        self.view.cookieStore = window.profile.cookieStore()
        self.page = self.view.page

        WebAttribute = QtWebEngineCore.QWebEngineSettings.WebAttribute
        self.view.settings.setAttribute(WebAttribute.LocalContentCanAccessFileUrls, True)
        self.view.settings.setAttribute(WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.settings.setAttribute(WebAttribute.ScrollAnimatorEnabled, True)
        # Both default to False, which silently breaks navigator.clipboard, so
        # forum "copy link" buttons and pasting into the editor do nothing
        self.view.settings.setAttribute(WebAttribute.JavascriptCanAccessClipboard, True)
        self.view.settings.setAttribute(WebAttribute.JavascriptCanPaste, True)

        self.page.setBackgroundColor(window.background_color)
        self.view.loadStarted.connect(self.load_started)
        self.view.loadProgress.connect(self.load_progress)
        self.view.loadFinished.connect(self.load_finished)
        self.view.urlChanged.connect(self.url_changed)
        self.view.titleChanged.connect(self.title_changed)
        self.page.newWindowRequested.connect(self.new_window_requested)
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
        tab = self.window.new_tab(background=True)
        # openIn() preserves the opener relationship, setUrl() does not
        request.openIn(tab.page)

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
                add.triggered.connect(lambda _: self.page.runJavaScript(f"addGame({url!r});"))
                menu.addAction(add)
        elif "f95zone.to/threads/" in (url := self.view.url().url()):
            add = QtGui.QAction(self.icon, "Add this page to F95Checker", menu)
            add.triggered.connect(lambda _: self.page.runJavaScript(f"addGame({url!r});"))
            menu.addAction(add)
        menu.exec(self.view.mapToGlobal(pos))

    @property
    def is_current(self):
        return self.window.current_tab is self

    def load(self, url: str):
        self.view.setUrl(QtCore.QUrl(url))

    def inject(self, suffix: str = ""):
        if self.extension:
            self.page.runJavaScript(self.extension + suffix)

    def load_started(self):
        self.loading = True
        self.window.sync_controls()
        self.window.set_progress(self, 1)
        self.inject()

    def load_progress(self, value: int):
        self.window.sync_controls()
        self.window.set_progress(self, max(1, value))
        self.inject()

    def load_finished(self, _=None):
        self.loading = False
        self.window.sync_controls()
        self.window.set_progress(self, 0)
        self.inject("\nupdateIcons();")

    def url_changed(self, url: QtCore.QUrl):
        if self.is_current:
            self.window.set_url_text(url.url())

    def title_changed(self, title: str):
        self.window.tab_title_changed(self, title)

    def reload(self):
        if self.loading:
            self.view.stop()
            self.load_finished()
        else:
            self.view.reload()
            self.load_started()


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
        self.profile = QtWebEngineCore.QWebEngineProfile(None if private else "F95Checker", self)

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
        for widget in (b.back, b.forward, b.reload, b.url, b.extension):
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
        b.back.clicked.connect(lambda _=None: self.current_tab.view.back())
        b.forward.clicked.connect(lambda _=None: self.current_tab.view.forward())
        b.reload.clicked.connect(lambda _=None: self.current_tab.reload())
        b.url.returnPressed.connect(lambda: self.current_tab.load(b.url.text()))
        if extension:
            b.extension.clicked.connect(
                lambda _=None: self.current_tab.page.runJavaScript(
                    f"addGame({self.current_tab.view.url().url()!r});"
                )
            )
        else:
            b.extension.setVisible(False)

        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabBar().setVisible(False)  # shown once a second tab exists
        # Qt has no middle-click-to-close, so filter the tab bar's own events. A filter
        # rather than a QTabBar subclass: nothing to construct before the tabs exist, and
        # close_tab is already on self
        self.tabs.tabBar().installEventFilter(self)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        # Every index in here is a tab bar index, so dragging a tab has to move
        # tab_list with it or current_tab and close_tab start hitting the wrong one
        self.tabs.tabBar().tabMoved.connect(
            lambda frm, to: self.tab_list.insert(to, self.tab_list.pop(frm))
        )

        if buttons and tabs:
            # Only with the chrome: the minimal windows have no tab bar to show
            for keys, handler in (
                ("Ctrl+T", lambda: self.new_tab("about:blank")),
                ("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                ("Ctrl+Tab", self.next_tab),
            ):
                QtGui.QShortcut(QtGui.QKeySequence(keys), self).activated.connect(handler)

        self.layout().addWidget(self.controls, stretch=0)
        self.layout().addWidget(self.tabs, stretch=1)

    @property
    def current_tab(self):
        i = self.tabs.currentIndex()
        return self.tab_list[i] if 0 <= i < len(self.tab_list) else None

    @property
    def webview(self):
        # Kept so cookies()/css_redirect()/xpath_redirect() need no changes
        return self.current_tab.view

    def new_tab(self, url: str = None, background: bool = False):
        tab = WebTab(self, self.extension, self.icon)
        index = self.tabs.addTab(tab.view, "New tab")
        self.tab_list.insert(index, tab)
        self.tabs.tabBar().setVisible(self.tabs_enabled and len(self.tab_list) > 1)
        if not background:
            self.tabs.setCurrentIndex(index)
            # The chrome attaches to the window before any view does, so without
            # this the back button wins initial focus and the page gets no keys
            tab.view.setFocus()
        if url:
            tab.load(url)
        return tab

    def eventFilter(self, obj, event):
        if obj is self.tabs.tabBar() and event.type() is QtCore.QEvent.Type.MouseButtonRelease:
            if event.button() is QtCore.Qt.MouseButton.MiddleButton:
                # tabAt returns -1 off the end of the strip, where a click closes nothing
                if (index := obj.tabAt(event.position().toPoint())) >= 0:
                    self.close_tab(index)
                    return True
        return super().eventFilter(obj, event)

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
        self.tabs.tabBar().setVisible(self.tabs_enabled and len(self.tab_list) > 1)

    def next_tab(self):
        if len(self.tab_list) > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % len(self.tab_list))

    def tab_changed(self, _: int):
        # The whole chrome follows whichever tab is now current
        tab = self.current_tab
        self.sync_controls()
        if not tab:
            return
        self.set_url_text(tab.view.url().url())
        self.set_progress(tab, 1 if tab.loading else 0)
        if not self.title_fixed:
            self.setWindowTitle(tab.view.title())

    def tab_title_changed(self, tab: WebTab, title: str):
        if tab in self.tab_list:
            self.tabs.setTabText(self.tab_list.index(tab), title[:30])
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
        if executable:
            url = download.url().url()
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
            return
        save_dialog(download)
    app.window.profile.downloadRequested.connect(download_requested)

    app.window.setStyleSheet(f"""
        #controls * {{
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
    """)

    app.window.new_tab()
    return app
