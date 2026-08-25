# Built-in Browser: Tabs, Adblock, Download Handoff — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn F95Checker's built-in Qt WebEngine browser into a usable browser — one shared window with tabs, working clipboard access, HaGeZi Pro ad blocking, and downloads handed to an external download manager such as IDM.

**Architecture:** All work is confined to the webview subprocess plus three new settings. `modules/webview.py` splits into a process layer (`webview.py`) and a Qt UI layer (`webview_window.py`), where a `WebTab` owns one view and its signal wiring while a `BrowserWindow` owns the chrome. The full browser becomes a single lazily-spawned process that receives further URLs over the existing stdin JSON-line pipe. Ad blocking is a hostname set consulted by a `QWebEngineUrlRequestInterceptor`; downloads shell out to a user-configured executable via argv.

**Tech Stack:** Python 3.11+, PyQt6 / PyQt6-WebEngine, aiohttp, aiosqlite, imgui (bundled `imgui[glfw]` fork). Spec: `docs/superpowers/specs/2026-07-30-webview-tabs-adblock-downloads-design.md`.

## Global Constraints

- **Branch before the first commit.** The repo's default branch is `master` and there are nine commits below. Create a feature branch (or an isolated worktree via `superpowers:using-git-worktrees`) first; do not commit directly to `master`.
- **No new dependencies.** Everything here uses PyQt6, aiohttp and the stdlib, all already pinned in `requirements.txt`.
- **API floor is PyQt6 6.4.** Windows/Linux run PyQt6 6.7.1 / PyQt6-WebEngine 6.7.0; macOS runs 6.4.2 / 6.4.0. Every API used exists in 6.4. Do not raise the pins.
- **No test framework.** The project has no `tests/`, no pytest, and `requirements-dev.txt` contains only cx-Freeze and setuptools. The one check in this plan is a stdlib `assert` script run with `python test_blocklist.py`. Do not add pytest.
- **No dev environment is installed** — no PyQt6, no aiohttp, no imgui; the interpreter on PATH is Python 3.14, for which the pinned `imgui==2.0.0` and `PyQt6==6.7.1` have no wheels. Consequences: (1) `modules/blocklist.py` must stay importable with stdlib only, which is why `python test_blocklist.py` is the one runnable check; (2) **you cannot run the app.** Every step below that says to launch F95Checker or click through the browser is the user's to perform. Do not attempt it, do not install dependencies, and never report a GUI check as passing. Report those steps as "requires user verification" and list exactly what the user should look for.
- **Never commit documentation.** User preference: `docs/superpowers/**` stays out of every commit. Add only the files each step names.
- **Follow existing patterns.** Settings, DB columns, subprocess launching and filepickers all have established idioms in this codebase; each task points at the exact lines to copy.
- **The webview runs as a separate OS process.** `main.py:74-79` dispatches `webview-daemon` before `main()`. Code in the child cannot see `globals.settings`; everything it needs crosses via `create_kwargs()` (`modules/webview.py:107-120`).
- **Blocklist URL** (exact): `https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/pro-onlydomains.txt`
- **`QWebEngineUrlRequestInterceptor` runs on every request.** Keep `blocked()` allocation-free and never do I/O in it.

---

### Task 1: Settings and schema

Adds the three settings everything else reads. Nothing behavioural yet — after this task the options exist, persist across restarts, and do nothing.

**Files:**
- Modify: `common/structs.py:820-835` (the `Settings` dataclass)
- Modify: `modules/db.py:197-212` (the `settings` table columns)
- Modify: `modules/gui.py:4292-4297` (the Browser settings section)

**Interfaces:**
- Consumes: nothing.
- Produces: `globals.settings.browser_adblock: bool`, `globals.settings.download_manager_executable: str`, `globals.settings.download_manager_arguments: str`.

Field and column names must match exactly — `row_to_cls` (`modules/db.py:478`) maps columns to dataclass fields by name. Both lists are alphabetical; note `download_manager_*` sorts *before* `downloads_dir` because `_` (0x5F) precedes `s` (0x73).

- [ ] **Step 1: Add the dataclass fields**

In `common/structs.py`, in the `Settings` dataclass. Insert `browser_adblock` immediately after `browser` (currently line 820), and the two download-manager fields immediately before `downloads_dir` (currently line 835):

```python
    browser                     : Browser.get
    browser_adblock             : bool
    browser_custom_arguments    : str
```

```python
    display_tab                 : Tab.get
    download_manager_arguments  : str
    download_manager_executable : str
    downloads_dir               : dict[Os, str]
```

- [ ] **Step 2: Add the table columns**

In `modules/db.py`, in the `columns` dict of the `settings` `create_table` call. `create_table` (`modules/db.py:93-96`) issues `ALTER TABLE ... ADD COLUMN` for any column missing from an existing DB, so this is the entire migration:

```python
            "browser_adblock":             f'INTEGER DEFAULT {int(True)}',
```

placed after `"bg_refresh_interval"`, and:

```python
            "download_manager_arguments":  f'TEXT    DEFAULT "/d {{url}} /n"',
            "download_manager_executable": f'TEXT    DEFAULT ""',
```

placed after `"display_tab"`.

The doubled braces in `/d {{url}} /n` are required — this is an f-string, and a single `{url}` would raise `KeyError`. The stored default must come out as the literal `/d {url} /n`.

- [ ] **Step 3: Verify the migration and defaults**

Run the app against your existing database:

```bash
python main.py
```

Then confirm the columns exist with the intended defaults:

```bash
python -c "import sqlite3,os; d=os.path.expandvars(r'%APPDATA%\f95checker\db.sqlite3'); c=sqlite3.connect(d); print([r for r in c.execute('SELECT browser_adblock, download_manager_executable, download_manager_arguments FROM settings')])"
```

Expected: `[(1, '', '/d {url} /n')]`

- [ ] **Step 4: Add the adblock checkbox**

In `modules/gui.py`, in the Browser settings section. Insert after the `software_webview` block that currently ends at line 4297, before the `copy_urls_as_bbcode` label:

```python
            draw_settings_label(
                "Block ads:",
                "Blocks ads, trackers and malware domains in the integrated browser, using HaGeZi's Pro DNS blocklist. "
                "The list is downloaded in the background and refreshed weekly."
            )
            draw_settings_checkbox("browser_adblock")
```

- [ ] **Step 5: Add the download manager configure popup**

Still in `modules/gui.py`, immediately after the block from Step 4. This mirrors the custom-browser popup at `modules/gui.py:4231-4272` — same text input, same `filepicker` browse button, same right-click context menu:

```python
            draw_settings_label(
                "Download manager:",
                "Hand downloads from the integrated browser to an external download manager instead of saving them "
                "directly. Leave the executable empty to save downloads normally. Use {url} in the arguments where the "
                "download link should go. For IDM on Windows, browse to IDMan.exe and keep the default arguments."
            )
            if imgui.button("Configure", width=right_width):
                def popup_content():
                    imgui.text("Executable: ")
                    imgui.same_line()
                    pos = imgui.get_cursor_pos_x()
                    changed, value = imgui.input_text("###download_manager_executable", set.download_manager_executable)
                    setter_extra = lambda _=None: async_thread.run(db.update_settings("download_manager_executable"))
                    if changed:
                        set.download_manager_executable = value
                        setter_extra()
                    if imgui.begin_popup_context_item("###download_manager_executable_context"):
                        utils.text_context(set, "download_manager_executable", setter_extra, no_icons=True)
                        imgui.end_popup()
                    imgui.same_line()
                    clicked = imgui.button(icons.folder_open_outline)
                    imgui.same_line(spacing=0)
                    args_width = imgui.get_cursor_pos_x() - pos
                    imgui.dummy(0, 0)
                    if clicked:
                        def callback(selected: str):
                            if selected:
                                set.download_manager_executable = selected
                                async_thread.run(db.update_settings("download_manager_executable"))
                        utils.push_popup(filepicker.FilePicker(
                            title="Select or drop download manager executable",
                            start_dir=set.download_manager_executable,
                            callback=callback
                        ).tick)
                    imgui.text("Arguments: ")
                    imgui.same_line()
                    imgui.set_cursor_pos_x(pos)
                    imgui.set_next_item_width(args_width)
                    changed, value = imgui.input_text("###download_manager_arguments", set.download_manager_arguments)
                    setter_extra = lambda _=None: async_thread.run(db.update_settings("download_manager_arguments"))
                    if changed:
                        set.download_manager_arguments = value
                        setter_extra()
                    if imgui.begin_popup_context_item("###download_manager_arguments_context"):
                        utils.text_context(set, "download_manager_arguments", setter_extra, no_icons=True)
                        imgui.end_popup()
                utils.push_popup(
                    utils.popup, "Configure download manager",
                    popup_content,
                    buttons=True,
                    closable=True,
                    outside=False
                )
```

- [ ] **Step 6: Verify the UI**

Run `python main.py`, open Settings > Browser. Confirm:
- A "Block ads:" checkbox, enabled by default, with the tooltip on hover.
- A "Download manager:" row with a Configure button; the popup shows both inputs and the folder icon; the arguments field pre-filled with `/d {url} /n`.
- Toggle the checkbox off, set an executable path, restart the app, and confirm both persisted.

- [ ] **Step 7: Commit**

```bash
git add common/structs.py modules/db.py modules/gui.py
git commit -m "feat: add adblock and download manager settings"
```

---

### Task 2: Blocklist parsing and matching

The only non-trivial pure logic in this feature, so it is the only thing with an automated check. Written test-first.

**Files:**
- Create: `modules/blocklist.py`
- Create: `test_blocklist.py` (project root, alongside `tags-diff.py` and `main-debug.py`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `blocklist.BLOCKLIST_URL: str`
  - `blocklist.parse_blocklist(text: str) -> set[str]`
  - `blocklist.blocked(host: str, hosts: set[str]) -> bool`

**Why its own module.** `modules/blocklist.py` must import nothing outside the stdlib at module scope. `modules/webview.py` imports PyQt6 at module level, so putting these functions there would make the check unrunnable without a full dev environment installed. Keeping the blocklist concern in one dependency-free module means `python test_blocklist.py` runs on a bare Python install. Task 3 adds `ensure_blocklist()` here too; Task 8's `Blocker` imports from here.

Any import of `api` or `globals` inside this module must be function-local (the established style in this codebase — see `modules/webview.py:81-87`), so importing the module never pulls in the app.

- [ ] **Step 1: Write the failing check**

Create `test_blocklist.py`:

```python
#!/usr/bin/env python
# Stdlib-only self-check, needs no installed dependencies.
# Run: python test_blocklist.py
from modules.blocklist import blocked, parse_blocklist

LIST = """\
# Title: HaGeZi's Pro DNS Blocklist
# Number of entries: 3
#
ads.example.com
tracker.net
boredcrown.com
"""


def test_parse_blocklist():
    hosts = parse_blocklist(LIST)
    assert hosts == {"ads.example.com", "tracker.net", "boredcrown.com"}, hosts
    assert not any(h.startswith("#") for h in hosts), "comment lines leaked in"
    assert "" not in hosts, "blank line leaked in"


def test_blocked():
    hosts = parse_blocklist(LIST)
    # listed domains and any depth of subdomain
    assert blocked("ads.example.com", hosts)
    assert blocked("a.b.ads.example.com", hosts)
    assert blocked("cdn.tracker.net", hosts)
    # not listed
    assert not blocked("f95zone.to", hosts)
    # parent of a listed domain must not be blocked
    assert not blocked("example.com", hosts)
    # a suffix match is not a subdomain match
    assert not blocked("notads.example.com", hosts)
    # a listed name appearing as a leading label is not a match
    assert not blocked("ads.example.com.evil.tld", hosts)
    # degenerate hosts
    assert not blocked("localhost", hosts)
    assert not blocked("", hosts)
    # a malformed bare-TLD entry must never black out the web
    assert not blocked("example.com", {"com"})


if __name__ == "__main__":
    test_parse_blocklist()
    test_blocked()
    print("ok")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python test_blocklist.py
```

Expected: `ModuleNotFoundError: No module named 'modules.blocklist'`

- [ ] **Step 3: Implement the two functions**

Create `modules/blocklist.py` with no module-scope imports at all:

```python
BLOCKLIST_URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/pro-onlydomains.txt"


def parse_blocklist(text: str):
    return {line for line in text.splitlines() if line and line[0] != "#"}


def blocked(host: str, hosts: set[str]):
    # The list holds base domains, so walk up the labels. The "." condition is
    # also the safety rail: a bare TLD entry can never match anything.
    while "." in host:
        if host in hosts:
            return True
        host = host.partition(".")[2]
    return False
```

- [ ] **Step 4: Run it to verify it passes**

```bash
python test_blocklist.py
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add modules/blocklist.py test_blocklist.py
git commit -m "feat: add blocklist parsing and subdomain matching"
```

---

### Task 3: Blocklist download and startup preload

Fetches and caches the list in the main process, preloaded at app startup so it is ready before the first browser window.

**Files:**
- Modify: `modules/blocklist.py` (add after `blocked()` from Task 2)
- Modify: `main.py:27-30`
- Modify: `modules/gui.py` (the `browser_adblock` checkbox from Task 1, Step 4)

**Interfaces:**
- Consumes: `blocklist.BLOCKLIST_URL`, `globals.settings.browser_adblock`.
- Produces: `blocklist.blocklist_path() -> pathlib.Path` and `async blocklist.ensure_blocklist() -> None`; the cached file at `globals.data_path / "blocklist.txt"`.

- [ ] **Step 1: Implement `blocklist_path` and `ensure_blocklist`**

In `modules/blocklist.py`, after `blocked()`. Add `import time` at module scope — stdlib only, so the dependency-free property holds:

```python
def blocklist_path():
    from modules import globals
    return globals.data_path / "blocklist.txt"


async def ensure_blocklist():
    from modules import (
        api,
        globals,
    )
    if not globals.settings.browser_adblock:
        return
    path = blocklist_path()
    if path.is_file() and time.time() - path.stat().st_mtime < 7 * 86400:
        return
    try:
        # cookies=False is mandatory: api.request defaults to attaching
        # globals.cookies, which would leak F95zone session cookies to GitHub.
        # The explicit timeout overrides request_timeout, tuned for small calls.
        data = await api.fetch("GET", BLOCKLIST_URL, cookies=False, timeout=120)
        if data:
            path.write_bytes(data)
    except Exception:
        pass  # a nicety, never surface and never block
```

The imports are function-local to match the existing style in this codebase (see `create_kwargs`, `modules/webview.py:81-87`), to avoid an import cycle with `api`, and — critically — to keep `import modules.blocklist` free of third-party dependencies so `test_blocklist.py` runs anywhere.

`blocklist_path()` exists so the path is written once; Task 8's `create_kwargs()` change calls it rather than rebuilding the string.

- [ ] **Step 2: Preload at startup**

In `main.py`, inside `main()`. The call goes inside the `with` block so `db.setup()` has loaded settings and `api.setup()` has built the session:

```python
    from modules import api, db
    with db.setup(), api.setup():

        from modules import blocklist
        async_thread.run(blocklist.ensure_blocklist())

        from modules import gui
        globals.gui = gui.MainGUI()
```

Fire-and-forget: never awaited, so it cannot delay the window.

- [ ] **Step 3: Refresh when the setting is enabled**

In `modules/gui.py`, change the checkbox line from Task 1 Step 4 to use the return value. `draw_settings_checkbox` already returns `changed` (`modules/gui.py:3936-3942`), so no helper changes are needed:

```python
            if draw_settings_checkbox("browser_adblock"):
                async_thread.run(blocklist.ensure_blocklist())
```

Add `blocklist` to the existing `from modules import (...)` block at the top of `modules/gui.py`.

- [ ] **Step 4: Verify the download**

Delete any cached list, then start the app and wait a few seconds:

```bash
python -c "import os,pathlib; p=pathlib.Path(os.path.expandvars(r'%APPDATA%\f95checker'))/'blocklist.txt'; p.unlink(missing_ok=True); print('deleted')"
python main.py
```

In another shell, confirm the file arrived and parses:

```bash
python -c "import os,pathlib; from modules.blocklist import parse_blocklist; p=pathlib.Path(os.path.expandvars(r'%APPDATA%\f95checker'))/'blocklist.txt'; h=parse_blocklist(p.read_text(encoding='utf-8')); print(len(h), 'domains')"
```

Expected: roughly `220000 domains` (the list grows over time; anything above 200000 is healthy).

Then verify the guards:
- Turn "Block ads" off, delete the file, restart — the file must **not** reappear.
- With it still off, toggle it on in Settings — the file must appear within seconds, no restart.
- Restart with the file present and fresh; confirm no new download (file mtime unchanged).

- [ ] **Step 5: Commit**

```bash
git add modules/blocklist.py modules/gui.py main.py
git commit -m "feat: download and preload adblock blocklist at startup"
```

---

### Task 4: Extract `webview_window.py`

A pure move with no behaviour change, isolated as its own task so a reviewer can confirm "nothing changed" before any restructuring lands. Doing this in the same commit as Task 5 would make the real changes invisible in the diff.

**Files:**
- Create: `modules/webview_window.py`
- Modify: `modules/webview.py:123-394` (remove `create`), and the four entry points that call it

**Interfaces:**
- Consumes: nothing new.
- Produces: `webview_window.create(**kwargs) -> QtWidgets.QApplication`, with the identical signature and return value that `webview.create` has today.

- [ ] **Step 1: Move `create` verbatim**

Create `modules/webview_window.py`. Move the entire `create()` function (`modules/webview.py:123-394`) into it unchanged, along with the imports it needs at module level:

```python
import base64
import json
import pathlib
import sys

from PyQt6 import (
    QtCore,
    QtGui,
    QtNetwork,
    QtWebChannel,
    QtWidgets,
)
from PyQt6.QtNetwork import QNetworkProxy

from common.structs import ChildPipe
from modules.webview import config_qt_flags
```

`QNetworkProxy` is imported in both modules — `webview.py` still needs it in `create_kwargs()` (`modules/webview.py:92-98`), and `webview_window.py` needs it to apply the proxy.

Keep the lazy `from PyQt6 import QtWebEngineCore, QtWebEngineWidgets` **inside** `create()` where it already is (`modules/webview.py:155-158`). That laziness is deliberate — importing QtWebEngine at module scope in the main process is what the "Qt WebEngine doesn't like running alongside other OpenGL applications" comment is guarding against.

- [ ] **Step 2: Import it back into `webview.py`**

In `modules/webview.py`, delete `create()` and the now-unused imports it took with it (`base64`, `pathlib`, `QtGui`, `QtWebChannel`, `QtWidgets`). Add at the bottom of the imports:

```python
from modules.webview_window import create
```

`open()`, `cookies()`, `css_redirect()` and `xpath_redirect()` keep calling `create(**kwargs)` unchanged.

- [ ] **Step 3: Verify all four entry points still work**

There is no automated coverage for Qt UI here; exercise each path by hand. Set Settings > Browser > Browser to "Integrated", then:

1. **`open`** — click a game's thread link. The browser window opens, loads the thread, back/forward/reload and the URL bar work, and the F95Checker icon button adds the game.
2. **`cookies`** — log out, then log in via the sidebar. The login window opens and the login completes.
3. **`css_redirect` / `xpath_redirect`** — on a game with an F95zone Donor DDL or RPDL link, start a download so the resolver window opens and redirects.

Expected: behaviour identical to before this task. If anything differs, this step is wrong — it is a pure move.

- [ ] **Step 4: Commit**

```bash
git add modules/webview.py modules/webview_window.py
git commit -m "refactor: extract webview window into its own module"
```

---

### Task 5: `WebTab` and clipboard access

Restructures the moved `create()` so per-view wiring lives on an object, and hoists the profile to window scope. Still exactly one tab and no tab bar — behaviour is unchanged except that the clipboard starts working.

**Files:**
- Modify: `modules/webview_window.py`

**Interfaces:**
- Consumes: `webview_window.create` from Task 4.
- Produces:
  - `class WebTab` with `.view`, `.page`, `.is_current`, and `.load(url: str)`
  - `class BrowserWindow(QtWidgets.QWidget)` with `.tabs`, `.controls`, `.profile`, `.current_tab`, `.webview` (property), `.new_tab(url: str = None, background: bool = False) -> WebTab`
  - `create()` keeps its signature and still returns the `QApplication` with `app.window` set

`BrowserWindow.webview` must be a property returning `self.current_tab.view`. That is what lets `cookies()`, `css_redirect()` and `xpath_redirect()` keep poking `app.window.webview.<...>` with zero changes — they only ever have one tab. Preserve the attribute-stuffing the current code does (`.page`, `.history`, `.profile`, `.settings`, `.cookieStore` assigned onto the view object) so those call sites keep working.

- [ ] **Step 1: Create the `WebTab` class**

In `modules/webview_window.py`, above `create()`. This is the per-view wiring lifted out of `create()` — the signal handlers from `modules/webview.py:281-329`, the extension injection from `:230-277`, and the settings from `:218-220`:

```python
class WebTab:
    def __init__(self, window, extension: str, icon: QtGui.QIcon):
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
        if window.proxy_auth:
            self.page.proxyAuthenticationRequired.connect(self.proxy_authenticate)
        if extension:
            # One channel per page, all sharing the window's single RPCProxy
            self.channel = QtWebChannel.QWebChannel(self.view)
            self.channel.registerObject("rpcproxy", window.rpcproxy)
            self.page.setWebChannel(self.channel)
            self.view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            self.view.customContextMenuRequested.connect(self.context_menu)

    def proxy_authenticate(self, _url, authenticator, _host):
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
```

`sync_controls` and `set_progress` both ignore non-current tabs, so a background tab loading cannot touch the chrome. This preserves the old single-view progress behaviour exactly (`modules/webview.py:281-312`) while making it tab-aware.

- [ ] **Step 2: Create the `BrowserWindow` class**

Still in `modules/webview_window.py`. This is the chrome from `modules/webview.py:163-199`, the stylesheet from `:344-389`, and the guard logic that replaces the old direct references:

```python
class BrowserWindow(QtWidgets.QWidget):

    def __init__(
        self, *,
        buttons: bool,
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

        # Chrome, moved verbatim from the old create() (modules/webview.py:174-199)
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
        self.tabs.currentChanged.connect(lambda _: self.sync_controls())
        self.tabs.tabCloseRequested.connect(self.close_tab)

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
        if url:
            tab.load(url)
        return tab

    def close_tab(self, index: int):
        if len(self.tab_list) <= 1:
            self.close()
            return
        tab = self.tab_list.pop(index)
        self.tabs.removeTab(index)
        tab.page.deleteLater()
        self.tabs.tabBar().setVisible(self.buttons_enabled and len(self.tab_list) > 1)

    def tab_title_changed(self, tab, title: str):
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

    def set_progress(self, tab, value: int):
        if tab is not self.current_tab:
            return  # a background tab must never drive the chrome
        self.controls.progress.setValue(value)
        self.controls.progress.repaint()

    def closeEvent(self, close: QtGui.QCloseEvent):
        close.accept()
        for tab in self.tab_list:
            tab.page.deleteLater()
```

`title_fixed` replicates the existing either/or at `modules/webview.py:223-228`: an explicit `title` kwarg pins the window title, otherwise it tracks the active tab's page title.

- [ ] **Step 3: Rewrite `create()` to assemble them**

First extract three module-level helpers, each holding code lifted unchanged out of the old `create()`:

```python
def apply_proxy(proxy_config: dict | None):
    # modules/webview.py:144-153
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
    # modules/webview.py:231-235
    qwebchanneljsfile = QtCore.QFile(":/qtwebchannel/qwebchannel.js")
    qwebchanneljsfile.open(QtCore.QFile.OpenModeFlag.ReadOnly)
    qwebchanneljs = qwebchanneljsfile.readAll().data().decode("utf-8")
    qwebchanneljsfile.close()
    return qwebchanneljs + pathlib.Path(path).read_text()


def make_rpcproxy():
    # modules/webview.py:236-258, unchanged
    from external import async_thread
    import aiohttp
    async_thread.setup()

    class RPCProxy(QtCore.QObject):
        __slots__ = ("session",)

        def __init__(self):
            super().__init__()
            self.session = aiohttp.ClientSession(
                loop=async_thread.loop,
                cookie_jar=aiohttp.DummyCookieJar(loop=async_thread.loop),
            )

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
```

Then `create()` becomes assembly only. Its signature is unchanged from `modules/webview.py:123-141`:

```python
def create(
    *,
    title: str = None,
    buttons: bool = True,
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
):
    config_qt_flags(debug, software)
    proxy_auth = apply_proxy(proxy_config)

    app = QtWidgets.QApplication(sys.argv)
    app.pipe = ChildPipe()
    icon_font = QtGui.QFontDatabase.applicationFontFamilies(
        QtGui.QFontDatabase.addApplicationFont(icon_font)
    )[0]
    icon = QtGui.QIcon(icon)

    if extension:
        extension = load_extension(extension)
        rpcproxy = make_rpcproxy()
    else:
        extension = ""
        rpcproxy = None

    app.window = BrowserWindow(
        buttons=buttons,
        private=private,
        icon=icon,
        background_color=QtGui.QColor(style_bg),
        extension=extension,
        rpcproxy=rpcproxy,
        proxy_auth=proxy_auth,
        title=title,
    )
    if size:
        app.window.resize(*size)
    if pos:
        app.window.move(*pos)

    def download_requested(download):
        # modules/webview.py:331-339, unchanged; Task 9 replaces this
        old_path = pathlib.Path(download.downloadDirectory()) / download.downloadFileName()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(app.window, "Save File", str(old_path), "*" + old_path.suffix)
        if path:
            new_path = pathlib.Path(path)
            download.setDownloadDirectory(str(new_path.parent))
            download.setDownloadFileName(new_path.name)
            download.accept()
    app.window.profile.downloadRequested.connect(download_requested)

    app.window.setStyleSheet(f"""
        ... the stylesheet from modules/webview.py:344-389, copied verbatim ...
    """)

    tab = app.window.new_tab()
    # Old hijack behaviour, preserved so this task changes nothing. Task 6
    # deletes these two lines and connects it inside WebTab.__init__ instead.
    tab.page.newWindowRequested.connect(
        lambda request: tab.view.setUrl(request.requestedUrl())
    )
    return app
```

Two things to be careful about. `app.window.setStyleSheet(...)` takes the f-string from `modules/webview.py:344-389` unchanged — copy it, do not retype it, since it interpolates five style variables and the icon font family. And `page.setBackgroundColor` is now done per-tab in `WebTab.__init__` from `window.background_color`, replacing `modules/webview.py:390`.

- [ ] **Step 4: Verify no regressions plus the clipboard fix**

Repeat the full Task 4 Step 3 checklist — all four entry points must behave exactly as before. Then verify the new behaviour:

1. Open an F95zone thread in the integrated browser.
2. Use a post's "Copy link" / share button. Expected: the link is actually on your clipboard (before this task, nothing happened).
3. Click into the reply editor and press Ctrl+V. Expected: it pastes.

- [ ] **Step 5: Commit**

```bash
git add modules/webview_window.py
git commit -m "refactor: per-tab view wiring, shared profile, enable clipboard access"
```

---

### Task 6: Tab bar, new-window handling and shortcuts

Turns the single-tab window into a real tabbed browser.

**Files:**
- Modify: `modules/webview_window.py`

**Interfaces:**
- Consumes: `BrowserWindow.new_tab`, `BrowserWindow.close_tab`, `WebTab.page` from Task 5.
- Produces: no new public surface; `newWindowRequested` now opens tabs.

- [ ] **Step 1: Replace the new-window hijack**

Delete the two `tab.page.newWindowRequested.connect(...)` lines at the end of `create()` — the placeholder Task 5 left in to preserve the old hijack behaviour (originally `modules/webview.py:340-342`, which threw the request away and reused the current view). Connect it per-tab in `WebTab.__init__` instead, beside the other signal connections:

```python
        self.page.newWindowRequested.connect(self.new_window_requested)
```

and on `WebTab`:

```python
    def new_window_requested(self, request):
        from PyQt6 import QtWebEngineCore
        background = request.destination() is QtWebEngineCore.QWebEngineNewWindowRequest.DestinationType.InNewBackgroundTab
        tab = self.window.new_tab(background=background)
        # openIn() preserves the opener relationship; setUrl() does not
        request.openIn(tab.page)
```

Middle-clicking a link already arrives as `InNewBackgroundTab`, so there is no separate middle-click path to write.

- [ ] **Step 2: Add the shortcuts**

In `BrowserWindow.__init__`, only when chrome is enabled — the minimal modes must not gain shortcuts:

```python
        if buttons:
            for keys, handler in (
                ("Ctrl+T", lambda: self.new_tab("about:blank")),
                ("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                ("Ctrl+Tab", self.next_tab),
            ):
                QtGui.QShortcut(QtGui.QKeySequence(keys), self).activated.connect(handler)
```

and:

```python
    def next_tab(self):
        if len(self.tab_list) > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % len(self.tab_list))
```

Explicit `QKeySequence("Ctrl+Tab")` rather than `StandardKey.NextChild`, whose binding varies by platform.

- [ ] **Step 3: Verify tab behaviour**

Open a thread in the integrated browser, then confirm each:

| Action | Expected |
|---|---|
| Middle-click a link | Opens a background tab; focus stays put; tab bar appears |
| Ctrl-click / a popup link | Opens a tab and switches to it |
| Ctrl+T | New blank tab, focused |
| Drag a tab | Reorders |
| Ctrl+Tab | Cycles forward, wrapping to the first |
| Close a tab via its × | Closes that tab; the right one stays selected |
| Ctrl+W with 2+ tabs | Closes current tab |
| Ctrl+W / × on the last tab | Closes the window |
| One tab only | Tab bar hidden |
| Log in via sidebar | Login window has no tab bar and no shortcuts |

Also confirm the URL bar and back/forward buttons track the *active* tab when switching, and that a background tab finishing loading does not overwrite the foreground tab's URL bar.

- [ ] **Step 4: Commit**

```bash
git add modules/webview_window.py
git commit -m "feat: tabs in the integrated browser"
```

---

### Task 7: One shared browser process

Makes every "open in browser" action land in the existing window as a new tab.

**Files:**
- Modify: `modules/globals.py:38-39` area (add the module global)
- Modify: `modules/webview.py:23-62` (`start`)
- Modify: `modules/webview_window.py` (child-side reader)

**Interfaces:**
- Consumes: `DaemonPipe` (`common/structs.py:100-142`), `BrowserWindow.new_tab`.
- Produces: `globals.browser_daemon: DaemonPipe | None`; the child accepts `{"open": url}` JSON lines on stdin.

The transport already exists: `DaemonPipe.put()` writes JSON lines to the child's stdin (`common/structs.py:124-127`).

- [ ] **Step 1: Add the global**

In `modules/globals.py`, beside the other module-level globals:

```python
browser_daemon = None
```

- [ ] **Step 2: Route to the running browser, or spawn one**

In `modules/webview.py`, in `start()`. Add the reuse check before the spawn, and pipe stdin:

```python
    args = [action, *args]
    kwargs = create_kwargs() | kwargs

    from common.structs import DaemonPipe
    reuse = action == "open"
    if reuse and globals.browser_daemon:
        try:
            globals.browser_daemon.put({"open": args[1]})
            return None
        except DaemonPipe.DaemonPipeExit:
            globals.browser_daemon = None

    proc = await asyncio.create_subprocess_exec(
        *shlex.split(globals.start_cmd),
        "webview-daemon",
        json.dumps(args),
        json.dumps(kwargs),
        stdin=(subprocess.PIPE if (pipe or reuse) else None),
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
```

`DaemonPipe` holds its own `DaemonProcess`, so the child is still killed when the main process exits. Storing it in `globals` keeps it alive; `put()` raises `DaemonPipeExit` once the child is gone, which is what clears the stale handle.

`callbacks.open_webpage` (`modules/callbacks.py:398-408`) awaits `webview.start("open", url, size=...)` and ignores the return value, so returning `None` on the reuse path needs no caller change.

- [ ] **Step 3: Read URLs in the child**

In `modules/webview_window.py`, add one class attribute to `BrowserWindow`, directly under the `class` line and above `__init__` (Qt requires signals to be class attributes, not instance attributes):

```python
class BrowserWindow(QtWidgets.QWidget):
    url_received = QtCore.pyqtSignal(str)

    def __init__(
```

Then add the reader helper at module level in `webview_window.py`:

```python
def watch_stdin(window):
    import threading

    def reader():
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if url := message.get("open"):
                window.url_received.emit(url)

    threading.Thread(target=reader, daemon=True).start()
```

A thread rather than `QtCore.QSocketNotifier`, which does not work on non-socket handles on Windows. Emitting a signal is the safe way across threads — Qt queues it onto the GUI thread.

Connect it in `open()`:

```python
    app.window.url_received.connect(lambda url: app.window.new_tab(url))
    watch_stdin(app.window)
```

Only `open()` does this. `cookies()`, `css_redirect()` and `xpath_redirect()` must not — they are modal one-shots, and `cookies()` uses stdout for its own `app.pipe.put()` traffic.

- [ ] **Step 4: Verify the shared process**

1. From the library, open a game's thread in the integrated browser. Open two more. Expected: **one** window with three tabs, not three windows.
2. Confirm only one browser process exists:

```bash
python -c "import subprocess; print(subprocess.run(['tasklist','/fi','imagename eq python.exe'],capture_output=True,text=True).stdout)"
```

3. Close the browser window entirely, then open a thread again. Expected: a fresh window opens (the stale handle was detected and replaced).
4. Log in via the sidebar while the browser is open. Expected: a separate login window, not a tab.
5. Resolve a DDL link while the browser is open. Expected: a separate resolver window, not a tab.
6. Quit F95Checker with the browser open. Expected: the browser process exits too.

- [ ] **Step 5: Commit**

```bash
git add modules/globals.py modules/webview.py modules/webview_window.py
git commit -m "feat: route browser opens into one shared tabbed window"
```

---

### Task 8: Install the request blocker

**Files:**
- Modify: `modules/webview.py:107-120` (`create_kwargs`)
- Modify: `modules/webview_window.py`

**Interfaces:**
- Consumes: `blocklist.parse_blocklist`, `blocklist.blocked`, `blocklist.blocklist_path` (Tasks 2-3); the cached file (Task 3).
- Produces: `create()` accepts a new `blocklist_file: str | None` kwarg — the path to the cached list, or `None` when disabled.

- [ ] **Step 1: Pass the path to the child**

In `modules/webview.py`, in `create_kwargs()`. Add the function-local import beside the existing ones, then the dict entry:

```python
    from modules.blocklist import blocklist_path
```

```python
        blocklist_file=(
            str(blocklist_path()) if globals.settings.browser_adblock else None
        ),
```

Add `blocklist_file: str | None` to `create()`'s keyword-only parameters in `modules/webview_window.py`. The kwarg is named `blocklist_file`, not `blocklist`, so it cannot shadow the module name.

- [ ] **Step 2: Implement and install the interceptor**

In `modules/webview_window.py`:

```python
def make_blocker(path: str):
    from PyQt6 import QtWebEngineCore
    from modules.blocklist import blocked, parse_blocklist
    try:
        hosts = parse_blocklist(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None  # not downloaded yet; next window will have it
    if not hosts:
        return None
    main_frame = QtWebEngineCore.QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame

    class Blocker(QtWebEngineCore.QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            # Never block the top-level document: navigating *to* a blocked
            # host would fail with no explanation
            if info.resourceType() is not main_frame and blocked(info.requestUrl().host(), hosts):
                info.block(True)

    return Blocker()
```

Install it in `create()` immediately after the `BrowserWindow` is constructed, and **only** when chrome is enabled, so the DDL resolvers are never interfered with:

```python
    if blocklist_file and buttons:
        if blocker := make_blocker(blocklist_file):
            app.window.blocker = blocker  # keep a reference alive
            app.window.profile.setUrlRequestInterceptor(blocker)
```

Keeping the reference on the window matters — PyQt does not own it, and a garbage-collected interceptor takes the blocking with it.

- [ ] **Step 3: Verify blocking**

1. With "Block ads" on and the list downloaded, open an ad-heavy page in the integrated browser (any F95zone-linked file host works well). Expected: visibly fewer ads than the same page in your normal browser.
2. Confirm F95zone itself is unaffected: thread pages, images, spoilers and the reply editor all work.
3. Verify the MainFrame exception. Take a domain from the list:

```bash
python -c "import os,pathlib; p=pathlib.Path(os.path.expandvars(r'%APPDATA%\f95checker'))/'blocklist.txt'; print([l for l in p.read_text(encoding='utf-8').splitlines() if l and l[0]!='#'][0])"
```

Type that domain into the browser's URL bar and press Enter. Expected: the page attempts to load and fails normally (DNS/404/etc), **not** an instant blank block. If it blocks, the `ResourceTypeMainFrame` guard is wrong.
4. Turn "Block ads" off, open a new browser window, and confirm ads return.
5. Confirm a DDL resolver window still resolves correctly with blocking enabled elsewhere.

- [ ] **Step 4: Commit**

```bash
git add modules/webview.py modules/webview_window.py
git commit -m "feat: block ad and tracker requests in the integrated browser"
```

---

### Task 9: Download manager handoff

**Files:**
- Modify: `modules/webview.py:107-120` (`create_kwargs`)
- Modify: `modules/webview_window.py` (the `download_requested` handler)

**Interfaces:**
- Consumes: `globals.settings.download_manager_executable`, `globals.settings.download_manager_arguments` (Task 1).
- Produces: `create()` accepts `download_manager: tuple[str, str]` — `(executable, arguments)`.

- [ ] **Step 1: Pass the configuration to the child**

In `create_kwargs()`:

```python
        download_manager=(
            globals.settings.download_manager_executable,
            globals.settings.download_manager_arguments,
        ),
```

Add `download_manager: tuple[str, str]` to `create()`'s parameters.

- [ ] **Step 2: Rewrite the download handler**

In `modules/webview_window.py`, replacing the handler originally at `modules/webview.py:331-339`. Add `shlex` and `subprocess` to the module imports:

```python
    def save_dialog(download):
        old_path = pathlib.Path(download.downloadDirectory()) / download.downloadFileName()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(app.window, "Save File", str(old_path), "*" + old_path.suffix)
        if path:
            new_path = pathlib.Path(path)
            download.setDownloadDirectory(str(new_path.parent))
            download.setDownloadFileName(new_path.name)
            download.accept()

    def download_requested(download):
        executable, arguments = download_manager
        if executable:
            url = download.url().url()
            # No shell, and {url} is substituted AFTER shlex.split, so nothing
            # in this web-controlled URL can be parsed as a quote or separator.
            # The executable stays out of shlex.split because POSIX rules eat
            # the backslashes in paths like C:\Program Files\...
            args = [executable, *(arg.replace("{url}", url) for arg in shlex.split(arguments))]
            try:
                subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                save_dialog(download)  # bad path: user still gets their file
            else:
                download.cancel()
            return
        save_dialog(download)

    app.window.profile.downloadRequested.connect(download_requested)
```

The launch is attempted *before* `download.cancel()` so a mistyped executable cannot silently eat the download.

- [ ] **Step 3: Verify the handoff**

1. **Default off.** With the executable empty, download something in the integrated browser. Expected: the normal save dialog, exactly as before.
2. **Happy path.** Set the executable to your download manager (on Windows, typically `C:\Program Files (x86)\Internet Download Manager\IDMan.exe`) and keep `/d {url} /n`. Download a file. Expected: no save dialog; the manager picks up the URL and downloads it.
3. **Bad path.** Set the executable to `C:\nope\nope.exe` and download. Expected: the save dialog appears and the download still works. No crash, no lost download.
4. **The spec's open risk — do not skip.** Find a download that requires being logged in (an F95zone Donor DDL link, or an RPDL torrent) and hand it off. Expected: the manager downloads the real file, not an HTML error page or a login redirect.

   If step 4 fails, **stop and report**. It means the resolved URL needs session cookies that a bare CLI invocation cannot carry, IDM's command line has no clean way to pass them, and the design needs revisiting. Do not paper over it.
5. **Injection check.** Confirm a URL containing shell metacharacters is passed through literally rather than executed:

```bash
python -c "import shlex; url='http://x/a&calc.exe'; print([r'C:\idm.exe', *(a.replace('{url}', url) for a in shlex.split('/d {url} /n'))])"
```

Expected: `['C:\\idm.exe', '/d', 'http://x/a&calc.exe', '/n']` — the `&` sits inside one argv element and no shell ever sees it.

- [ ] **Step 4: Commit**

```bash
git add modules/webview.py modules/webview_window.py
git commit -m "feat: hand integrated browser downloads to an external download manager"
```

---

## Final verification

- [ ] Run the automated check: `python test_blocklist.py` → `ok`
- [ ] Confirm no documentation was committed: `git diff --stat master...HEAD -- docs/` → empty
- [ ] Confirm no new dependencies: `git diff master...HEAD -- requirements.txt requirements-dev.txt` → empty
- [ ] Confirm the full set of touched files is what you expect: `git diff --stat master...HEAD`
- [ ] Re-run the Task 4 Step 3 entry-point checklist one final time — `open`, login, and DDL resolution all still work
- [ ] Update `CHANGELOG.md` following the existing format, then commit it alone
