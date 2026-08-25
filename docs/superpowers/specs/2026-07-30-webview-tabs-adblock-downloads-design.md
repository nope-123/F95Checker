# Built-in browser: tabs, clipboard, ad blocking, download handoff

Date: 2026-07-30
Status: implemented

## Goal

Make F95Checker's built-in Qt WebEngine browser behave like a real browser:
multiple tabs in one window, working clipboard access, ad blocking, and the
ability to hand downloads to an external download manager such as IDM.

All work is confined to the webview process. The main ImGui application is
untouched except for two new settings.

## Constraint: extensions cannot be loaded

QtWebEngine strips out Chromium's extensions subsystem. There are no `chrome.*`
extension APIs, no way to load a `.crx` or unpacked extension, and no native
messaging host support. This is not a version issue and Qt does not plan to add
it.

`browser/integrated.js` is not a counter-example. It is a purpose-written script
that avoids every extension API — plain DOM manipulation plus a single
`QWebChannel` bridge call for privileged work — so the injection trick it uses
does not generalise to real extensions.

Both goals are therefore met with native Qt hooks instead of extension ports:

- ad blocking via `QWebEngineUrlRequestInterceptor`, which sits in the network
  stack and so catches subresources that page-level JS cannot
- IDM via `QWebEngineProfile.downloadRequested`, already wired at
  `modules/webview.py:331-339`, shelling out to the download manager

Both are simpler and more reliable than a port would have been.

Version note: Windows and Linux run PyQt6 6.7.1 / PyQt6-WebEngine 6.7.0, macOS
runs 6.4.2 / 6.4.0. Every API used here exists in 6.4, so no version floor
changes. (Linux and macOS build targets are currently commented out in
`build.yml`.)

## Architecture

### Module split

`modules/webview.py` currently mixes the process layer, the window, per-view
signal wiring and four entry points into one 507-line file, with a 270-line
`create()` at its centre. Signal handlers close over `app.window.webview` by
name, which cannot work once a window holds N views. Tabs therefore require the
per-view wiring to become per-instance.

Two files:

| File | Owns |
|---|---|
| `modules/webview.py` | `start()`, `create_kwargs()`, `config_qt_flags()`, and the `open` / `cookies` / `css_redirect` / `xpath_redirect` entry points. Public surface unchanged. |
| `modules/webview_window.py` | `WebTab`, the window (chrome, tab bar, shortcuts), and `Blocker`. |

`create()` moves wholesale into `webview_window.py`. A `WebTab` owns one
`QWebEngineView` and all of its own signal wiring, `integrated.js` injection and
context menu. The window owns the chrome — URL bar, back/forward/reload,
progress bar, extension button — and forwards to the active tab. `Blocker`
lives beside the profile it installs on.

### Process model

Today every "open in browser" action spawns its own OS process with its own
window (`modules/webview.py:50-56`). Each of those processes constructs
`QWebEngineProfile("F95Checker")` against the same on-disk storage
(`modules/webview.py:201`), which QtWebEngine does not support.

The full browser becomes a single shared process, spawned lazily:

- `globals.browser_daemon` holds the live `DaemonPipe`, or `None`.
- `open()` checks it. Alive → `pipe.put({"open": url})` and return. Dead or
  absent → spawn, storing the handle.
- `start()` gains `stdin=subprocess.PIPE` so the parent can write to the child.
  The transport already exists: `DaemonPipe.put()` writes JSON lines to the
  child's stdin (`common/structs.py:124-127`) and `ChildPipe.get()` reads them
  (`common/structs.py:160-167`).
- In the child, a daemon thread performs blocking `sys.stdin.readline()` and
  emits a Qt signal carrying the URL; the window turns that into a new tab. A
  thread rather than `QSocketNotifier`, which does not work on non-socket
  handles on Windows.
- When the child exits, the next `open()` sees a dead pipe and spawns a fresh
  one. `DaemonProcess` (`common/structs.py:61-87`) already handles killing it on
  main-process teardown.

`cookies()`, `css_redirect()` and `xpath_redirect()` remain one-off private-mode
subprocesses. They are modal and transient, and they must never gain tabs, the
blocker or the download hook. Restricting the persistent profile to the single
shared browser also resolves the profile collision described above.

**Correction (found during Task 6 — an earlier draft of this document claimed all
three pass `buttons=False`; that is false).** Only `cookies()` forces
`buttons=False` unconditionally (`modules/webview.py:120`). `css_redirect()` and
`xpath_redirect()` force it only when their `minimal` argument is true
(`modules/webview.py:135-140`, `:171-176`), and `callbacks.redirect_masked_link` /
`redirect_xpath_link` pass `minimal=copy` where `copy` **defaults to False**
(`modules/callbacks.py:435`, `:462`), which is the path `modules/gui.py:2459` and
`:2465` take. So the ordinary "resolve and open a DDL link" action runs a resolver
window with full chrome.

Consequently `buttons=False` must not be used as a proxy for "this is a resolver
window". Both resolvers additionally re-resolve `app.window.webview` *inside* their
`url_changed` and `load_progress` closures, so anything that can change which tab is
current will divert their click-through JS and their `loadProgress.disconnect()`.
Those closures bind the view once, immediately after `create()`.

## Clipboard

QtWebEngine defaults `JavascriptCanAccessClipboard` and `JavascriptCanPaste` to
`false`, and `modules/webview.py:218-220` sets only three unrelated attributes.
That is why forum "copy link" buttons and pasting into the forum's rich-text
editor silently do nothing: `navigator.clipboard` is blocked.

Two lines beside the existing attributes:

```python
settings.setAttribute(WebAttribute.JavascriptCanAccessClipboard, True)
settings.setAttribute(WebAttribute.JavascriptCanPaste, True)
```

No setting, no UI. Native Ctrl+C / Ctrl+V inside the page already work through
`QWebEngineView`'s built-in web actions; if they turn out not to, that is a
separate bug to debug, not something this design works around.

## Tabs

A `QTabWidget` with `setTabsClosable(True)` and `setMovable(True)` — close
buttons and drag-reordering come free, so no custom tab bar.

The `QWebEngineProfile` moves up to window scope, created once and passed to
every view, so cookies and login are shared across tabs.

Chrome synchronisation is a guard, not disconnect/reconnect churn: each tab's
`urlChanged`, `loadStarted`, `loadProgress` and `loadFinished` handlers update
the URL bar, navigation buttons and progress bar only when
`self is window.current_tab`. On `QTabWidget.currentChanged` the window
refreshes the chrome from the newly active tab.

New-window requests use Qt's own adoption API rather than the current hijack at
`modules/webview.py:340-342`, which discards the opener relationship:

```python
def new_window_requested(request):
    background = request.destination() == DestinationType.InNewBackgroundTab
    tab = window.new_tab(background=background)
    request.openIn(tab.page)
```

Middle-clicking a link already arrives as `InNewBackgroundTab`, so background
tabs need no separate code path.

Shortcuts, three `QShortcut`s on the window (the codebase currently has none):

| Keys | Action |
|---|---|
| Ctrl+T | new tab at `about:blank`, focused |
| Ctrl+W | close current tab |
| Ctrl+Tab | next tab, wrapping |

Closing the last tab closes the window. Session restore is out of scope.

## Ad blocking

Hostname matching against HaGeZi's Pro DNS blocklist. A set lookup costs
microseconds per request and needs no new dependency.

### List choice

HaGeZi publishes the Pro list in several encodings. Two are candidates:

| File | Size | Entries | Declared syntax |
|---|---|---|---|
| `hosts/pro.txt` | 19.9 MB | 545,586 | Hosts (including possible subdomains) |
| `wildcard/pro-onlydomains.txt` | 4.2 MB | 220,780 | Domains (without subdomains) |

Use **`wildcard/pro-onlydomains.txt`**:

`https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/pro-onlydomains.txt`

The two cover the same ground — one pre-expands known subdomains, the other lists
base domains and expects the resolver to match subdomains. The domains-only file
is a 5× smaller download, holds ~26 MB in a Python set rather than ~70 MB, and
blocks subdomains HaGeZi never enumerated. The cost is three lines of matcher.

Format is a bare domain per line, with `#` comment lines in a header block, so
parsing is:

```python
hosts = {l for l in text.splitlines() if l and l[0] != "#"}
```

### Matching

Because the list holds base domains, matching walks up the labels:

```python
def blocked(host):
    while "." in host:
        if host in hosts:
            return True
        host = host.partition(".")[2]
    return False
```

The `"." in host` condition is the loop guard *and* the safety rail: it stops
before testing a bare TLD, so a malformed list entry like `com` can never black
out the whole web. Domain depth is 3-4 labels in practice, so this is a handful
of set lookups per request.

```python
class Blocker(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.resourceType() != ResourceTypeMainFrame \
                and blocked(info.requestUrl().host()):
            info.block(True)
```

Skipping `ResourceTypeMainFrame` is essential. Without it, navigating *to* a
blocked host fails with no explanation.

### Ownership

List ownership is split across the process boundary. The main process owns the
*file*; the child owns the *set*. A parsed set cannot cross a process boundary,
and serialising 220k strings over the pipe would cost more than the child
re-parsing 4 MB itself.

One idempotent function in `modules/webview.py`:

```python
async def ensure_blocklist():
    if not globals.settings.browser_adblock:
        return
    path = globals.data_path / "blocklist.txt"
    if path.is_file() and time.time() - path.stat().st_mtime < 7 * 86400:
        return
    try:
        path.write_bytes(await api.fetch("GET", BLOCKLIST_URL, cookies=False, timeout=120))
    except Exception:
        pass   # a nicety; never surface, never block
```

`cookies=False` is mandatory, not stylistic. `api.request` defaults to
`cookies=True`, which substitutes `globals.cookies` (`modules/api.py:254-255`),
and aiohttp sends explicitly-passed cookies regardless of host — so the default
would leak F95zone session cookies to `raw.githubusercontent.com`.

The explicit `timeout=120` overrides `globals.settings.request_timeout`, which is
tuned for small API calls and can be shorter than a 4 MB transfer needs.

It stats the file first, so the common case is a no-op. Two fire-and-forget call
sites, both `async_thread.run()`:

| Call site | Why |
|---|---|
| `main()`, inside the `db.setup(), api.setup()` block (`main.py:27`) | Preload at app startup, so the list is ready before the first browser window |
| The `browser_adblock` checkbox in `gui.py` | Takes effect the moment the setting is enabled, without a restart |

`draw_settings_checkbox` already returns `changed` (`gui.py:3936-3942`), so the
toggle hook is `if draw_settings_checkbox("browser_adblock"): async_thread.run(...)`
— one line, no change to the shared helper.

`webview.start()` deliberately does *not* call it. It would fire on every browser
open while never helping the window being opened, since the download cannot beat
the child's boot.

The child reads the cached file at boot; the path arrives through
`create_kwargs()`.

`main.py:74-79` dispatches `webview-daemon` before `main()` is ever reached, so
child processes never run the fetch, and `lock_singleton()` (`main.py:38-56`)
guarantees a single main process, so there is no double-download.

Timing: the fetch is never awaited and never delays a window. On a cold
first-ever launch, a browser window opened within a second or two of startup may
still block nothing; it takes effect from the next one. The list header declares
`Expires: 1 day`; the 7-day refresh is a deliberate trade against re-downloading
4 MB daily, and is a one-constant change.

The interceptor is installed on the shared browser's profile only, never on the
redirect resolvers, whose navigation must not be interfered with.

## Download handoff

`modules/webview.py:331-339` currently always opens a `QFileDialog`. When a
download manager is configured, hand off instead:

```python
def download_requested(download):
    if executable:
        url = download.url().url()
        args = [executable, *(a.replace("{url}", url) for a in shlex.split(arguments))]
        try:
            subprocess.Popen(args, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
            download.cancel()
            return
        except OSError:
            pass   # fall through to the save dialog
    save_dialog(download)
```

Three properties of this that are not negotiable:

**No shell.** `download.url()` is web-controlled. Running the handoff through a
shell (`shell=True`, or a single command string) turns a hostile download URL
into arbitrary command execution. The URL must arrive as its own `argv` element.

**Substitute after splitting.** `shlex.split` runs on the arguments template
*before* `{url}` is substituted, so no character in the URL can ever be parsed as
a quote or separator.

**Executable stored separately from arguments.** `shlex.split` with POSIX rules
eats backslashes, which destroys `C:\Program Files\...`. Keeping the executable
out of the split path avoids this entirely.

All three fall out of mirroring what the custom-browser setting already does —
`args = [set.browser_custom_executable, *shlex.split(set.browser_custom_arguments)]`
at `modules/callbacks.py:392`, exec'd via argv with no shell. Same trust
boundary, same mechanism, proven on all three platforms.

The launch is attempted *before* `download.cancel()`, so a mistyped executable
path cannot silently eat the download — the user falls back to the normal save
dialog.

This is a generic download-manager handoff, not IDM-specific, so the same code
path serves JDownloader, aria2 and others. IDM is Windows-only; F95Checker is
tri-platform.

## Settings

Three new settings, each following the established pattern — field in
`common/structs.py`, column default in `modules/db.py` (see `:200` and `:253`),
widget in the Browser section of `modules/gui.py` (~`:4292`) — then passed to the
child through `create_kwargs()` (`modules/webview.py:107-120`).

| Setting | Type | Default | Widget |
|---|---|---|---|
| `browser_adblock` | bool | `True` | `draw_settings_checkbox`, as `software_webview` at `gui.py:4297` |
| `download_manager_executable` | str | `""` | Text input + `filepicker.FilePicker` browse button |
| `download_manager_arguments` | str | `"/d {url} /n"` | Text input |

The two download-manager settings deliberately mirror
`browser_custom_executable` / `browser_custom_arguments`, and reuse that exact
widget pair — a "Configure" button opening a popup with an executable input, a
folder-open browse button, and an arguments input (`gui.py:4231-4272`). Copying
it means the filepicker, the right-click text context menu and the
`db.update_settings` wiring all come for free.

An empty `download_manager_executable` reproduces today's behaviour exactly; it
is the switch that enables the whole feature. The arguments default is IDM's
syntax, and the label documents `{url}` as the placeholder, so a Windows user
only has to browse to:

```
C:\Program Files (x86)\Internet Download Manager\IDMan.exe
```

## Error handling

| Failure | Behaviour |
|---|---|
| Blocklist fetch fails, or file missing/malformed | Empty set, nothing blocked, no popup, no delay. It is a nicety. |
| Download manager fails to launch (`OSError`) | Fall back to the save dialog; the download is not cancelled. |
| IPC message arrives while the window is closing | Child is exiting; the parent's next `open()` finds a dead pipe and respawns. |
| Child process dies unexpectedly | Same path. Nothing is cleaned up eagerly; the stale handle is detected and replaced on the next `open()`. |

## Testing

One `test_webview.py` covering the two pieces of real logic, both pure and
Qt-free:

- **list parsing** — bare domains are collected; the `#` header block and blank
  lines are ignored
- **`blocked()`** — a listed domain matches; any depth of subdomain of a listed
  domain matches (`a.b.ads.example.com` against `ads.example.com`); an unlisted
  domain does not; a domain that merely *ends with* a listed string but is not a
  subdomain does not (`notads.example.com` against `ads.example.com`); and a
  list containing `com` does not block `example.com`
- **block decision** — blocked host as subresource is blocked; blocked host as
  `MainFrame` is *allowed*; unlisted host is allowed

Three of these guard against silent, total feature failure and are not optional:
the MainFrame case, the suffix-not-subdomain case, and the bare-TLD case.

The Qt UI surfaces get a manual checklist rather than a Qt test harness:

1. Opening a second thread from the library adds a tab to the existing window
2. Middle-clicking a link opens a background tab without stealing focus
3. Ctrl+T, Ctrl+W, Ctrl+Tab behave as tabulated
4. Closing the last tab closes the window
5. A forum "copy link" button actually copies
6. An ad-heavy page loads without ads
7. A login-gated DDL link reaches IDM and downloads successfully

## Out of scope

- Loading real browser extensions, in any form
- Cosmetic filtering (element hiding) and EasyList filter syntax
- Session restore of open tabs across restarts
- Per-site blocker toggles or a blocked-request counter
- Pinned tabs, tab search, tab groups
- Any change to the main ImGui window beyond the two settings

## Open risk

A bare `IDMan.exe /d <url> /n` carries none of the Qt profile's cookies, so any
download URL requiring an authenticated session would fail. F95zone Donor DDL
and RPDL links resolve to tokenised direct URLs — that is what `css_redirect`
and `xpath_redirect` (`modules/webview.py:429-506`) exist for — so this is
expected to work.

This is unverified. The implementation plan must carry an explicit step to test
the handoff against a live login-gated link before the feature is considered
done. If cookies do prove necessary, IDM's CLI offers no clean way to pass them
and the design needs revisiting.
