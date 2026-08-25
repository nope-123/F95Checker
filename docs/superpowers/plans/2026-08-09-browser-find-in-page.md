# Browser Find In Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ctrl+F in the integrated browser opens a find bar floating over the top right of the page that searches the current tab, highlights every match, shows `2/17`, and steps through matches with Enter and Shift+Enter.

**Architecture:** A single `FindBar` widget per window, parented to the window's `QTabWidget` and never added to a layout, positioned by hand over the page area. It searches through `QWebEnginePage.findText()`, which is Chromium's own find. Each `WebTab` carries its own `find_open` / `find_query` / `find_status`, and the one bar mirrors whichever tab is current — it never re-runs a search on a tab switch, because highlights survive a hidden view and a repeated query would advance the match.

**Tech Stack:** Python 3.11+, PyQt6 / PyQt6-WebEngine, `QWebEnginePage.findText`, `QWebEngineFindTextResult`. Tests are plain scripts with `assert`, run offscreen, no pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-browser-find-in-page-design.md`

## Global Constraints

- All production changes live in `modules/webview_window.py`. `modules/webview.py`, the ImGui app, and the settings are untouched. No new setting.
- No new dependency. No new file except the test.
- Version floor is PyQt6 6.4.2 / PyQt6-WebEngine 6.4.0 on macOS, 6.7.1 / 6.7.0 elsewhere (`requirements.txt:22-25`). Everything used here exists in 6.4.
- `QtWebEngineCore` and `QtWebEngineWidgets` must be imported inside functions, never at module scope — `config_qt_flags()` has to run before Qt WebEngine loads. `QtCore`, `QtGui`, `QtWidgets` are already module-level (`modules/webview_window.py:10-15`).
- `QKeyEvent.key()` returns a plain `int` in PyQt6, so key comparisons use `==` and `in`, **not** the `is` that the file's existing `eventFilter` uses for `QEvent.Type` and `MouseButton` (those really are enum members).
- Verified against this repo's PyQt6 6.7.1 before writing this plan: `FindFlag(0)` is a valid "no flags" value; a first search reports `activeMatch=1`; repeating the same query advances to 2; `FindBackward` goes back to 1; no match reports `0/0`; `findText("")` clears and never calls the callback.
- Button glyphs are written as `"\U000f005d"`-style escapes in this plan so they survive copy and paste. The file itself uses literal Nerd Font glyphs; either form is fine as long as the codepoint matches.
- Every test file in this repo is a standalone script run as `python test_x.py`, with one case per subprocess because a process gets one `QApplication`. Follow `test_webview_block.py` exactly.

---

### Task 1: FindBar widget, Ctrl+F, and placement

Creates the widget, its per-tab state, and its position over the page. No searching yet.

**Files:**
- Modify: `modules/webview_window.py` — new `FindBar` class before `class BrowserWindow` (after `WebTab`, which ends at line 327); `WebTab.__init__` (lines 121-125); `BrowserWindow.__init__` (lines 412-439); `BrowserWindow.eventFilter` (lines 465-472)
- Test: `test_webview_find.py` (create)

**Interfaces:**
- Consumes: `BrowserWindow.tabs`, `BrowserWindow.current_tab`, `WebTab.view`, `WebTab.page`, `WebTab.is_current` — all existing.
- Produces:
  - `WebTab.find_open: bool`, `WebTab.find_query: str`, `WebTab.find_status: str`
  - `BrowserWindow.find: FindBar`
  - `FindBar.MARGIN: int`, `FindBar.query: QLineEdit`, `FindBar.status: QLabel`, `FindBar.prev/next/done: QPushButton`
  - `FindBar.activate() -> None`, `FindBar.dismiss() -> None`, `FindBar.place() -> None`, `FindBar.reposition() -> None`, `FindBar.set_query(text: str) -> None`

- [ ] **Step 1: Write the failing test**

Create `test_webview_find.py`:

```python
#!/usr/bin/env python
# Run: python test_webview_find.py
# Needs PyQt6 + QtWebEngine (like test_webview_block.py, unlike test_blocklist.py),
# runs offscreen and touches no network: every page is set with setHtml().
import os
import sys

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

PAGE = "https://find.invalid/page"
HTML = "<html><body><p>cat</p><p>dog cat</p><p>a cat here</p></body></html>"
OTHER = "<html><body><p>dog</p><p>dog</p></body></html>"


def browser():
    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=True, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(800, 600)
    window.show()
    return app, window


def loaded(app, tab, html: str, then):
    """setHtml is asynchronous, and so is every find that follows it, so a case is a
    chain of callbacks that ends by quitting the app. Disconnected after the first
    load, or a case that navigates again would restart its own chain and quit early."""
    def once(ok):
        tab.view.loadFinished.disconnect(once)
        QtCore.QTimer.singleShot(200, then)
    tab.view.loadFinished.connect(once)
    tab.page.setHtml(html, QtCore.QUrl(PAGE))


def test_bar_starts_hidden():
    app, window = browser()
    window.new_tab()
    assert not window.find.isVisible(), "the find bar showed itself unasked"


def test_ctrl_f_shortcut_is_registered():
    app, window = browser()
    keys = [s.key().toString() for s in window.findChildren(QtGui.QShortcut)]
    assert "Ctrl+F" in keys, f"no Ctrl+F shortcut, only {keys}"


def test_bar_sits_over_the_top_right_of_the_page():
    app, window = browser()
    window.new_tab()
    QTest.qWait(100)
    window.find.activate()
    QTest.qWait(100)
    bar, tabs = window.find, window.tabs
    assert bar.isVisible(), "the find bar did not show"
    assert bar.x() + bar.width() == tabs.width() - FindBar.MARGIN, (
        f"bar right edge {bar.x() + bar.width()} is not {FindBar.MARGIN} from {tabs.width()}"
    )
    assert bar.y() == FindBar.MARGIN, f"bar top {bar.y()} is not {FindBar.MARGIN}"


def test_bar_clears_the_tab_bar_when_a_second_tab_opens():
    app, window = browser()
    window.new_tab()
    QTest.qWait(100)
    window.find.activate()
    QTest.qWait(100)
    window.new_tab(background=True)
    QTest.qWait(100)
    bar = window.find
    assert window.tabs.tabBar().isVisible(), "the tab bar did not appear"
    assert bar.y() >= window.tabs.tabBar().height(), (
        f"bar top {bar.y()} overlaps the {window.tabs.tabBar().height()}px tab bar"
    )


def test_dismiss_hides_the_bar():
    app, window = browser()
    tab = window.new_tab()
    window.find.activate()
    assert tab.find_open, "activate did not mark the tab"
    window.find.dismiss()
    assert not window.find.isVisible(), "dismiss left the bar showing"
    assert not tab.find_open, "dismiss left the tab marked"


if __name__ == "__main__":
    tests = {
        "hidden": test_bar_starts_hidden,
        "shortcut": test_ctrl_f_shortcut_is_registered,
        "position": test_bar_sits_over_the_top_right_of_the_page,
        "tabbar": test_bar_clears_the_tab_bar_when_a_second_tab_opens,
        "dismiss": test_dismiss_hides_the_bar,
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

- [ ] **Step 2: Run the test to verify it fails**

Run: `python test_webview_find.py`
Expected: FAIL — `ImportError: cannot import name 'FindBar' from 'modules.webview_window'`

- [ ] **Step 3: Add the per-tab state to `WebTab`**

In `modules/webview_window.py`, in `WebTab.__init__`, next to the existing flags (currently lines 124-125):

```python
        self.loading = False
        self.probe = False
        # Find state lives on the tab: the window has one bar, and it mirrors
        # whichever tab is current
        self.find_open = False
        self.find_query = ""
        self.find_status = ""
```

- [ ] **Step 4: Write the `FindBar` class**

Insert between `WebTab` and `class BrowserWindow`:

```python
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
        for widget in (self.query, self.status, self.prev, self.next, self.done):
            self.layout().addWidget(widget)

        self.done.clicked.connect(lambda _=None: self.dismiss())
        self.query.installEventFilter(self)
        self.hide()

    def set_query(self, text: str):
        # Blocked, or restoring a tab's query would look like typing and search
        self.query.blockSignals(True)
        self.query.setText(text)
        self.query.blockSignals(False)

    def place(self):
        """Top right of the page area, in the tab widget's coordinates. The page is a
        grandchild -- it sits inside the tab widget's own stack -- so its offset has to
        be mapped rather than read off its geometry, which is relative to that stack."""
        area = self.window.tabs
        page = area.currentWidget()
        top = page.mapTo(area, QtCore.QPoint(0, 0)).y() if page else 0
        self.adjustSize()
        self.move(area.width() - self.width() - self.MARGIN, top + self.MARGIN)

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
        tab.find_open = True
        self.set_query(tab.find_query)
        self.status.setText(tab.find_status)
        self.place()
        self.show()
        self.raise_()
        self.query.setFocus()
        self.query.selectAll()

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
        return super().eventFilter(obj, event)
```

- [ ] **Step 5: Wire it into `BrowserWindow`**

In `BrowserWindow.__init__`, immediately after the tab widget block (after the `tabMoved` connect, currently line 427) and before the shortcut block:

```python
        self.find = FindBar(self)
        # Window resize reaches the tab widget; the tab bar appearing or going away
        # does not, and it moves the page area under the bar
        self.tabs.installEventFilter(self)
```

Then extend the shortcut block (currently lines 429-436) with a second registration below it:

```python
        if buttons:
            # Not gated on tabs like the shortcuts above: those act on tabs, find acts
            # on a page. This keeps it out of the chrome-less login and cookie windows
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self).activated.connect(self.find.activate)
```

Replace `BrowserWindow.eventFilter` (currently lines 465-472) in full:

```python
    def eventFilter(self, obj, event):
        if obj is self.tabs:
            # The window was resized, so the page area moved under the find bar
            if event.type() is QtCore.QEvent.Type.Resize:
                self.find.reposition()
        elif obj is self.tabs.tabBar():
            # The tab bar coming or going moves the page area down or up without the
            # tab widget resizing at all, so it needs an event of its own
            if event.type() in (QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Hide):
                self.find.reposition()
            if event.type() is QtCore.QEvent.Type.MouseButtonRelease:
                if event.button() is QtCore.Qt.MouseButton.MiddleButton:
                    # tabAt returns -1 off the end of the strip, where a click closes nothing
                    if (index := obj.tabAt(event.position().toPoint())) >= 0:
                        self.close_tab(index)
                        return True
        return super().eventFilter(obj, event)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python test_webview_find.py`
Expected: PASS, prints `ok`

- [ ] **Step 7: Check it by eye, once**

The automated test can only prove geometry. Whether the bar actually draws *above* the web view is the one thing offscreen Qt cannot answer, and it is the main risk in parenting it next to the view.

Run: `python main.py`, open any game's thread in the integrated browser, press Ctrl+F.
Expected: the bar is visible over the top right of the page, above page content, and typing into it works.

- [ ] **Step 8: Commit**

```bash
git add modules/webview_window.py test_webview_find.py
git commit -m "feat: a find bar over the page, opened with Ctrl+F"
```

---

### Task 2: Searching and the match counter

**Files:**
- Modify: `modules/webview_window.py` — `FindBar`
- Test: `test_webview_find.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces:
  - `FindBar.search(backward: bool = False) -> None` — search the current tab with the text in the box
  - `FindBar.run(tab: WebTab, backward: bool = False) -> None` — search one named tab, which need not be on screen
  - `FindBar.found(tab: WebTab, result) -> None` — `findText` callback, `result` is a `QWebEngineFindTextResult`
  - `FindBar.set_status(tab: WebTab, text: str) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `test_webview_find.py`, above the `__main__` block:

```python
def test_typing_reports_every_match():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, finish)
    def finish():
        seen.append(window.find.status.text())
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == ["1/3"], f"counter showed {seen}, expected the first of three matches"


def test_a_query_that_matches_nothing_says_so():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("giraffe")
        QtCore.QTimer.singleShot(500, finish)
    def finish():
        seen.append(window.find.status.text())
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == ["0/0"], f"counter showed {seen}, expected no matches"


def test_emptying_the_box_clears_the_counter():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, clear)
    def clear():
        # Qt never calls the callback for an empty string, so a stale counter would
        # sit there forever
        window.find.query.setText("")
        QtCore.QTimer.singleShot(500, finish)
    def finish():
        seen.append((window.find.status.text(), tab.find_status))
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == [("", "")], f"clearing the box left {seen}"


def test_escape_closes_but_keeps_the_query():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, escape)
    def escape():
        QTest.keyClick(window.find.query, QtCore.Qt.Key.Key_Escape)
        seen.append((window.find.isVisible(), tab.find_open, tab.find_query))
        # Ctrl+F again starts from the last query, selected, so typing replaces it
        window.find.activate()
        seen.append((window.find.query.text(), window.find.query.selectedText()))
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == [(False, False, "cat"), ("cat", "cat")], f"escape left {seen}"
```

Register them in `__main__`:

```python
        "count": test_typing_reports_every_match,
        "nomatch": test_a_query_that_matches_nothing_says_so,
        "empty": test_emptying_the_box_clears_the_counter,
        "escape": test_escape_closes_but_keeps_the_query,
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python test_webview_find.py count`
Expected: FAIL — `counter showed [''], expected the first of three matches` (nothing searches yet)

- [ ] **Step 3: Implement searching**

Add to `FindBar`, after `set_query`:

```python
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
            lambda result: self.found(tab, result),
        )

    def found(self, tab: "WebTab", result):
        self.set_status(tab, f"{result.activeMatch()}/{result.numberOfMatches()}")

    def set_status(self, tab: "WebTab", text: str):
        tab.find_status = text
        if tab.is_current:  # a background tab must never drive the chrome
            self.status.setText(text)
```

Connect typing, in `FindBar.__init__` next to the `done.clicked` connect:

```python
        self.query.textChanged.connect(lambda _: self.search())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python test_webview_find.py`
Expected: PASS, prints `ok`

- [ ] **Step 5: Commit**

```bash
git add modules/webview_window.py test_webview_find.py
git commit -m "feat: search the page from the find bar, with a match counter"
```

---

### Task 3: Stepping through matches

**Files:**
- Modify: `modules/webview_window.py` — `FindBar.__init__`, `FindBar.eventFilter`
- Test: `test_webview_find.py`

**Interfaces:**
- Consumes: `FindBar.search(backward)` from Task 2.
- Produces: no new names. Enter and the `next` button call `search(backward=False)`; Shift+Enter and `prev` call `search(backward=True)`.

- [ ] **Step 1: Write the failing tests**

Add to `test_webview_find.py`:

```python
def test_enter_advances_and_shift_enter_goes_back():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, forward)
    def forward():
        seen.append(window.find.status.text())
        QTest.keyClick(window.find.query, QtCore.Qt.Key.Key_Return)
        QtCore.QTimer.singleShot(500, backward)
    def backward():
        seen.append(window.find.status.text())
        QTest.keyClick(
            window.find.query, QtCore.Qt.Key.Key_Return,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        QtCore.QTimer.singleShot(500, finish)
    def finish():
        seen.append(window.find.status.text())
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == ["1/3", "2/3", "1/3"], f"stepping went {seen}"


def test_the_buttons_step_too():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, forward)
    def forward():
        window.find.next.click()
        QtCore.QTimer.singleShot(500, backward)
    def backward():
        seen.append(window.find.status.text())
        window.find.prev.click()
        QtCore.QTimer.singleShot(500, finish)
    def finish():
        seen.append(window.find.status.text())
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == ["2/3", "1/3"], f"the buttons stepped {seen}"
```

Register them in `__main__`:

```python
        "step": test_enter_advances_and_shift_enter_goes_back,
        "buttons": test_the_buttons_step_too,
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python test_webview_find.py step`
Expected: FAIL — `stepping went ['1/3', '1/3', '1/3']`; Return is swallowed by the line edit and nothing steps

- [ ] **Step 3: Implement stepping**

In `FindBar.__init__`, next to the other connects:

```python
        self.prev.clicked.connect(lambda _=None: self.search(backward=True))
        self.next.clicked.connect(lambda _=None: self.search(backward=False))
```

In `FindBar.eventFilter`, add to the `KeyPress` branch, below the Escape check:

```python
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                # Not returnPressed: that signal carries no modifiers, and Shift+Enter
                # for the previous match is the binding every browser has
                self.search(backward=bool(
                    event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                ))
                return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python test_webview_find.py`
Expected: PASS, prints `ok`

- [ ] **Step 5: Commit**

```bash
git add modules/webview_window.py test_webview_find.py
git commit -m "feat: step through matches with enter, shift enter and the buttons"
```

---

### Task 4: Each tab keeps its own search

**Files:**
- Modify: `modules/webview_window.py` — new `FindBar.follow`, `BrowserWindow.tab_changed` (lines 505-514)
- Test: `test_webview_find.py`

**Interfaces:**
- Consumes: `WebTab.find_open` / `find_query` / `find_status`, `FindBar.set_query`, `FindBar.place`.
- Produces: `FindBar.follow(tab: WebTab) -> None` — mirror a tab that just became current.

- [ ] **Step 1: Write the failing test**

Add to `test_webview_find.py`:

```python
def test_each_tab_keeps_its_own_search():
    app, window = browser()
    first = window.new_tab()
    second = window.new_tab(background=True)
    seen = []
    def search_first():
        window.tabs.setCurrentIndex(0)
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, search_second)
    def search_second():
        seen.append(("first", window.find.status.text()))
        window.tabs.setCurrentIndex(1)
        window.find.activate()
        window.find.query.setText("dog")
        QtCore.QTimer.singleShot(500, back_to_first)
    def back_to_first():
        seen.append(("second", window.find.query.text(), window.find.status.text()))
        window.tabs.setCurrentIndex(0)
        # No wait: switching back must restore from the tab, not re-run the search.
        # Re-running would advance the match, silently moving the tab off 1/3
        seen.append(("first again", window.find.query.text(), window.find.status.text()))
        window.tabs.setCurrentIndex(1)
        window.find.dismiss()
        window.tabs.setCurrentIndex(0)
        seen.append(("still open", window.find.isVisible(), window.find.query.text()))
        app.quit()
    loaded(app, first, HTML, lambda: None)
    second.page.setHtml(OTHER, QtCore.QUrl(PAGE))
    QtCore.QTimer.singleShot(1500, search_first)
    app.exec()
    assert seen == [
        ("first", "1/3"),
        ("second", "dog", "1/2"),
        ("first again", "cat", "1/3"),
        ("still open", True, "cat"),
    ], f"tab state went {seen}"


def test_a_tab_with_no_search_hides_the_bar():
    app, window = browser()
    first = window.new_tab()
    window.new_tab(background=True)
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, switch)
    def switch():
        window.tabs.setCurrentIndex(1)
        seen.append(window.find.isVisible())
        window.tabs.setCurrentIndex(0)
        seen.append(window.find.isVisible())
        app.quit()
    loaded(app, first, HTML, search)
    app.exec()
    assert seen == [False, True], f"bar visibility across tabs went {seen}"
```

Register them in `__main__`:

```python
        "pertab": test_each_tab_keeps_its_own_search,
        "hides": test_a_tab_with_no_search_hides_the_bar,
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python test_webview_find.py hides`
Expected: FAIL — `bar visibility across tabs went [True, True]`; the bar follows nothing and stays up on a tab that never searched

- [ ] **Step 3: Implement following**

Add to `FindBar`, after `activate`:

```python
    def follow(self, tab: "WebTab"):
        """Mirror a tab that just became current. Deliberately does not re-run the
        search: highlights belong to the page and survive the view being hidden, and
        findText with an unchanged query advances to the next match, so re-running
        would silently walk a tab off the match it was showing."""
        if not tab.find_open:
            self.hide()
            return
        self.set_query(tab.find_query)
        self.status.setText(tab.find_status)
        self.place()
        self.show()
        self.raise_()
```

In `BrowserWindow.tab_changed`, after the existing `if not tab: return`:

```python
        self.find.follow(tab)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python test_webview_find.py`
Expected: PASS, prints `ok`

- [ ] **Step 5: Commit**

```bash
git add modules/webview_window.py test_webview_find.py
git commit -m "feat: keep a find in each tab, and follow whichever is current"
```

---

### Task 5: Searching again after the page changes

**Files:**
- Modify: `modules/webview_window.py` — `WebTab.load_finished` (lines 301-312)
- Test: `test_webview_find.py`

**Interfaces:**
- Consumes: `FindBar.run(tab, backward)` from Task 2, `WebTab.find_open` / `find_query`.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

Add to `test_webview_find.py`:

```python
def test_navigating_searches_the_new_page():
    app, window = browser()
    tab = window.new_tab()
    seen = []
    def search():
        window.find.activate()
        window.find.query.setText("cat")
        QtCore.QTimer.singleShot(500, navigate)
    def navigate():
        seen.append(window.find.status.text())
        # Highlights die with the old document, so a stale 1/3 over a page with no cat
        # in it would be a lie
        tab.page.setHtml(OTHER, QtCore.QUrl(PAGE))
        QtCore.QTimer.singleShot(1500, finish)
    def finish():
        seen.append(window.find.status.text())
        app.quit()
    loaded(app, tab, HTML, search)
    app.exec()
    assert seen == ["1/3", "0/0"], f"counter across a navigation went {seen}"
```

Register it in `__main__`:

```python
        "navigate": test_navigating_searches_the_new_page,
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python test_webview_find.py navigate`
Expected: FAIL — `counter across a navigation went ['1/3', '1/3']`; the counter still describes the page that is gone

- [ ] **Step 3: Implement the re-run**

In `WebTab.load_finished`, after `self.inject("\nupdateIcons();")` and **before** the `probe` block that can close this tab:

```python
        if self.find_open and self.find_query:
            # Highlights die with the old document, so a tab with the bar open searches
            # the page that replaced it. Background tabs too -- set_status keeps a tab
            # that is not current from writing the counter. Before the probe check
            # below, which can delete this tab's view out from under findText
            self.window.find.run(self)
```

- [ ] **Step 4: Run the whole suite**

Run: `python test_webview_find.py`
Expected: PASS, prints `ok`

- [ ] **Step 5: Run the neighbouring suites for regressions**

The new `eventFilter` branches and the `tab_changed` call sit in code those tests already cover.

Run: `python test_webview_block.py`
Expected: PASS, prints `ok`

- [ ] **Step 6: Update the changelog**

In `CHANGELOG.md`, under `### Added:`, below the existing browser entries:

```markdown
- Find in page in the integrated browser: Ctrl+F, with a match counter and Enter / Shift+Enter to step through matches
```

- [ ] **Step 7: Commit**

```bash
git add modules/webview_window.py test_webview_find.py CHANGELOG.md
git commit -m "feat: search the new page when a tab with find open navigates"
```
