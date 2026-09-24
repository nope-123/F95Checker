# Browser Vertical Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The integrated browser can show its tabs as a searchable list down the left side instead of the strip along the top, switched by a toolbar button, Ctrl+Shift+, or a right-click on either strip, and remembered across windows.

**Architecture:** A new `TabSidebar` widget sits beside the existing `QTabWidget` in a `QSplitter`. It is only ever a view of `BrowserWindow.tab_list`: everything done to a row becomes the call the top strip would have made (`setCurrentIndex`, `close_tab`, `tabBar().moveTab`), and the list redraws from `tab_list` when that lands. The layout choice and sidebar width live in a `QSettings` ini inside the browser process.

**Tech Stack:** Python 3.14, PyQt6 6.7.1 / PyQt6-WebEngine 6.7.0 (Qt Widgets, `QSettings`, `QSplitter`, `QListWidget`), offscreen test files run with plain `python`.

**Spec:** `docs/superpowers/specs/2026-09-22-browser-vertical-tabs-design.md`

## Global Constraints

- All product code goes in `modules/webview_window.py` (a fork-only file). Do not touch `modules/db.py`, `common/structs.py`, `modules/gui.py` or `modules/webview.py`.
- Settings: `QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "f95checker", "browser")`, keys `vertical_tabs` (bool, default false) and `sidebar_width` (int, default 220).
- Menu wording, exactly: "Turn on vertical tabs" / "Turn off vertical tabs".
- Shortcuts, exactly: `Ctrl+Shift+,` toggles; `Ctrl+Shift+A` focuses the search box, vertical mode only.
- Search placeholder, exactly: "Search tabs". A row with no page title shows "New tab".
- Only windows built with `buttons=True, tabs=True` get the sidebar, button, shortcuts and menu. `tabs=False` windows must never write `vertical_tabs`.
- Glyphs from the bundled Material Design Icons font: `"\U000f10aa"` (nf-md-dock_left) in horizontal mode, `"\U000f1513"` (nf-md-dock_top) in vertical mode.
- Files are LF. Run tests with the local `python` (it has PyQt6 6.7.1); every test file runs offscreen and needs no network.
- Match the file's comment style: comments say *why*, in full sentences, no "added for X" notes.

## Review Focus

1. **A machine with vertical tabs saved runs the existing tests.** `test_webview_close.py` and `test_webview_find.py` assert top-strip geometry and would fail only on that machine → both redirect `QSettings` to a temp folder (Task 1, Step 1).
2. **A long list scrolled down while background tabs load.** Every url/title change redraws; a rebuild would jump to the top → rows are reused (Task 1 test `scroll`).
3. **Find bar open while toggling.** The page narrows and moves up under a hand-placed bar → it must still sit at the page's top right (Task 1 test `findbar`).
4. **A link dropped while the search hides rows.** The row under the pointer must map to the tab's real index, not its visible position (Task 3 test `filtereddrop`).
5. **Middle-clicking a row to close it.** The item view selects on any press, which would switch to the tab being closed → middle press is swallowed (Task 3 test `middle`).

## Before starting

The working tree already holds four uncommitted, finished tab changes (link placement beside the opener, squeezed tab titles, link drop on the tab bar, always-visible tab bar) plus this plan and its spec. Commit those on their own first, if the user has agreed to commits, so the vertical-tabs commits below stay reviewable:

```bash
git add modules/webview_window.py test_webview_close.py test_webview_find.py CHANGELOG-fork.md
git commit -m "Open links beside their tab, squeeze long titles, drop links on the tab bar, keep it visible"
git add docs/superpowers/specs/2026-09-22-browser-vertical-tabs-design.md docs/superpowers/plans/2026-09-22-browser-vertical-tabs.md
git commit -m "Spec and plan for vertical tabs in the browser"
```

---

### Task 1: Sidebar that mirrors the tabs, the toggle, and remembering it

**Files:**
- Modify: `modules/webview_window.py` (new `TabSidebar` class before `BrowserWindow`; `BrowserWindow.__init__`, `new_tab`, `close_tab`, `tab_changed`, `tab_title_changed`; `WebTab.url_changed`)
- Modify: `test_webview_close.py` (`browser()` helper), `test_webview_find.py` (`browser()` helper)
- Create: `test_webview_vtabs.py`

**Interfaces:**
- Produces: `TabSidebar(window)` with `.list: QListWidget`, `.refresh()`, `.row_changed(row: int)`. `BrowserWindow.sidebar: TabSidebar`, `.splitter: QSplitter`, `.settings: QSettings`, `.vertical: bool`, `.set_vertical(on: bool)`, `.toggle_vertical()`, `.vertical_label -> str`, `.controls.buttons.vertical: QPushButton`.

- [ ] **Step 1: Keep the existing tests off the real settings file**

In `test_webview_close.py`, add `import tempfile` after `import sys`, and replace `browser()` with:

```python
def browser(tabs=True):
    app = QtWidgets.QApplication(sys.argv)
    # Vertical tabs are remembered in browser.ini. Pointed at a throwaway folder, or a
    # machine that has them turned on would fail every top-strip assertion here
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, tempfile.mkdtemp(),
    )
    window = BrowserWindow(
        buttons=True, tabs=tabs, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.show()
    return app, window
```

In `test_webview_find.py`, add `import tempfile` after `import sys`, and replace `browser()` with:

```python
def browser():
    app = QtWidgets.QApplication(sys.argv)
    # Vertical tabs are remembered in browser.ini. Pointed at a throwaway folder, or a
    # machine that has them turned on would fail every top-strip assertion here
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, tempfile.mkdtemp(),
    )
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(800, 600)
    window.show()
    return app, window
```

- [ ] **Step 2: Write the failing tests**

Create `test_webview_vtabs.py`:

```python
#!/usr/bin/env python
# Run: python test_webview_vtabs.py
# Needs PyQt6 + QtWebEngine (like test_webview_close.py), runs offscreen and touches
# no network: pages are about:blank fragments and data: urls. Every case points
# QSettings at a temp folder first, so no run reads or writes the real browser.ini.
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from modules.webview_window import (
    BrowserWindow,
    FindBar,
    config_qt_flags,
)

config_qt_flags(debug=False, software=True)

from PyQt6 import (  # noqa: E402
    QtCore,
    QtGui,
    QtWidgets,
)
from PyQt6.QtTest import QTest  # noqa: E402


def until(check, ms=10000):
    """QTest.qWaitFor, which PyQt6 does not expose. Polls rather than sleeping a
    fixed time, so a page that loads fast costs nothing"""
    deadline = QtCore.QDeadlineTimer(ms)
    while not check() and not deadline.hasExpired():
        QTest.qWait(20)
    return check()


def settings():
    return QtCore.QSettings(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope,
        "f95checker", "browser",
    )


def window_(tabs=True):
    window = BrowserWindow(
        buttons=tabs, tabs=tabs, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(900, 600)
    window.show()
    return window


def browser(tabs=True):
    app = QtWidgets.QApplication(sys.argv)
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, tempfile.mkdtemp(),
    )
    return app, window_(tabs)


def opened(window, *names):
    """One tab per name, at about:blank#name: the view reports a url it was given
    straight away, so rows can be told apart without loading anything"""
    tabs = [window.new_tab(f"about:blank#{name}", background=bool(window.tab_list)) for name in names]
    QtWidgets.QApplication.processEvents()
    return tabs


def rows(window):
    """Each row's url, from the second line of its tooltip"""
    items = window.sidebar.list
    return [items.item(i).toolTip().split("\n")[-1].split("#")[-1] for i in range(items.count())]


def test_toggle_swaps_the_strips_and_is_remembered():
    app, window = browser()
    opened(window, "a")
    bar, sidebar = window.tabs.tabBar(), window.sidebar
    assert bar.isVisible() and not sidebar.isVisible(), "a fresh profile did not start with tabs on top"
    window.toggle_vertical()
    assert sidebar.isVisible() and not bar.isVisible(), "the toggle did not move the tabs to the side"
    assert settings().value("vertical_tabs", type=bool), "the choice was not written"
    second = window_()
    assert second.vertical and second.sidebar.isVisible(), "a new window forgot the choice"
    window.toggle_vertical()
    assert bar.isVisible() and not sidebar.isVisible(), "the toggle did not move the tabs back"
    keys = [s.key().toString() for s in window.findChildren(QtGui.QShortcut)]
    assert "Ctrl+Shift+," in keys, f"no toggle shortcut, only {keys}"


def test_the_list_mirrors_tab_list():
    app, window = browser()
    window.toggle_vertical()
    first, second = opened(window, "first", "second")
    window.new_tab("about:blank#child", background=True, opener=first)
    app.processEvents()
    assert rows(window) == ["first", "child", "second"], rows(window)
    window.tabs.tabBar().moveTab(0, 2)
    assert rows(window) == ["child", "second", "first"], rows(window)
    window.close_tab(0)
    assert rows(window) == ["second", "first"], rows(window)
    window.tabs.setCurrentIndex(1)
    assert window.sidebar.list.currentRow() == 1, "the current tab's row is not selected"
    second.load("data:text/html,<title>Renamed</title>")
    assert until(lambda: window.sidebar.list.item(0).text() == "Renamed"), (
        f"the row kept {window.sidebar.list.item(0).text()!r} after the title changed"
    )


def test_sidebar_width_is_remembered():
    app, window = browser()
    window.toggle_vertical()
    opened(window, "a")
    window.splitter.setSizes([300, 600])
    window.splitter.splitterMoved.emit(300, 1)  # what a drag on the divider emits
    assert settings().value("sidebar_width", type=int) == 300, "the width was not written"
    second = window_()
    app.processEvents()
    assert second.sidebar.width() == 300, f"a new window opened the sidebar at {second.sidebar.width()}"


def test_a_window_without_tabs_gets_neither_and_keeps_the_choice():
    """The login and resolver windows pass tabs=False. They show one page flat, and
    building one must not write over the choice the main browser saved"""
    app, window = browser()
    window.toggle_vertical()
    login = window_(tabs=False)
    login.new_tab()
    assert not login.vertical and not login.sidebar.isVisible(), "a one-page window grew a sidebar"
    assert not login.tabs.tabBar().isVisible(), "a one-page window grew a tab bar"
    assert not login.controls.buttons.vertical.isVisible(), "a one-page window grew the toggle"
    assert settings().value("vertical_tabs", type=bool), "a one-page window wrote over the choice"


def test_the_find_bar_follows_the_page_when_toggled():
    """The sidebar narrows the page and the top strip going away moves it up, and the
    find bar is placed by hand, so both have to reach it"""
    app, window = browser()
    opened(window, "a")
    QTest.qWait(100)
    window.find.activate()
    QTest.qWait(100)
    window.toggle_vertical()
    QTest.qWait(100)
    bar, tabs = window.find, window.tabs
    assert bar.x() + bar.width() == tabs.width() - FindBar.MARGIN, (
        f"bar right edge {bar.x() + bar.width()} is not {FindBar.MARGIN} from {tabs.width()}"
    )
    assert bar.y() == FindBar.MARGIN, f"bar top {bar.y()} is not {FindBar.MARGIN} with no strip above"


def test_a_long_list_keeps_its_place_through_updates():
    """Every title and url change redraws the list. Rebuilding it would throw anyone
    scrolled down a long list back to the top each time a background tab loads"""
    app, window = browser()
    window.resize(900, 300)  # rows are short offscreen: forty only overflow a short window
    window.toggle_vertical()
    opened(window, *map(str, range(40)))
    scroll = window.sidebar.list.verticalScrollBar()
    assert scroll.maximum() > 0, "not enough tabs to scroll, so this proves nothing"
    scroll.setValue(scroll.maximum())
    window.tab_list[20].load("about:blank#moved")  # a background tab navigates
    assert until(lambda: "moved" in rows(window)), "the row never saw the new url"
    assert scroll.value() == scroll.maximum(), f"the list jumped to {scroll.value()}"


if __name__ == "__main__":
    tests = {
        "toggle": test_toggle_swaps_the_strips_and_is_remembered,
        "mirror": test_the_list_mirrors_tab_list,
        "width": test_sidebar_width_is_remembered,
        "oneview": test_a_window_without_tabs_gets_neither_and_keeps_the_choice,
        "findbar": test_the_find_bar_follows_the_page_when_toggled,
        "scroll": test_a_long_list_keeps_its_place_through_updates,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python test_webview_vtabs.py toggle`
Expected: FAIL with `AttributeError: 'BrowserWindow' object has no attribute 'sidebar'`

- [ ] **Step 4: Add the `TabSidebar` class**

In `modules/webview_window.py`, insert immediately before `class BrowserWindow(QtWidgets.QWidget):`:

```python
class TabSidebar(QtWidgets.QWidget):
    """Vertical tabs: a list with one row per tab. Only ever a view of
    window.tab_list, never a second copy of it -- nothing here reorders or drops a
    row. What you do to a row becomes the call the top strip would have made, and the
    list redraws from tab_list once that lands, so the two strips cannot disagree."""

    def __init__(self, window: "BrowserWindow"):
        super().__init__(window)
        self.window = window
        self.setLayout(QtWidgets.QVBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.list = QtWidgets.QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.layout().addWidget(self.list)

        self.list.currentRowChanged.connect(self.row_changed)

    def refresh(self):
        """Redraw from tab_list. Rows are reused rather than rebuilt, so a long list
        keeps its scroll position through every title change"""
        tabs = self.window.tab_list
        # Blocked, or selecting the current tab's row would look like a click on it
        self.list.blockSignals(True)
        while self.list.count() > len(tabs):
            self.list.takeItem(self.list.count() - 1)
        while self.list.count() < len(tabs):
            self.list.addItem("")
        for row, tab in enumerate(tabs):
            title, url = tab.view.title() or "New tab", tab.view.url().toString()
            item = self.list.item(row)
            item.setText(title)
            item.setToolTip(f"{title}\n{url}")
        self.list.setCurrentRow(self.window.tabs.currentIndex())
        self.list.blockSignals(False)

    def row_changed(self, row: int):
        if row >= 0:
            self.window.tabs.setCurrentIndex(row)


```

- [ ] **Step 5: Settings and the `vertical` flag**

In `BrowserWindow.__init__`, replace:

```python
        self.tab_list = []
        self.profile =
```

with:

```python
        self.tab_list = []
        self.vertical = False
        # Kept by the browser itself, not the main app: a setting there would be four
        # of upstream's files for a preference only the browser reads and writes.
        # IniFormat, so it sits in the app's own data folder rather than the registry
        self.settings = QtCore.QSettings(
            QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope,
            "f95checker", "browser",
        )
        self.profile =
```

- [ ] **Step 6: The toolbar button**

Replace:

```python
        b.extension = QtWidgets.QPushButton(icon, "", b)
        for widget in (b.back, b.forward, b.reload, b.url, b.extension):
```

with:

```python
        b.extension = QtWidgets.QPushButton(icon, "", b)
        b.vertical = QtWidgets.QPushButton("", b)  # set_vertical picks the glyph
        for widget in (b.back, b.forward, b.reload, b.url, b.extension, b.vertical):
```

Replace:

```python
        else:
            b.extension.setVisible(False)
```

with:

```python
        else:
            b.extension.setVisible(False)
        b.vertical.clicked.connect(lambda _=None: self.toggle_vertical())
        b.vertical.setVisible(tabs)  # the one-page windows have no tabs to lay out
```

- [ ] **Step 7: Build the sidebar before any signal can reach it**

Replace:

```python
        self.find = FindBar(self)
```

with:

```python
        self.find = FindBar(self)
        # Same reason: tab_changed and tabMoved redraw it
        self.sidebar = TabSidebar(self)
```

Delete these three lines (their job moves into `set_vertical`):

```python
        # Always there, like a browser's, so a link always has somewhere to be dropped.
        # The chrome-less windows are one page and never get one
        self.tabs.tabBar().setVisible(tabs)
```

Replace:

```python
        self.tabs.tabBar().tabMoved.connect(
            lambda frm, to: self.tab_list.insert(to, self.tab_list.pop(frm))
        )
```

with:

```python
        self.tabs.tabBar().tabMoved.connect(
            lambda frm, to: self.tab_list.insert(to, self.tab_list.pop(frm))
        )
        # Connected second, so it runs once tab_list is already back in step
        self.tabs.tabBar().tabMoved.connect(lambda _, __: self.sidebar.refresh())
```

- [ ] **Step 8: The toggle shortcut**

Replace:

```python
                ("Ctrl+Tab", self.next_tab),
            ):
```

with:

```python
                ("Ctrl+Tab", self.next_tab),
                ("Ctrl+Shift+,", self.toggle_vertical),  # Edge's
            ):
```

- [ ] **Step 9: The splitter, and restoring the saved choice**

Replace:

```python
        self.layout().addWidget(self.controls, stretch=0)
        self.layout().addWidget(self.tabs, stretch=1)
```

with:

```python
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
```

- [ ] **Step 10: `set_vertical`, `toggle_vertical`, `vertical_label`**

Insert right after the end of `BrowserWindow.eventFilter` (after its final `return super().eventFilter(obj, event)`, before `def close_tab`):

```python

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
```

- [ ] **Step 11: Redraw wherever `tab_list` or a tab changes**

In `new_tab`, replace:

```python
        if url:
            tab.load(url)
        return tab
```

with:

```python
        if url:
            tab.load(url)
        self.sidebar.refresh()
        return tab
```

In `close_tab`, replace:

```python
        # Deferred because this runs inside QTabBar's mouse handler
        tab.view.deleteLater()
```

with:

```python
        # Deferred because this runs inside QTabBar's mouse handler
        tab.view.deleteLater()
        # Removing a tab in front of the current one moves no current tab, so nothing
        # else would redraw
        self.sidebar.refresh()
```

In `tab_changed`, replace:

```python
        tab = self.current_tab
        self.sync_controls()
        if not tab:
            return
        self.find.follow(tab)
```

with:

```python
        tab = self.current_tab
        self.sync_controls()
        self.sidebar.refresh()
        if not tab:
            return
        self.find.follow(tab)
```

In `tab_title_changed`, replace:

```python
        if tab in self.tab_list:
            self.tabs.setTabText(self.tab_list.index(tab), title[:30])
```

with:

```python
        if tab in self.tab_list:
            self.tabs.setTabText(self.tab_list.index(tab), title[:30])
        self.sidebar.refresh()
```

In `WebTab.url_changed`, replace:

```python
    def url_changed(self, url: QtCore.QUrl):
        if self.is_current:
            self.window.set_url_text(url.url())
```

with:

```python
    def url_changed(self, url: QtCore.QUrl):
        if self.is_current:
            self.window.set_url_text(url.url())
        self.window.sidebar.refresh()  # the row's tooltip, and what search matches
```

- [ ] **Step 12: Run the tests to verify they pass**

Run: `python test_webview_vtabs.py`
Expected: `ok`

- [ ] **Step 13: Run every webview test file**

Run: `for t in test_webview_vtabs.py test_webview_close.py test_webview_find.py test_webview_block.py test_webview_nav.py test_webview_scroll.py test_webview_redirect.py; do echo "== $t"; python $t 2>&1 | tail -1; done`
Expected: `ok` for all seven.

- [ ] **Step 14: Commit**

```bash
git add modules/webview_window.py test_webview_vtabs.py test_webview_close.py test_webview_find.py
git commit -m "Show the browser's tabs down the side, toggled and remembered"
```

---

### Task 2: Search

**Files:**
- Modify: `modules/webview_window.py` (`TabSidebar`; the shortcut list in `BrowserWindow.__init__`)
- Modify: `test_webview_vtabs.py`

**Interfaces:**
- Consumes: `TabSidebar.refresh()`, `TabSidebar.list`, `BrowserWindow.vertical`, `BrowserWindow.current_tab` (Task 1).
- Produces: `TabSidebar.search: QLineEdit`, `.query -> str` (stripped, casefolded), `.focus_search()`, `.leave_search()`, `.eventFilter(obj, event)` with a `self.search` branch that Task 3 extends.

- [ ] **Step 1: Write the failing tests**

In `test_webview_vtabs.py`, add after `rows()`:

```python
def visible(window):
    items = window.sidebar.list
    return [i for i in range(items.count()) if not items.item(i).isHidden()]
```

Add before `if __name__ == "__main__":`:

```python
def test_search_filters_by_title_and_url():
    app, window = browser()
    window.toggle_vertical()
    alpha, beta, gamma = opened(window, "a", "b", "c")
    alpha.load("data:text/html,<title>Alpha thread</title>")
    beta.load("data:text/html,<title>Beta thread</title>")
    gamma.load("data:text/html,<title>Gamma</title><!--needle-->")
    items = window.sidebar.list
    assert until(
        lambda: [items.item(i).text() for i in range(3)] == ["Alpha thread", "Beta thread", "Gamma"]
    ), "the pages never loaded"
    search = window.sidebar.search
    search.setText("ALP")
    assert visible(window) == [0], f"title search showed {visible(window)}"
    search.setText("needle")
    assert visible(window) == [2], f"url search showed {visible(window)}"
    search.setText("thread")
    beta.load("data:text/html,<title>Renamed</title>")
    assert until(lambda: visible(window) == [0]), (
        f"a tab renamed out of the search stayed listed: {visible(window)}"
    )
    search.setText("zzz")
    QTest.keyClick(search, QtCore.Qt.Key.Key_Return)
    assert search.text() == "zzz", "Enter with nothing to switch to threw the query away"
    QTest.keyClick(search, QtCore.Qt.Key.Key_Escape)
    assert search.text() == "" and visible(window) == [0, 1, 2], "Esc did not clear the search"
    keys = [s.key().toString() for s in window.findChildren(QtGui.QShortcut)]
    assert "Ctrl+Shift+A" in keys, f"no tab search shortcut, only {keys}"


def test_enter_switches_to_the_first_match():
    app, window = browser()
    window.toggle_vertical()
    opened(window, "one", "two", "three")
    search = window.sidebar.search
    search.setText("thr")
    QTest.keyClick(search, QtCore.Qt.Key.Key_Return)
    assert window.tabs.currentIndex() == 2, f"Enter went to tab {window.tabs.currentIndex()}"
    assert search.text() == "" and visible(window) == [0, 1, 2], "Enter did not clear the search"
```

Add to the `tests` dict:

```python
        "search": test_search_filters_by_title_and_url,
        "enter": test_enter_switches_to_the_first_match,
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python test_webview_vtabs.py search`
Expected: FAIL with `AttributeError: 'TabSidebar' object has no attribute 'search'`

- [ ] **Step 3: The search box**

In `TabSidebar`'s docstring, replace:

```python
    """Vertical tabs: a list with one row per tab. Only ever a view of
    window.tab_list, never a second copy of it -- nothing here reorders or drops a
```

with:

```python
    """Vertical tabs: a search box over a list with one row per tab. Only ever a view
    of window.tab_list, never a second copy of it -- nothing here reorders or drops a
```

In `TabSidebar.__init__`, replace:

```python
        self.list = QtWidgets.QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.layout().addWidget(self.list)

        self.list.currentRowChanged.connect(self.row_changed)
```

with:

```python
        self.search = QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText("Search tabs")
        self.search.setClearButtonEnabled(True)
        self.list = QtWidgets.QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.layout().addWidget(self.search)
        self.layout().addWidget(self.list)

        self.list.currentRowChanged.connect(self.row_changed)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.search.installEventFilter(self)

    @property
    def query(self):
        return self.search.text().strip().casefold()
```

- [ ] **Step 4: Filter in `refresh`**

In `TabSidebar.refresh`, replace:

```python
        for row, tab in enumerate(tabs):
            title, url = tab.view.title() or "New tab", tab.view.url().toString()
            item = self.list.item(row)
            item.setText(title)
            item.setToolTip(f"{title}\n{url}")
```

with:

```python
        query = self.query
        for row, tab in enumerate(tabs):
            title, url = tab.view.title() or "New tab", tab.view.url().toString()
            item = self.list.item(row)
            item.setText(title)
            item.setToolTip(f"{title}\n{url}")
            # Only the row: the tab itself is untouched
            item.setHidden(bool(query) and query not in title.casefold() and query not in url.casefold())
```

- [ ] **Step 5: Keys in the box, and focusing it**

Add after `TabSidebar.row_changed`:

```python

    def focus_search(self):
        """Ctrl+Shift+A. Only with the sidebar out: there is no box to focus otherwise"""
        if self.window.vertical:
            self.search.setFocus()
            self.search.selectAll()

    def leave_search(self):
        self.search.clear()
        if tab := self.window.current_tab:
            tab.view.setFocus()

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
        return super().eventFilter(obj, event)
```

In `BrowserWindow.__init__`, replace:

```python
                ("Ctrl+Shift+,", self.toggle_vertical),  # Edge's
            ):
```

with:

```python
                ("Ctrl+Shift+,", self.toggle_vertical),  # Edge's
                ("Ctrl+Shift+A", self.sidebar.focus_search),  # Chrome's tab search
            ):
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python test_webview_vtabs.py`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add modules/webview_window.py test_webview_vtabs.py
git commit -m "Search the browser's vertical tabs by title or url"
```

---

### Task 3: Reorder, close and drop links on the list

**Files:**
- Modify: `modules/webview_window.py` (`TabSidebar`; `BrowserWindow.eventFilter` drop branch; new `BrowserWindow.open_dropped`)
- Modify: `test_webview_vtabs.py`

**Interfaces:**
- Consumes: `TabSidebar.query`, `TabSidebar.eventFilter` (Task 2); `BrowserWindow.close_tab(index: int)`, `tabs.tabBar().moveTab`.
- Produces: `BrowserWindow.open_dropped(urls: list[QtCore.QUrl], to: int)`; `TabSidebar.closer: QToolButton` (with a `.row: int` attribute), `.drag_row`, `.hover(pos)`, `.drop_index(pos) -> int`, and an `obj is self.list.viewport()` branch in `eventFilter` that Task 4 extends.

- [ ] **Step 1: Write the failing tests**

In `test_webview_vtabs.py`, add after the imports:

```python
LEFT = QtCore.Qt.MouseButton.LeftButton
MIDDLE = QtCore.Qt.MouseButton.MiddleButton
NONE = QtCore.Qt.MouseButton.NoButton
```

Add after `visible()`:

```python
def in_step(window):
    views = [window.tabs.widget(i) for i in range(window.tabs.count())]
    return all(v is t.view for v, t in zip(views, window.tab_list))


def mouse(widget, kind, pos, button, buttons):
    point = QtCore.QPointF(pos)
    event = QtGui.QMouseEvent(
        kind, point, widget.mapToGlobal(point), button, buttons,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, event)
    QtWidgets.QApplication.processEvents()


def row_center(window, row):
    items = window.sidebar.list
    return items.visualItemRect(items.item(row)).center()


def drag_row(window, frm, to):
    view = window.sidebar.list.viewport()
    Type = QtCore.QEvent.Type
    mouse(view, Type.MouseButtonPress, row_center(window, frm), LEFT, LEFT)
    mouse(view, Type.MouseMove, row_center(window, to), NONE, LEFT)
    mouse(view, Type.MouseButtonRelease, row_center(window, to), LEFT, NONE)


def drop_link(window, pos, url):
    """Enter first: Qt ignores a drop on a widget no drag ever entered"""
    view = window.sidebar.list.viewport()
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl(url)])
    args = (QtCore.Qt.DropAction.CopyAction, mime, NONE, QtCore.Qt.KeyboardModifier.NoModifier)
    enter = QtGui.QDragEnterEvent(pos, *args)
    QtWidgets.QApplication.sendEvent(view, enter)
    assert view.acceptDrops() and enter.isAccepted(), "the list turned the link away"
    QtWidgets.QApplication.sendEvent(view, QtGui.QDropEvent(QtCore.QPointF(pos), *args))
    QtWidgets.QApplication.processEvents()
```

Add before `if __name__ == "__main__":`:

```python
def test_dragging_a_row_reorders_the_tabs():
    app, window = browser()
    window.toggle_vertical()
    a, b, c = opened(window, "a", "b", "c")
    drag_row(window, 2, 0)
    assert window.tab_list == [c, a, b], f"tab_list is {rows(window)}"
    assert in_step(window), "the tab bar disagrees with tab_list"
    assert rows(window) == ["c", "a", "b"], rows(window)
    assert window.current_tab is c, "the dragged tab is not the current one"


def test_dragging_is_off_while_filtered():
    app, window = browser()
    window.toggle_vertical()
    a, b, c = opened(window, "a", "b", "c")
    window.sidebar.search.setText("about")  # matches every row, so all stay in reach
    drag_row(window, 0, 2)
    assert window.tab_list == [a, b, c], f"a filtered drag reordered: {rows(window)}"


def test_middle_click_closes_without_switching():
    app, window = browser()
    window.toggle_vertical()
    a, b, c = opened(window, "a", "b", "c")
    view = window.sidebar.list.viewport()
    Type = QtCore.QEvent.Type
    mouse(view, Type.MouseButtonPress, row_center(window, 1), MIDDLE, MIDDLE)
    assert window.current_tab is a, "a middle press switched to the tab it was closing"
    mouse(view, Type.MouseButtonRelease, row_center(window, 1), MIDDLE, NONE)
    assert window.tab_list == [a, c], f"middle click left {rows(window)}"


def test_the_hover_close_button_closes_that_row():
    app, window = browser()
    window.toggle_vertical()
    a, b, c = opened(window, "a", "b", "c")
    mouse(window.sidebar.list.viewport(), QtCore.QEvent.Type.MouseMove, row_center(window, 2), NONE, NONE)
    closer = window.sidebar.closer
    assert closer.isVisible(), "hovering a row showed no close button"
    closer.click()
    assert window.tab_list == [a, b], f"the close button left {rows(window)}"
    assert not closer.isVisible(), "the close button outlived its row"


def test_a_link_dropped_on_the_list_opens_at_that_row():
    app, window = browser()
    window.toggle_vertical()
    opened(window, "a", "b")
    rect = window.sidebar.list.visualItemRect(window.sidebar.list.item(0))
    drop_link(window, QtCore.QPoint(rect.center().x(), rect.bottom() - 1), "about:blank#dropped")  # lower half of the first row
    assert rows(window) == ["a", "dropped", "b"], rows(window)
    assert in_step(window), "the tab bar disagrees with tab_list"


def test_a_link_dropped_while_filtered_lands_by_tab_order():
    """With rows hidden, the row under the pointer is still that tab's real index"""
    app, window = browser()
    window.toggle_vertical()
    opened(window, "a", "b", "c")
    window.sidebar.search.setText("#c")
    assert visible(window) == [2], visible(window)
    rect = window.sidebar.list.visualItemRect(window.sidebar.list.item(2))
    drop_link(window, QtCore.QPoint(rect.center().x(), rect.top() + 1), "about:blank#dropped")  # upper half of c, shown first
    assert rows(window) == ["a", "b", "dropped", "c"], rows(window)
```

Add to the `tests` dict:

```python
        "drag": test_dragging_a_row_reorders_the_tabs,
        "filtered": test_dragging_is_off_while_filtered,
        "middle": test_middle_click_closes_without_switching,
        "hover": test_the_hover_close_button_closes_that_row,
        "drop": test_a_link_dropped_on_the_list_opens_at_that_row,
        "filtereddrop": test_a_link_dropped_while_filtered_lands_by_tab_order,
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python test_webview_vtabs.py drag`
Expected: FAIL with `AssertionError: tab_list is ['a', 'b', 'c']` (the view's own drag-select switches tabs instead of moving one)

- [ ] **Step 3: Share the tab bar's drop code**

In `BrowserWindow.eventFilter`, replace:

```python
                for url in event.mimeData().urls():
                    tab = self.new_tab(url.toString())
                    # tabMoved keeps tab_list in step, same as a drag
                    obj.moveTab(self.tab_list.index(tab), to)
                    to += 1
            return True
        return super().eventFilter(obj, event)
```

with:

```python
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
```

- [ ] **Step 4: Mouse tracking, drops and the close button on the list**

In `TabSidebar.__init__`, replace:

```python
        self.window = window
        self.setLayout(QtWidgets.QVBoxLayout(self))
```

with:

```python
        self.window = window
        self.drag_row = None  # the row a left press landed on, until the release
        self.setLayout(QtWidgets.QVBoxLayout(self))
```

Replace:

```python
        self.list.setUniformItemSizes(True)
        self.layout().addWidget(self.search)
        self.layout().addWidget(self.list)

        self.list.currentRowChanged.connect(self.row_changed)
```

with:

```python
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
```

Replace:

```python
        self.search.installEventFilter(self)

    @property
    def query(self):
```

with:

```python
        self.search.installEventFilter(self)
        # The viewport, not the list: an item view gets its mouse and drop events there
        self.list.viewport().installEventFilter(self)

    @property
    def query(self):
```

In `TabSidebar.refresh`, replace:

```python
        tabs = self.window.tab_list
        # Blocked, or selecting the current tab's row would look like a click on it
```

with:

```python
        tabs = self.window.tab_list
        self.closer.hide()  # its row may have just moved or gone
        # Blocked, or selecting the current tab's row would look like a click on it
```

- [ ] **Step 5: Hover and drop position**

Add after `TabSidebar.leave_search`:

```python

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
```

- [ ] **Step 6: The list's mouse and drop events**

In `TabSidebar.eventFilter`, replace the end of the Enter branch (`FindBar.eventFilter` ends in the same two lines, so match all four):

```python
                    self.window.tabs.setCurrentIndex(first)
                    self.leave_search()
                return True
        return super().eventFilter(obj, event)
```

with:

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python test_webview_vtabs.py`
Expected: `ok`

- [ ] **Step 8: Run the top strip's drop test, which now goes through `open_dropped`**

Run: `python test_webview_close.py`
Expected: `ok`

- [ ] **Step 9: Commit**

```bash
git add modules/webview_window.py test_webview_vtabs.py
git commit -m "Reorder, close and drop links on the browser's vertical tabs"
```

---

### Task 4: Right-click menu on both strips

**Files:**
- Modify: `modules/webview_window.py` (`TabSidebar.eventFilter`; `BrowserWindow.__init__` shortcut block; new `BrowserWindow.show_tab_menu`)
- Modify: `test_webview_vtabs.py`

**Interfaces:**
- Consumes: `BrowserWindow.vertical_label`, `.toggle_vertical()` (Task 1); the viewport branch of `TabSidebar.eventFilter` (Task 3).
- Produces: `BrowserWindow.show_tab_menu(pos: QtCore.QPoint)` (global position).

- [ ] **Step 1: Write the failing test**

Add before `if __name__ == "__main__":`:

```python
def test_right_click_menu_holds_the_toggle():
    app, window = browser()
    opened(window, "a", "b")
    # A real menu spins its own event loop and would hang the run, so exec just
    # records the menu. Never restored: each case is its own process
    menus = []
    QtWidgets.QMenu.exec = lambda self, *_: menus.append(self)
    bar = window.tabs.tabBar()
    bar.customContextMenuRequested.emit(bar.tabRect(0).center())
    assert [a.text() for a in menus[-1].actions()] == ["Turn on vertical tabs"], menus[-1].actions()
    menus[-1].actions()[0].trigger()
    assert window.vertical, "the top strip's menu did not turn vertical tabs on"
    view = window.sidebar.list.viewport()
    pos = row_center(window, 0)
    QtWidgets.QApplication.sendEvent(view, QtGui.QContextMenuEvent(
        QtGui.QContextMenuEvent.Reason.Mouse, pos, view.mapToGlobal(pos),
    ))
    assert [a.text() for a in menus[-1].actions()] == ["Turn off vertical tabs"], menus[-1].actions()
    menus[-1].actions()[0].trigger()
    assert not window.vertical, "the sidebar's menu did not turn vertical tabs off"
```

Add to the `tests` dict, after `"toggle"`:

```python
        "menu": test_right_click_menu_holds_the_toggle,
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python test_webview_vtabs.py menu`
Expected: FAIL with `IndexError: list index out of range` (no menu was shown)

- [ ] **Step 3: The menu**

Add after `BrowserWindow.vertical_label`:

```python

    def show_tab_menu(self, pos: QtCore.QPoint):
        """Right-click on either strip. Holds only the layout toggle: the page keeps its
        own menu, and this one is not a second place to find it"""
        menu = QtWidgets.QMenu(self)
        menu.addAction(self.vertical_label).triggered.connect(lambda _=None: self.toggle_vertical())
        menu.exec(pos)
        menu.deleteLater()
```

In `BrowserWindow.__init__`, replace:

```python
                ("Ctrl+Shift+A", self.sidebar.focus_search),  # Chrome's tab search
            ):
                QtGui.QShortcut(QtGui.QKeySequence(keys), self).activated.connect(handler)
```

with:

```python
                ("Ctrl+Shift+A", self.sidebar.focus_search),  # Chrome's tab search
            ):
                QtGui.QShortcut(QtGui.QKeySequence(keys), self).activated.connect(handler)
            bar = self.tabs.tabBar()
            bar.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            bar.customContextMenuRequested.connect(lambda pos: self.show_tab_menu(bar.mapToGlobal(pos)))
```

In `TabSidebar.eventFilter`, replace:

```python
            elif event.type() is Type.Leave:
                self.closer.hide()
```

with:

```python
            elif event.type() is Type.Leave:
                self.closer.hide()
            elif event.type() is Type.ContextMenu:
                self.window.show_tab_menu(event.globalPos())
                return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python test_webview_vtabs.py`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add modules/webview_window.py test_webview_vtabs.py
git commit -m "Toggle vertical tabs from a right-click on either strip"
```

---

### Task 5: Release notes, full suite, hand over the GUI check

**Files:**
- Modify: `CHANGELOG-fork.md`
- Modify: `docs/superpowers/specs/2026-09-22-browser-vertical-tabs-design.md` (status line)

- [ ] **Step 1: Release notes**

In `CHANGELOG-fork.md`, add under `### Added:` (after the existing drag-a-link line):

```markdown
- Vertical tabs in the browser: a list down the side with a search box, switched from the toolbar, Ctrl+Shift+, or a right-click on the tabs, and remembered
```

- [ ] **Step 2: Mark the spec implemented**

In the spec, replace `Status: approved` with `Status: implemented`.

- [ ] **Step 3: Run every webview test file**

Run: `for t in test_webview_vtabs.py test_webview_close.py test_webview_find.py test_webview_block.py test_webview_nav.py test_webview_scroll.py test_webview_redirect.py; do echo "== $t"; python $t 2>&1 | tail -1; done`
Expected: `ok` for all seven.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG-fork.md docs/superpowers/specs/2026-09-22-browser-vertical-tabs-design.md
git commit -m "Note vertical tabs in the fork changelog"
```

- [ ] **Step 5: Hand the GUI check to the user**

The user eyeballs GUI changes themselves. Tell them what to try, and offer to launch the browser (say first that a window will open):
open a handful of threads; toggle from the toolbar, from Ctrl+Shift+, and from a right-click on the tabs; search by title and by part of a url, Enter and Esc; drag rows; close by middle-click and by the hover ×; drop a link from a page onto the list; restart the browser and check it reopens vertical at the same width. Ctrl+Shift+, is Shift plus comma on a US layout and only a real key press shows whether it fires on theirs.
