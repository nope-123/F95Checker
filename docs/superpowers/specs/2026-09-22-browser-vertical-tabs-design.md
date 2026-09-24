# Built-in browser: vertical tabs with search

Date: 2026-09-22
Status: implemented

## Goal

The integrated browser can show its tabs as a list down the left side instead of
the strip along the top, with a search box that filters that list. A toolbar
button switches between the two, and the choice is remembered.

The side list is for many tabs: titles stay readable at any count, where the top
strip squeezes them to a few characters and then scrolls. Search is for getting
back to one of those tabs without reading the whole list.

All work is confined to `modules/webview_window.py` plus a new test file.
`modules/webview_window.py` exists only in this fork, so upstream's files --
`modules/db.py`, `common/structs.py`, `modules/gui.py`, `modules/webview.py` --
are untouched, and so is the main ImGui application.

## Scope

Only the main browser window, the one built with `buttons=True, tabs=True`. The
login and resolver windows pass `tabs=False` (`modules/webview.py:172`, `:197`),
are one page each, and get neither the button nor the sidebar. Gating on
`buttons and tabs` is the same gate the Ctrl+T / Ctrl+W / Ctrl+Tab shortcuts
already use, and the toggle button lives in the toolbar that `buttons` adds.

Not included: site icons in the rows, and collapsing the sidebar to a narrow
strip. The top strip shows no icons today either; both can come later.

## Architecture

### The sidebar is a view of `tab_list`, never a second source of truth

`QTabWidget` stays exactly as it is and keeps owning the pages. Every existing
index -- `current_tab`, `close_tab`, `new_tab`'s opener placement, the
`tabMoved` lambda that keeps `tab_list` in step -- stays a tab bar index, and
none of that code changes.

In vertical mode the tab widget's own bar is hidden and a sidebar appears to its
left. The sidebar holds a search `QLineEdit` above a `QListWidget` with one row
per tab, in `tab_list` order. The list never reorders or removes its own rows:
it is redrawn from `tab_list` whenever that changes. Anything the user does in
the list turns into the call the top strip would have made -- `setCurrentIndex`,
`close_tab`, `tabBar().moveTab` -- and the list follows from the resulting
signal. That keeps one path for every change, whichever strip started it.

Refresh triggers: `new_tab` and `close_tab` (rows added or removed), the tab
bar's `tabMoved` (order), `tab_title_changed` and `url_changed` (row text and
tooltip, and whether the row still matches the search), and `currentChanged`
(which row is selected).

### Layout

The window's vertical layout today is `[controls, tabs]`. `tabs` moves into a
horizontal `QSplitter` as `[sidebar, tabs]`, so the divider can be dragged to
resize the sidebar. The find bar is parented to the tab widget
(`FindBar.__init__`) and places itself in its coordinates, so it stays at the top
right of the page with no change. Hiding or showing the tab bar on a toggle
already sends it the Show/Hide events `eventFilter` repositions it on.

In horizontal mode the sidebar is hidden and the window looks exactly as it
does today.

## Behaviour

### Rows

Each row shows the tab's title, elided with "…" to the sidebar width, and a
tooltip with the full title and the URL. The current tab's row is selected.

- Left click switches to the tab.
- Middle click closes it, as on the top strip.
- An × on the row under the mouse closes it. One button is moved to whichever
  row is hovered rather than one widget per row, which every redraw would have
  to rebuild.
- Ctrl+W still closes the current tab.
- Dragging a row reorders the tab. The drop computes the target index -- before
  the row under the pointer if on its upper half, after it if on its lower half,
  last if below every row -- and calls `tabBar().moveTab`. The list redraws on
  `tabMoved`.
- Dropping a link from a page or another app opens it as a new tab at that same
  target index. The top strip's drop code in `eventFilter` becomes one method,
  `open_dropped(urls, to)`, that both call: the top strip computes `to` from x,
  the list from y.

### Search

- Filters the rows as you type: case-insensitive substring on the title or the
  URL. Hidden rows are only hidden; the tabs themselves are untouched.
- Enter switches to the first visible row, clears the search and gives the page
  focus. Esc clears the search and gives the page focus.
- A left click on a row while filtered switches but keeps the filter, so you can
  look through the matches one by one. Middle click closes without clearing.
- A new tab or a title change while filtered is matched like any other row, so
  a tab that does not match stays hidden until the search is cleared.
- Dragging rows to reorder is off while a filter is active: with rows hidden,
  "between these two rows" is ambiguous. Dropping a link still works.
- Ctrl+Shift+A focuses the search box, as Chrome's tab search does. It does
  nothing in horizontal mode, where there is no box to focus.

### Toggle

Three ways to switch between horizontal and vertical, all calling one method:

- a button at the end of the toolbar
- Ctrl+Shift+, (Edge's shortcut)
- right-clicking the tabs: the top strip, or anywhere on the sidebar list
  including its empty space below the rows, opens a menu with "Turn on vertical
  tabs" or "Turn off vertical tabs", Edge's wording. The top strip is always
  visible now, so this is always in reach. The menu holds only that item; the
  page's own right-click menu is unchanged.

Switching keeps the current tab and every tab's position; it only swaps which
strip is showing.

### Remembered

`QSettings(IniFormat, UserScope, "f95checker", "browser")`, which lands in the
main application's own data folder: `%APPDATA%\f95checker\browser.ini` on
Windows, `~/.config/f95checker/browser.ini` on Linux. Two keys:

- `vertical_tabs` (bool, default false), written on every toggle
- `sidebar_width` (int, default 220), written when the splitter moves

A new window reads both. A setting in the main application instead would mean a
column in `modules/db.py`, a field in `common/structs.py`, a checkbox in
`modules/gui.py` and a kwarg through `create_kwargs()` -- four upstream files --
for a preference the browser both sets and reads by itself.

Private mode remembers them too: they are a layout preference, not browsing data.

## Testing

A new `test_webview_vtabs.py` in the style of `test_webview_close.py`: offscreen,
no network, one process per case. Every case points `QSettings.setPath` at a
temp directory first, so no run touches the real `browser.ini`.

- Toggling hides the tab bar and shows the sidebar, and back; the choice is
  written, and a second window built afterwards opens in vertical mode.
- The right-click menu on the top strip and on the sidebar holds the one toggle
  item, worded for the current mode, and triggering it toggles.
- The list mirrors `tab_list` through `new_tab` (including opener placement),
  `close_tab`, a `moveTab` on the tab bar and a title change.
- Search hides non-matching rows by title and by URL; Enter switches to the
  first match and clears the search.
- A row drag reorders `tab_list` and the tab bar together; it is refused while a
  filter is active.
- A link dropped on the list opens at the row it landed on.
- The login-style window (`tabs=False`) has no sidebar and no toggle.

The existing webview tests must keep passing unchanged, and the GUI check is
yours: open a handful of threads, toggle, search, drag and close from the list.
