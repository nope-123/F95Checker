# Built-in browser: find in page

Date: 2026-08-09
Status: implemented

## Goal

Ctrl+F in the integrated browser: a floating find bar over the top-right of the
page that searches the current tab, highlights every match, reports `2/17`, and
steps through matches with Enter and Shift+Enter.

All work is confined to `modules/webview_window.py` plus a new test file. The
main ImGui application and `modules/webview.py` are untouched, and no new
setting is added.

Version note: Windows and Linux run PyQt6 6.7.1 / PyQt6-WebEngine 6.7.0, macOS
runs 6.4.2 / 6.4.0 (`requirements.txt:22-25`). Everything used here —
`QWebEnginePage.findText()` with a result callback, `FindFlag.FindBackward`,
and `QWebEngineFindTextResult.numberOfMatches()`/`activeMatch()` — exists in
6.4, so no version floor changes.

## Why Qt's own find and not JavaScript

`findText()` is Chromium's find implementation reached through Qt. It highlights
every match, paints the active one differently, scrolls it into view, and
crosses iframe boundaries. A JS implementation would have to walk the DOM,
inject its own marks, and would still miss cross-origin frames — for a strictly
worse result.

The call is asynchronous:

```python
page.findText(query, flags, lambda result: ...)
```

The callback receives a `QWebEngineFindTextResult` with `numberOfMatches()` and
`activeMatch()`, so the counter costs nothing extra. The callback form is used
rather than the `findTextFinished` signal specifically to avoid per-tab signal
wiring: the callback closes over the tab it was issued for.

Qt does not invoke the callback when the search string is empty, so the empty
case never reaches `findText` with a callback — it is handled directly, or the
counter would show a stale value forever.

## The widget

A `FindBar(QtWidgets.QWidget)` class in `modules/webview_window.py`, declared
alongside `CookieJar` and `WebTab`. It stays in that file: it is browser chrome,
the same as the URL bar and progress bar that `BrowserWindow.__init__` builds
inline, and the window subprocess already owns every other piece of the browser.

Contents, left to right: a `QLineEdit` for the query, a `QLabel` counter, a
previous button, a next button, and a close button. The existing chrome uses
Nerd Font glyphs for its buttons (`modules/webview_window.py:381-385`), so this
one does too.

### Parenting and position

The bar is a child of `self.tabs`, the `QTabWidget`, and is raised above it.

Not a child of a `QWebEngineView`: the view is backed by its own composited
surface, and a child widget over it is the fragile arrangement. A raised sibling
inside the tab widget is a plain Qt stacking question. `self.tabs` is also
exactly the rectangle the page occupies, so top-right of the tab widget is
top-right of the page, with no coordinate mapping.

One bar per window, not per tab. Only one tab is visible at a time, so a bar per
tab would be N widgets to show exactly one of.

Position is set by `move()` to the top-right of the page area minus a fixed
margin. The page widget is a grandchild — it lives inside the tab widget's own
stack — so its offset is read with `mapTo()` rather than off its geometry, which
is relative to that stack and reads `y=0` whether the tab bar is showing or not.

Repositioning is driven from `BrowserWindow.eventFilter`, already installed on
the tab bar for middle-click-to-close
(`modules/webview_window.py:465-472`), on two events: a `Resize` of the tab
widget, which is the window being resized, and a `Show`/`Hide` of the tab bar,
which is the second tab appearing or the last one closing
(`modules/webview_window.py:455`, `485`). The tab bar case needs its own event
because it moves the page area down without the tab widget resizing at all.

Repositioning on those events is deferred by one event-loop tick
(`QTimer.singleShot(0, ...)`). Measured on this repo's PyQt6: when the tab bar's
`Show` arrives, Qt has not yet moved the page area, so placing immediately uses
a layout one pass out of date and leaves the bar sitting on the tab bar.
Showing the bar places it directly instead, with the layout already settled.

The bar is also placed when shown, since its width follows its size hint.

## Behaviour

Ctrl+F shows the bar, raises it, focuses the line edit and selects any text
already in it, so a second Ctrl+F replaces the previous query by typing.

The shortcut is registered when `buttons` is true. The existing Ctrl+T / Ctrl+W
/ Ctrl+Tab shortcuts require `buttons and tabs`
(`modules/webview_window.py:429-436`) because they act on tabs; find does not,
and only needs a window with chrome. This leaves it out of the chrome-less login
and cookie windows (`modules/webview.py:178`), which show one page nobody reads.

- Typing searches incrementally on every `textChanged`.
- Enter searches forward, Shift+Enter searches backward with
  `FindFlag.FindBackward`. Chromium wraps at either end.
- The counter shows `activeMatch()/numberOfMatches()`; no match shows `0/0`.
- Esc and the close button hide the bar and clear the highlights with
  `findText("")`.
- Clearing the query to empty clears the highlights and blanks the counter, but
  leaves the bar open.

Case sensitivity is not exposed. `FindCaseSensitively` exists, but the search is
case-insensitive like every browser's default, and a toggle nobody asked for is
a button on every window forever.

## Per-tab state

Each `WebTab` carries its own find state, in the same place it already carries
`loading` and `probe`:

- `find_open` — whether the bar is showing for this tab
- `find_query` — its last query, kept after closing so Ctrl+F prefills it
- `find_status` — its counter text

Switching tabs makes the bar mirror the new current tab: restore the query and
counter, then show or hide. Signals are blocked while setting the line edit
text, or restoring a query would fire `textChanged` and search.

Switching tabs neither clears highlights nor re-runs the search. Highlights
belong to the page and survive the view being hidden inside the tab widget, so
each tab keeps its own visible matches with no work at all. Re-running would be
actively wrong: `findText` with an unchanged query advances to the *next* match,
so returning to a tab showing match 2 would silently jump it to match 3.

Searches are issued against `tab.page` and the result callback stores the
counter on that tab, writing the label only when that tab is current. This is
the rule `set_progress` already follows — "a background tab must never drive the
chrome" (`modules/webview_window.py:534-538`).

A tab with `find_open` re-runs its query from `load_finished`, because
highlights die with the old document and a stale `2/17` over a fresh page is a
lie. Background tabs re-run as well, which the callback rule keeps off the
chrome. Within-tab navigation otherwise leaves the bar alone, open and holding
its query.

Closing a tab deletes its view and its state with it
(`modules/webview_window.py:474-485`). There is nothing to unregister: the bar
holds no per-tab dictionary, only a reference to whichever tab is current.

## Testing

A new `test_webview_find.py`, in the shape of `test_webview_block.py`: offscreen
Qt, pages built with `setHtml()`, no network. It reuses that file's structure —
`browser()` to build a window, `tabs_after()`-style timed loop runs, and `run()`
to execute one test — because `findText` is asynchronous and the event loop has
to spin for the callback to arrive.

Cases:

- a page with a known number of occurrences reports the right total, and Enter
  advances `activeMatch`
- Shift+Enter steps backward
- a query that matches nothing shows `0/0`
- two tabs hold separate queries across a switch, and the bar shows the current
  tab's query and counter
- Esc clears the highlights and leaves `find_query` for the next Ctrl+F
- a tab with the bar open re-runs its query after navigating to a new page

## Out of scope

- Case-sensitivity, whole-word and regex options
- Highlight-all as a toggle — Chromium always highlights all matches
- A match-position scrollbar
- Find across all tabs
- F3 / Ctrl+G as extra next-match shortcuts
