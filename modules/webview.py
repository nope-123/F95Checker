import json
import re
import sys

from PyQt6 import (
    QtCore,
    QtNetwork,
)
from PyQt6.QtNetwork import QNetworkProxy

from modules.webview_window import create

# Qt WebEngine doesn't like running alongside other OpenGL
# applications so we need to run a dedicated multiprocess


async def start(action: str, *args, centered=True, use_f95_cookies=True, pipe=False, **kwargs):
    import asyncio
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

    proc = await asyncio.create_subprocess_exec(
        *shlex.split(globals.start_cmd),
        "webview-daemon",
        json.dumps(args),
        json.dumps(kwargs),
        stdout=(subprocess.PIPE if pipe else None),
    )

    if pipe:
        return DaemonPipe(proc)
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
    )


def open(url: str, *, cookies: dict[str, str] = {}, cookies_domain: str = None, **kwargs):
    app = create(**kwargs)
    url = QtCore.QUrl(url)
    if cookies and cookies_domain:
        cookies_domain = QtCore.QUrl("https://" + cookies_domain)
        for key, value in cookies.items():
            app.window.webview.cookieStore.setCookie(QtNetwork.QNetworkCookie(QtCore.QByteArray(key.encode()), QtCore.QByteArray(value.encode())), cookies_domain)
    app.window.webview.setUrl(url)
    app.window.show()
    app.exec()


def cookies(url: str, *, minimal=True, **kwargs):
    if minimal:
        kwargs |= dict(
            buttons=False,
            extension=False,
            private=True,
        )
    app = create(**kwargs | dict(buttons=False, extension=False, private=True))
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
    app = create(**kwargs)
    # Bound once, never re-resolved: minimal=False keeps the chrome, where a popup
    # opens a tab and would otherwise move app.window.webview out from under these
    # callbacks - injecting the click-through into the popup and disconnecting a
    # slot that was never connected to it
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
    app = create(**kwargs)
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
