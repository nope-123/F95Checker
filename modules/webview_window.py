import base64
import json
import os
import pathlib
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
        from PyQt6 import QtWebEngineCore
        if not self.window.tabs_enabled:
            # The one-shot windows must stay one page: a popup tab there would be
            # invisible and unclosable, and the login and redirect flows watch this
            # one view for the navigation they are waiting on. Load it in place
            return self.view.setUrl(request.requestedUrl())
        destination = QtWebEngineCore.QWebEngineNewWindowRequest.DestinationType
        tab = self.window.new_tab(background=request.destination() is destination.InNewBackgroundTab)
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
        self.controls.buttons = QtWidgets.QWidget(self.controls)
        self.controls.buttons.setLayout(QtWidgets.QHBoxLayout(self.controls.buttons))
        self.controls.buttons.layout().setContentsMargins(0, 0, 0, 0)
        self.controls.buttons.layout().setSpacing(0)
        self.controls.buttons.back = QtWidgets.QPushButton("󰁍", self.controls.buttons)
        self.controls.buttons.forward = QtWidgets.QPushButton("󰁔", self.controls.buttons)
        self.controls.buttons.reload = QtWidgets.QPushButton("󰑐", self.controls.buttons)
        self.controls.buttons.url = QtWidgets.QLineEdit(self.controls.buttons)
        self.controls.buttons.extension = QtWidgets.QPushButton(icon, "", self.controls.buttons)
        for widget in (
            self.controls.buttons.back,
            self.controls.buttons.forward,
            self.controls.buttons.reload,
            self.controls.buttons.url,
            self.controls.buttons.extension,
        ):
            self.controls.buttons.layout().addWidget(widget)
        if buttons:
            self.controls.layout().addWidget(self.controls.buttons)
        else:
            # Parented but never laid out, so showChildren() would still show it
            # and leave an invisible url bar reachable with Tab
            self.controls.buttons.setVisible(False)
        self.controls.progress = QtWidgets.QProgressBar(self.controls)
        self.controls.progress.setTextVisible(False)
        self.controls.progress.setFixedHeight(2)
        self.controls.progress.setMaximum(100)
        self.controls.layout().addWidget(self.controls.progress)

        # Nav controls act on whichever tab is current
        self.controls.buttons.back.clicked.connect(lambda _=None: self.current_tab.view.back())
        self.controls.buttons.forward.clicked.connect(lambda _=None: self.current_tab.view.forward())
        self.controls.buttons.reload.clicked.connect(lambda _=None: self.current_tab.reload())
        self.controls.buttons.url.returnPressed.connect(
            lambda: self.current_tab.load(self.controls.buttons.url.text())
        )
        if extension:
            self.controls.buttons.extension.clicked.connect(
                lambda _=None: self.current_tab.page.runJavaScript(
                    f"addGame({self.current_tab.view.url().url()!r});"
                )
            )
        else:
            self.controls.buttons.extension.setVisible(False)

        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabBar().setVisible(False)  # shown once a second tab exists
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        # Every index in here is a tab bar index, so dragging a tab has to move
        # tab_list with it or current_tab and close_tab start hitting the wrong one
        self.tabs.tabBar().tabMoved.connect(
            lambda frm, to: self.tab_list.insert(to, self.tab_list.pop(frm))
        )

        if buttons:
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
        self.tabs.tabBar().setVisible(self.buttons_enabled and len(self.tab_list) > 1)
        if not background:
            self.tabs.setCurrentIndex(index)
            # The chrome attaches to the window before any view does, so without
            # this the back button wins initial focus and the page gets no keys
            tab.view.setFocus()
        if url:
            tab.load(url)
        return tab

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
        self.tabs.tabBar().setVisible(self.buttons_enabled and len(self.tab_list) > 1)

    def next_tab(self):
        if len(self.tab_list) > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % len(self.tab_list))

    def tab_changed(self, _: int = -1):
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


def watch_stdin(window: BrowserWindow):
    # A thread, not QSocketNotifier: that one ignores non-socket handles on
    # Windows. Widgets are GUI-thread only, so hand the url over as a signal
    def reader():
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if url := message.get("open"):
                window.url_received.emit(url, message.get("cookies") or {})
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
        def interceptRequest(self, info):
            # Never block the top-level document: navigating *to* a blocked
            # host must fail normally (DNS/404/etc), not with a silent blank block
            if info.resourceType() is not main_frame and blocked(info.requestUrl().host(), hosts):
                info.block(True)

    return Blocker()


def install_blocker(window: BrowserWindow, blocklist_file: str | None, tabs: bool):
    # tabs is true only for the real shared browser (open()); cookies(),
    # css_redirect() and xpath_redirect() all pass tabs=False unconditionally,
    # and blocking a request there could stall the very redirect they are
    # waiting on, hanging forever with no error
    if not (blocklist_file and tabs):
        return
    if blocker := make_blocker(blocklist_file):
        window.blocker = blocker  # PyQt does not own it; a GC'd interceptor stops blocking
        window.profile.setUrlRequestInterceptor(blocker)


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
    install_blocker(app.window, blocklist_file, tabs)
    if size:
        app.window.resize(*size)
    if pos:
        app.window.move(*pos)

    def download_requested(download: QtWebEngineCore.QWebEngineDownloadRequest):
        old_path = pathlib.Path(download.downloadDirectory()) / download.downloadFileName()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(app.window, "Save File", str(old_path), "*" + old_path.suffix)
        if path:
            new_path = pathlib.Path(path)
            download.setDownloadDirectory(str(new_path.parent))
            download.setDownloadFileName(new_path.name)
            download.accept()
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
