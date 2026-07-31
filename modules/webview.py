import asyncio
import json
import re
import sys

from PyQt6 import (
    QtCore,
    QtNetwork,
)
from PyQt6.QtNetwork import QNetworkProxy

from modules.webview_window import (
    create,
    watch_stdin,
)

# Qt WebEngine doesn't like running alongside other OpenGL
# applications so we need to run a dedicated multiprocess

# Opening N selected games fires N tasks on the one async_thread loop, all
# suspending inside create_subprocess_exec, so the reuse check and the spawn
# have to be atomic or every one of them spawns its own window
browser_lock = asyncio.Lock()


async def start(action: str, *args, centered=True, use_f95_cookies=True, pipe=False, **kwargs):
    import contextlib
    import imgui
    import shlex
    import subprocess
    from common.structs import (
        DaemonPipe,
        DaemonProcess,
    )
    from modules import (
        api,
        globals,
    )

    if use_f95_cookies:
        kwargs["cookies"] = globals.cookies
        kwargs["cookies_domain"] = api.f95_domain

    if centered and (size := kwargs.get("size")):
        kwargs["pos"] = (
            int(globals.gui.screen_pos[0] + (imgui.io.display_size.x / 2) - size[0] / 2),
            int(globals.gui.screen_pos[1] + (imgui.io.display_size.y / 2) - size[1] / 2),
        )

    args = [action, *args]
    kwargs = create_kwargs() | kwargs

    # Browsing shares one process, one window: later opens become tabs. The
    # one-shot windows (login, DDL resolvers) keep spawning their own process
    reuse = action == "open"

    async with (browser_lock if reuse else contextlib.nullcontext()):
        if reuse and globals.browser_daemon:
            try:
                globals.browser_daemon.put({"open": args[1], "cookies": kwargs.get("cookies")})
                return None
            except DaemonPipe.DaemonPipeExit:
                globals.browser_daemon = None

        proc = await asyncio.create_subprocess_exec(
            *shlex.split(globals.start_cmd),
            "webview-daemon",
            json.dumps(args),
            json.dumps(kwargs),
            stdin=(subprocess.PIPE if reuse else None),
            stdout=(subprocess.PIPE if pipe else None),
        )

        if pipe:
            return DaemonPipe(proc)
        elif reuse:
            globals.browser_daemon = DaemonPipe(proc)
            return proc
        else:
            DaemonProcess(proc)
            return proc


def create_kwargs():
    from common.structs import ProxyType
    from modules import (
        colors,
        globals,
        icons,
    )
    from modules.blocklist import blocklist_path

    if globals.settings.proxy_type is ProxyType.Disabled:
        proxy_config = None
    else:
        proxy_type = QNetworkProxy.ProxyType.NoProxy
        match globals.settings.proxy_type:
            case ProxyType.SOCKS4:
                print("SOCKS4 proxy is not supported by Qt", file=sys.stderr)
                proxy_type = QNetworkProxy.ProxyType.NoProxy
            case ProxyType.SOCKS5: proxy_type = QNetworkProxy.ProxyType.Socks5Proxy
            case ProxyType.HTTP: proxy_type = QNetworkProxy.ProxyType.HttpProxy
        proxy_config = {
            "type": proxy_type.name,
            "host": globals.settings.proxy_host,
            "port": globals.settings.proxy_port,
            "username": globals.settings.proxy_username,
            "password": globals.settings.proxy_password,
        }

    return dict(
        debug=globals.debug,
        software=globals.settings.software_webview,
        private=globals.settings.browser_private,
        icon=str(globals.self_path / "resources/icons/icon.png"),
        icon_font=str(icons.font_path),
        extension=str(globals.self_path / "browser/integrated.js"),
        style_bg=colors.rgba_0_1_to_hex(globals.settings.style_bg)[:-2],
        style_accent=colors.rgba_0_1_to_hex(globals.settings.style_accent)[:-2],
        style_text=colors.rgba_0_1_to_hex(globals.settings.style_text)[:-2],
        style_text_dim=colors.rgba_0_1_to_hex(globals.settings.style_text_dim)[:-2],
        style_corner_radius=f"{globals.settings.style_corner_radius}px",
        proxy_config=proxy_config,
        # A str, never a Path: this dict crosses the process boundary as JSON
        blocklist_file=(
            str(blocklist_path()) if globals.settings.browser_adblock else None
        ),
        download_manager=(
            globals.settings.download_manager_executable,
            globals.settings.download_manager_arguments,
        ),
    )


def open(url: str, *, cookies: dict[str, str] = {}, cookies_domain: str = None, **kwargs):
    app = create(**kwargs)
    # The store belongs to the profile, not to a tab, and so does the domain the
    # spawn was given: later opens must land in the same place as the first one
    store = app.window.profile.cookieStore()
    domain = QtCore.QUrl("https://" + cookies_domain) if cookies_domain else None
    def set_cookies(cookies: dict[str, str]):
        if not (cookies and domain):
            return
        for key, value in cookies.items():
            store.setCookie(QtNetwork.QNetworkCookie(QtCore.QByteArray(key.encode()), QtCore.QByteArray(value.encode())), domain)
    set_cookies(cookies)
    app.window.webview.setUrl(QtCore.QUrl(url))
    # Later opens arrive on stdin instead of spawning another browser. They carry
    # their own cookies: this window outlives a sidebar login, and reapplying them
    # is exactly what a freshly spawned process used to do
    def open_tab(url: str, cookies: dict[str, str]):
        set_cookies(cookies)
        app.window.new_tab(url)
        app.window.raise_()
        app.window.activateWindow()
    app.window.url_received.connect(open_tab)
    watch_stdin(app.window)
    app.window.show()
    app.exec()


def cookies(url: str, *, minimal=True, **kwargs):
    if minimal:
        kwargs |= dict(
            buttons=False,
            extension=False,
            private=True,
        )
    app = create(**kwargs | dict(buttons=False, extension=False, private=True, tabs=False))
    url = QtCore.QUrl(url)
    def on_cookie_add(cookie: QtNetwork.QNetworkCookie):
        name = cookie.name().data().decode('utf-8')
        value = cookie.value().data().decode('utf-8')
        app.pipe.put((name, value))
    app.window.webview.cookieStore.cookieAdded.connect(on_cookie_add)
    app.window.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    app.window.webview.setUrl(url)
    app.window.show()
    app.exec()


def css_redirect(url: str, css_selector: str = None, *, minimal=True, cookies: dict[str, str] = {}, cookies_domain: str = None, **kwargs):
    if minimal:
        kwargs |= dict(
            buttons=False,
            extension=False,
            private=True,
        )
    # tabs=False unconditionally: this is a resolver, not a browser, whatever
    # minimal says. A click-through that opens a popup has to land in this view,
    # or urlChanged never fires here and pipe.get_async() waits forever
    app = create(**kwargs | dict(tabs=False))
    # Bound once, never re-resolved, so no tab can move it out from under these
    # callbacks: injecting the click-through into the wrong page, or disconnecting
    # a slot that was never connected to it
    webview = app.window.webview
    url = QtCore.QUrl(url)
    if cookies and cookies_domain:
        cookies_domain = QtCore.QUrl("https://" + cookies_domain)
        for key, value in cookies.items():
            webview.cookieStore.setCookie(QtNetwork.QNetworkCookie(QtCore.QByteArray(key.encode()), QtCore.QByteArray(value.encode())), cookies_domain)
    def url_changed(new: QtCore.QUrl):
        if new.host() != url.host():
            app.pipe.put(new.url())
            nonlocal css_selector
            if css_selector:
                css_selector = None
                webview.loadProgress.disconnect(load_progress)
    webview.urlChanged.connect(url_changed)
    if css_selector:
        def load_progress(_):
            webview.page.runJavaScript(f"""
                redirectClickElement = document.querySelector({css_selector!r});
                if (redirectClickElement) {{
                    redirectClickElement.click();
                }}
            """)
        webview.loadProgress.connect(load_progress)
    app.window.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    webview.setUrl(url)
    app.window.show()
    app.exec()


def xpath_redirect(url: str, xpath_expression: str = None, *, minimal=True, cookies: dict[str, str] = {}, cookies_domain: str = None, **kwargs):
    if minimal:
        kwargs |= dict(
            buttons=False,
            extension=False,
            private=True,
        )
    app = create(**kwargs | dict(tabs=False))
    # Bound once, never re-resolved - see css_redirect
    webview = app.window.webview
    url = QtCore.QUrl(url)
    if cookies and cookies_domain:
        cookies_domain = QtCore.QUrl("https://" + cookies_domain)
        for key, value in cookies.items():
            webview.cookieStore.setCookie(QtNetwork.QNetworkCookie(QtCore.QByteArray(key.encode()), QtCore.QByteArray(value.encode())), cookies_domain)
    def url_changed(new: QtCore.QUrl):
        if new.host() != url.host():
            app.pipe.put(new.url())
            nonlocal xpath_expression
            if xpath_expression:
                xpath_expression = None
                webview.loadProgress.disconnect(load_progress)
    webview.urlChanged.connect(url_changed)
    if xpath_expression:
        if index_match := re.search(r"\[(\d+)\]$", xpath_expression):
            xpath_index = int(index_match.group(1)) - 1
            xpath_expression = xpath_expression[:index_match.start()]
        else:
            xpath_index = 0
        def load_progress(_):
            webview.page.runJavaScript(f"""
                redirectClickElements = document.evaluate({xpath_expression!r}, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                if (redirectClickElements) {{
                    redirectClickElement = redirectClickElements.snapshotItem({xpath_index})
                    if (redirectClickElement) {{
                        redirectClickElement.click();
                    }}
                }}
            """)
        webview.loadProgress.connect(load_progress)
    app.window.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    webview.setUrl(url)
    app.window.show()
    app.exec()
