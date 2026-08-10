# Browser Close Warning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Closing the integrated browser while it shows more than one tab asks for confirmation first, instead of taking every tab down silently.

**Architecture:** One guard at the top of `BrowserWindow.closeEvent`, which every close path already funnels through — the title-bar button, Alt+F4, and `close_tab` closing the last tab. A `QMessageBox.question` returning anything but `Yes` calls `close.ignore()` and returns before the existing teardown. A second, smaller change themes `QMessageBox` from the window stylesheet so the dialog matches the browser instead of the platform default.

**Tech Stack:** Python 3.11+, PyQt6 / PyQt6-WebEngine (`requirements.txt:22-25`). The browser runs as a subprocess; all of it lives in `modules/webview_window.py`.

**Spec:** `docs/superpowers/specs/2026-08-10-browser-close-warning-design.md`

## Global Constraints

- All production changes are confined to `modules/webview_window.py`. `modules/webview.py`, `modules/db.py` and `modules/gui.py` are not touched.
- No new setting, no new dependency, no "don't ask again" checkbox.
- The prompt fires only when the window holds **two or more** tabs. A single-tab close stays silent.
- Dialog title: `Close browser`. Dialog text: `This window has {n} tabs open. Close them all?` — exact wording, with the live tab count substituted.
- Buttons are `Yes | No`, with `defaultButton` explicitly `No`.
- Tests run offscreen (`QT_QPA_PLATFORM=offscreen`) and touch no network.
- One `QApplication` per process: each test case runs as its own subprocess, dispatched from the `__main__` block. This is the existing convention in `test_webview_find.py:289-313` and `test_webview_block.py`.

---

## File Structure

**Modify: `modules/webview_window.py`**
- `BrowserWindow.closeEvent` (currently lines 724-728) — gains the confirmation guard. This is the whole behaviour change.
- The stylesheet f-string inside `create()` (currently lines 919-970) — gains four unscoped `QMessageBox` rules, next to the existing unscoped `QMenu` rules.

**Create: `test_webview_close.py`** (repo root, alongside `test_webview_find.py` and `test_webview_block.py` — this repo keeps its tests at the root, not in a `tests/` directory)
- Four cases covering the guard. No page is ever loaded: the guard reads `tab_list` and never touches page content, so these cases need no `setHtml` and no asynchronous settling, which is what makes this file simpler than `test_webview_find.py`.

**Modify: `CHANGELOG.md`**
- One line under `### Added:`, matching the existing browser entries.

---

### Task 1: The confirmation guard

**Files:**
- Create: `test_webview_close.py`
- Modify: `modules/webview_window.py:724-728` (`BrowserWindow.closeEvent`)

**Interfaces:**
- Consumes: `BrowserWindow(buttons, tabs, private, icon, background_color, extension, rpcproxy, proxy_auth, title)` — keyword-only constructor, `modules/webview_window.py:498-509`. `BrowserWindow.new_tab(url=None, background=False) -> WebTab`, `:627`. `BrowserWindow.tab_list: list[WebTab]`, `:525`. `BrowserWindow.tabs_enabled: bool`, `:513`. `config_qt_flags(debug, software)`.
- Produces: no new public names. `closeEvent` keeps its signature and its existing teardown behaviour whenever the close is allowed.

**Background the implementer needs:**

`QWidget.close()` returns `True` if the widget actually closed and `False` if the close event was ignored. That return value is what the tests assert on, so no signal wiring is needed. Verified in this environment offscreen.

A real `QMessageBox` runs its own nested event loop and would hang the test run forever, so each case replaces `QtWidgets.QMessageBox.question` with a stub. PyQt6 allows assigning over that static method; also verified in this environment. The stub is never restored, because one `QApplication` per process means each case is its own process, which exits immediately after.

- [ ] **Step 1: Write the failing tests for the tab-count guard**

Create `test_webview_close.py` with exactly this content:

```python
#!/usr/bin/env python
# Run: python test_webview_close.py
# Needs PyQt6 + QtWebEngine (like test_webview_find.py, unlike test_blocklist.py),
# runs offscreen and touches no network: the guard reads tab_list and never looks at
# page content, so no case here loads a page at all.
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from modules.webview_window import (
    BrowserWindow,
    config_qt_flags,
)

config_qt_flags(debug=False, software=True)

from PyQt6 import (  # noqa: E402
    QtGui,
    QtWidgets,
)


def browser(tabs=True):
    app = QtWidgets.QApplication(sys.argv)
    window = BrowserWindow(
        buttons=True, tabs=tabs, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(800, 600)
    window.show()
    return app, window


def stub_question(answer):
    """A real message box spins its own event loop and would hang the run, so every
    case answers for the user. Replaced and never restored: one QApplication per
    process means each case is its own process, and it exits right after."""
    asked = []
    def question(parent, title, text, *args):
        asked.append(text)
        return answer
    QtWidgets.QMessageBox.question = staticmethod(question)
    return asked


def test_one_tab_closes_without_asking():
    app, window = browser()
    window.new_tab()
    asked = stub_question(QtWidgets.QMessageBox.StandardButton.No)
    assert window.close() is True, "a single tab window refused to close"
    assert not window.isVisible(), "the window stayed up"
    assert asked == [], f"it asked about a single tab: {asked}"


def test_two_tabs_and_no_keeps_the_window():
    app, window = browser()
    window.new_tab()
    window.new_tab(background=True)
    asked = stub_question(QtWidgets.QMessageBox.StandardButton.No)
    assert window.close() is False, "No did not veto the close"
    assert window.isVisible(), "the window closed anyway"
    # The teardown deletes every view, so a veto that fell through to it would leave
    # this window up and empty
    assert len(window.tab_list) == 2, f"a vetoed close still took tabs: {len(window.tab_list)}"
    assert len(asked) == 1, f"expected one prompt, got {asked}"
    assert "2 tabs" in asked[0], f"the prompt did not name the tab count: {asked[0]!r}"


def test_two_tabs_and_yes_closes():
    app, window = browser()
    window.new_tab()
    window.new_tab(background=True)
    asked = stub_question(QtWidgets.QMessageBox.StandardButton.Yes)
    assert window.close() is True, "Yes did not close the window"
    assert not window.isVisible(), "the window stayed up"
    assert window.tab_list == [], f"teardown left {len(window.tab_list)} tabs"
    assert len(asked) == 1, f"expected one prompt, got {asked}"


if __name__ == "__main__":
    tests = {
        "single": test_one_tab_closes_without_asking,
        "veto": test_two_tabs_and_no_keeps_the_window,
        "confirm": test_two_tabs_and_yes_closes,
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

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python test_webview_close.py`

Expected: FAIL. The `veto` case is the one that must fail — `closeEvent` accepts unconditionally today, so `window.close()` returns `True` and the case dies with `AssertionError: No did not veto the close`. Because the runner spawns each case with `check=True`, the parent stops there and re-raises as `subprocess.CalledProcessError`; run `python test_webview_close.py veto` to see the assertion on its own.

The `single` case passes already, which is correct: it describes behaviour that must not change. `confirm` is not reached in this run.

- [ ] **Step 3: Add the guard to `closeEvent`**

In `modules/webview_window.py`, replace the whole of `closeEvent` (currently lines 724-728) with:

```python
    def closeEvent(self, close: QtGui.QCloseEvent):
        # Every close path lands here -- the title bar button, Alt+F4, and close_tab
        # closing the last tab -- so one guard covers all of them and no caller needs
        # to know it exists. That last path can never prompt: it only reaches close()
        # with a single tab left, which is below the threshold
        if len(self.tab_list) > 1 and QtWidgets.QMessageBox.question(
            self, "Close browser",
            f"This window has {len(self.tab_list)} tabs open. Close them all?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            # Explicit, rather than letting the platform pick: a stray Enter on a dialog
            # that just appeared must not discard a window full of tabs
            QtWidgets.QMessageBox.StandardButton.No,
        ) is not QtWidgets.QMessageBox.StandardButton.Yes:
            # Returning is load-bearing. An ignored close that fell through to the
            # teardown below would leave the window up with every one of its views
            # already deleted
            close.ignore()
            return
        close.accept()
        for tab in self.tab_list:
            tab.view.deleteLater()
        self.tab_list.clear()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python test_webview_close.py`

Expected: PASS, printing `ok`.

- [ ] **Step 5: Write the failing test for the chrome-less windows**

Append this case to `test_webview_close.py`, immediately after `test_two_tabs_and_yes_closes`:

```python
def test_a_window_without_tabs_never_asks():
    """The login and DDL resolver windows pass tabs=False (modules/webview.py:178,
    :200, :239). They cannot reach a second tab through the UI -- popups load in place
    there (modules/webview_window.py:167-172) and off-site navigations return early
    (:200-201) -- so the second tab here is built directly. The point is the guard
    itself: a modal in one of those windows would block a flow the main application is
    awaiting on the pipe, so that has to be unreachable rather than merely unlikely."""
    app, window = browser(tabs=False)
    window.new_tab()
    window.new_tab(background=True)
    asked = stub_question(QtWidgets.QMessageBox.StandardButton.No)
    assert window.close() is True, "a chrome-less window refused to close"
    assert asked == [], f"a chrome-less window asked: {asked}"
```

And add its entry to the `tests` dict in `__main__`, after `"confirm"`:

```python
        "chromeless": test_a_window_without_tabs_never_asks,
```

- [ ] **Step 6: Run the tests to verify the new case fails**

Run: `python test_webview_close.py chromeless`

Expected: FAIL with `AssertionError: a chrome-less window refused to close`. The guard added in Step 3 only counts tabs, so it prompts here too, and the stubbed `No` vetoes the close.

- [ ] **Step 7: Gate the guard on `tabs_enabled`**

In the `closeEvent` written in Step 3, change the condition's first line from:

```python
        if len(self.tab_list) > 1 and QtWidgets.QMessageBox.question(
```

to:

```python
        # tabs_enabled as well as the count: see test_webview_close.py's chrome-less case
        if self.tabs_enabled and len(self.tab_list) > 1 and QtWidgets.QMessageBox.question(
```

- [ ] **Step 8: Run the whole file to verify every case passes**

Run: `python test_webview_close.py`

Expected: PASS, printing `ok`.

- [ ] **Step 9: Run the neighbouring browser tests for regressions**

Run: `python test_webview_find.py` then `python test_webview_block.py`

Expected: both print `ok`. Neither closes a multi-tab window, so neither should change. If either fails, report the failure rather than adjusting it — a break here means the guard reached a path it should not have.

- [ ] **Step 10: Commit**

```bash
git add test_webview_close.py modules/webview_window.py
git commit -m "feat: ask before closing a browser window with tabs open"
```

---

### Task 2: Theme the message box

**Files:**
- Modify: `modules/webview_window.py:919-970` (the stylesheet f-string in `create()`)
- Modify: `CHANGELOG.md:1-5`

**Interfaces:**
- Consumes: the style values already parameters of `create()` — `style_bg`, `style_text`, `style_accent`, `style_corner_radius` (`modules/webview_window.py:786-790`).
- Produces: nothing other code calls.

**Why there is no automated test here:** the stylesheet is applied inside `create()`, which builds a `QApplication`, loads the icon font and the extension, and is not callable from the offscreen harness without standing up the whole subprocess. This is a string of CSS with no behaviour to assert on, and `test_webview_find.py` does not test the chrome's styling either. It is verified by looking at it, in Step 3.

- [ ] **Step 1: Add the `QMessageBox` rules to the stylesheet**

In `modules/webview_window.py`, inside the `app.window.setStyleSheet(f"""...""")` call, insert these blocks after the closing brace of the existing `QMenu::icon` rule (currently line 969) and before the closing `"""` (currently line 970). The doubled braces are required — this is an f-string:

```
        QMessageBox {{
            background: {style_bg};
        }}
        QMessageBox QLabel {{
            color: {style_text};
        }}
        QMessageBox QPushButton {{
            background: {style_bg};
            color: {style_text};
            border-radius: {style_corner_radius};
            padding: 5px 12px;
        }}
        QMessageBox QPushButton:hover {{
            background: {style_accent};
        }}
```

Unscoped on purpose, like the `QMenu` rules directly above: the download-manager failure warning (`modules/webview_window.py:904-909`) is themed by them too, rather than staying the one unstyled dialog in the browser.

Do not add `font-family` here. The Nerd Font glyph family is applied only to `#controls QPushButton` and `#findbar QPushButton`, and these buttons carry plain text labels that must render in the normal font.

- [ ] **Step 2: Check the file still parses**

Run: `python -c "import ast, pathlib; ast.parse(pathlib.Path('modules/webview_window.py').read_text(encoding='utf-8')); print('ok')"`

Expected: prints `ok`. An unescaped `{` in that f-string is the likely mistake, and this catches it without launching anything.

- [ ] **Step 3: Hand the visual check to the user**

This step opens a real browser window on the user's desktop. Tell them that before running anything, then let them look at it themselves — do not screenshot their screen.

Ask them to run the app, open a game thread in the integrated browser, press Ctrl+T for a second tab, then close the window with the title-bar button, and confirm:

- the dialog's background, text and buttons follow their theme rather than the system palette
- the button labels read `Yes` and `No` in the normal font, not as glyph boxes
- `No` leaves the window open with both tabs; `Yes` closes it

- [ ] **Step 4: Add the CHANGELOG entry**

In `CHANGELOG.md`, add this line under `### Added:`, after the existing find-in-page line (line 3):

```markdown
- Warning before closing the integrated browser with more than one tab open
```

- [ ] **Step 5: Commit**

```bash
git add modules/webview_window.py CHANGELOG.md
git commit -m "fix: theme the browser's message boxes"
```

---

## Out of Scope

Carried from the spec — do not build these:

- A setting, or a "don't ask again" checkbox
- Warning on a single-tab close
- Session restore, or reopening a closed window's tabs
- Honouring a page's own `beforeunload` handler
- Warning about in-flight downloads
