# Built-in browser: warn before closing a window with tabs open

Date: 2026-08-10
Status: implemented

## Goal

Closing the integrated browser while it shows more than one tab asks for
confirmation first, instead of taking every tab down silently.

All work is confined to `modules/webview_window.py` plus a new test file. The
main ImGui application and `modules/webview.py` are untouched, and no new
setting is added.

## Scope of the warning

The prompt appears only when the window holds two or more tabs. Closing a
single-tab window stays silent, as it is today. This matches Chrome and Firefox,
and a confirmation on every close is the first thing anyone turns off.

There is no setting and no "don't ask again" checkbox. A setting would mean a
field in `modules/db.py`, a checkbox in `modules/gui.py`, and another kwarg
through `create_kwargs()` in `modules/webview.py` — four files and the main
application, for a dialog that only fires on multi-tab closes. A dialog-local
"don't ask again" would be worse: it lives only as long as the browser
subprocess and comes back next launch.

## The guard

`BrowserWindow.closeEvent` (`modules/webview_window.py:724`) currently accepts
the event and tears down every tab. It gains one branch in front of that:

```python
def closeEvent(self, close: QtGui.QCloseEvent):
    if self.tabs_enabled and len(self.tab_list) > 1:
        button = QtWidgets.QMessageBox.StandardButton
        answer = QtWidgets.QMessageBox.question(
            self, "Close browser",
            f"This window has {len(self.tab_list)} tabs open. Close them all?",
            button.Yes | button.No,
            button.No,
        )
        if answer is not button.Yes:
            close.ignore()
            return
    close.accept()
    # existing teardown, unchanged
```

### Why `closeEvent` and not a button handler

Every way of closing the window already funnels through it: the title-bar close
button, Alt+F4, and `close_tab` calling `self.close()` when the last tab goes
(`modules/webview_window.py:657-660`). One guard covers all three, and no caller
needs to know it exists.

That last path cannot trip the prompt, which is the behaviour we want:
`close_tab` only reaches `self.close()` with a single tab in the list, below the
threshold. Ctrl+W with two or more tabs closes one tab and never reaches
`closeEvent` at all.

### Why the pieces are the way they are

`defaultButton` is passed explicitly as `No`. Left at Qt's `NoButton`, the
platform picks, and a stray Enter on a dialog that just appeared should not
discard a window full of tabs.

The `return` after `close.ignore()` is load-bearing. Without it the ignored
event still falls into the teardown loop below, and the window survives with
every one of its views deleted.

`self.tabs_enabled` is belt-and-braces. The chrome-less resolver and login
windows pass `tabs=False` (`modules/webview.py:178`, `:200`, `:239`) and cannot
reach a second tab today — popups load in place rather than opening a tab
(`modules/webview_window.py:167-172`) and off-site navigations return early
(`:200-201`), both gated on `tabs_enabled`. But a modal in one of those windows
would block a flow the main application is awaiting on the pipe, so the
condition makes that unreachable rather than merely unlikely.

### Application shutdown is unaffected

The main application does not close this window politely; it kills the
subprocess through `DaemonProcess.kill` (`common/structs.py:67-80`). No
`closeEvent` runs, so the prompt cannot stall the application quitting.

## Theming

A `QMessageBox` is a child of the window, so the window stylesheet cascades to
it, but the existing rules are scoped to `#controls *`, `#findbar` and `QMenu`
(`modules/webview_window.py:919-970`). Left alone the dialog renders in the
platform default palette: a bright system box over a dark browser.

Unscoped `QMessageBox` rules join the `QMenu` ones in that same block, drawing
from the style values already passed in:

- `QMessageBox` — `background: {style_bg}`
- `QMessageBox QLabel` — `color: {style_text}`
- `QMessageBox QPushButton` — `{style_bg}` / `{style_text}`, `border: 1px solid
  {style_text_dim}`, `border-radius: {style_corner_radius}`, `min-width` and padding
- `QMessageBox QPushButton:hover` — `background: {style_accent}`
- `QMessageBox QPushButton:focus` — `border-color: {style_accent}`

The border is not decoration. Qt keeps drawing the native button chrome over a
styled background until one is set, and setting it drops the native minimum
button size too — hence the explicit `min-width`, without which the buttons
collapse to the width of the word inside them. The `:focus` rule replaces the
native focus ring that styling the border removes, which is also what marks
`No` as the default when the dialog opens.

Unscoped on purpose: the download-manager failure warning
(`modules/webview_window.py:904-909`) is themed by the same rules, rather than
staying the one unstyled dialog in the browser.

The icon font stays where it is. It is applied only to `#controls QPushButton`
and `#findbar QPushButton`, so the Nerd Font glyph family never reaches these
buttons and their plain text labels render normally.

## Testing

A new `test_webview_close.py`, in the shape of `test_webview_find.py`: offscreen
Qt, no network, a `browser()` helper that builds a `BrowserWindow` directly.

A real modal spins its own event loop and would hang the run, so each case
replaces `QtWidgets.QMessageBox.question` with a stub that records the call and
returns a chosen `StandardButton`. The stub is installed on the `QtWidgets`
imported by `modules.webview_window`, which is the same module object the test
imports, and is never restored: Qt allows one `QApplication` per process, so each
case already runs as its own subprocess and exits immediately after.

Cases:

- one tab: `close()` closes the window with no prompt
- two tabs, stub answers `No`: the prompt was shown, the window is still visible
  and both tabs are still in `tab_list`
- two tabs, stub answers `Yes`: the window closes and `tab_list` is empty
- a `tabs=False` window with two tabs in `tab_list`: closes with no prompt

The last case builds its second tab directly rather than through a popup, since
the point is the `tabs_enabled` guard itself, not the popup gating that normally
keeps such a window at one tab.

## Out of scope

- A setting, or a "don't ask again" checkbox
- Warning on a single-tab close
- Session restore, or reopening a closed window's tabs
- Honouring a page's own `beforeunload` handler
- Warning about in-flight downloads: they are handed to an external manager or a
  save dialog, so there is rarely an in-process download to lose

## Known limitations

Both surfaced in review, both accepted rather than fixed:

- The modal spins its own event loop, so a tab pushed in by the main process
  while the prompt is up is not counted in the text and gets discarded by `Yes`.
  The race pre-dates this feature -- the same interleaving used to just close the
  window -- and it is self-healing, since the parent's next `put()` raises
  `DaemonPipeExit` and respawns. Closing it costs more than the outcome is worth.
- Ending a Windows session with 2+ tabs open puts the browser subprocess on the
  "this app is preventing you from restarting" screen until it times out. Chrome
  and Firefox do the same thing, and it is the one path where the guard delays
  something the user did not initiate.
