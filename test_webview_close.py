#!/usr/bin/env python
# Run: python test_webview_close.py
# Needs PyQt6 + QtWebEngine (like test_webview_find.py, unlike test_blocklist.py),
# runs offscreen and touches no network: the guard reads tab_list and never looks at
# page content, so no case here loads a page at all.
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from webview_testkit import (
    isolate_settings,
)
from modules.webview_window import (
    BrowserWindow,
    config_qt_flags,
)

config_qt_flags(debug=False, software=True)

from PyQt6 import (  # noqa: E402
    QtCore,
    QtGui,
    QtWidgets,
)


def browser(tabs=True):
    app = QtWidgets.QApplication(sys.argv)
    isolate_settings()
    window = BrowserWindow(
        buttons=True, tabs=tabs, private=True, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.show()
    return app, window


def stub_question(answer):
    """A real message box spins its own event loop and would hang the run, so every
    case answers for the user. Replaced and never restored: one QApplication per
    process means each case is its own process, and it exits right after."""
    asked = []
    def question(parent, title, text, buttons, default):
        asked.append((title, text, buttons, default))
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
    button = QtWidgets.QMessageBox.StandardButton
    assert asked == [(
        "Close browser",
        "This window has 2 tabs open. Close them all?",
        button.Yes | button.No,
        button.No,  # a stray Enter must not discard the tabs
    )], f"the prompt was not the one the spec asks for: {asked}"


def test_two_tabs_and_yes_closes():
    app, window = browser()
    window.new_tab()
    window.new_tab(background=True)
    asked = stub_question(QtWidgets.QMessageBox.StandardButton.Yes)
    assert window.close() is True, "Yes did not close the window"
    assert not window.isVisible(), "the window stayed up"
    assert window.tab_list == [], f"teardown left {len(window.tab_list)} tabs"
    assert len(asked) == 1, f"expected one prompt, got {asked}"


def test_closing_tabs_one_by_one_never_asks():
    """close_tab is the third close path, and the only one that reaches close()
    without the user aiming at the window. It cannot prompt because it only gets there
    on its <= 1 branch -- but that is a property of the count, so a refactor that
    popped the tab before closing would flip it and nothing else here would notice."""
    app, window = browser()
    window.new_tab()
    window.new_tab(background=True)
    asked = stub_question(QtWidgets.QMessageBox.StandardButton.No)
    window.close_tab(0)
    assert len(window.tab_list) == 1, "the first close_tab did not drop a tab"
    window.close_tab(0)
    assert not window.isVisible(), "the last close_tab left the window up"
    assert asked == [], f"closing tabs one by one asked: {asked}"


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


def test_links_open_beside_their_page():
    """A link lands right of the page it came from rather than at the end of the
    strip, a run of them off one page keeps click order, and a tab with no page
    behind it (Ctrl+T, a link sent from the main app) still appends."""
    app, window = browser()
    first = window.new_tab()
    second = window.new_tab(background=True)
    a = window.new_tab(background=True, opener=first)
    b = window.new_tab(background=True, opener=first)
    blank = window.new_tab()
    order = [first, a, b, second, blank]
    assert window.tab_list == order, "tabs landed out of place"
    # tab_list indices are tab bar indices, so the bar has to agree
    views = [window.tabs.widget(i) for i in range(window.tabs.count())]
    assert all(v is t.view for v, t in zip(views, order)), "the tab bar disagrees with tab_list"


def test_long_titles_squeeze_instead_of_scrolling():
    """QTabBar never scrolls during a drag, so a tab scrolled off the left edge is one
    you cannot drop another in front of. Four long titles used to do exactly that."""
    app, window = browser()
    window.resize(900, 600)
    for i in range(4):
        window.new_tab()
        window.tabs.setTabText(i, "x" * 30)  # tab_title_changed's cap
    app.processEvents()
    left = window.tabs.tabBar().tabRect(0).left()
    assert left >= 0, f"the first tab scrolled off the bar (x={left}), out of drag reach"


def test_a_link_dropped_on_the_tabs_opens_where_it_lands():
    """Like a browser's tab strip: a link dropped on the left half of the first tab
    opens as a new tab in front of it."""
    app, window = browser()
    first = window.new_tab()
    bar = window.tabs.tabBar()
    app.processEvents()
    # A browser's strip is there with one tab too, or a lone tab has nowhere to drop
    assert bar.isVisible(), "the tab bar is hidden with one tab open"
    second = window.new_tab(background=True)
    app.processEvents()
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl("about:blank#dropped")])
    pos = QtCore.QPoint(bar.tabRect(0).left() + 2, bar.tabRect(0).center().y())
    args = (QtCore.Qt.DropAction.CopyAction, mime,
            QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier)
    enter = QtGui.QDragEnterEvent(pos, *args)
    QtWidgets.QApplication.sendEvent(bar, enter)
    # Without both, the drop never arrives
    assert bar.acceptDrops() and enter.isAccepted(), "the tab bar turned the link away"
    QtWidgets.QApplication.sendEvent(bar, QtGui.QDropEvent(QtCore.QPointF(pos), *args))
    assert len(window.tab_list) == 3, "the drop opened nothing"
    dropped = window.tab_list[0]
    assert window.tab_list[1:] == [first, second], "the drop did not land in front"
    assert dropped.view.url() == QtCore.QUrl("about:blank#dropped"), dropped.view.url()
    assert window.current_tab is dropped, "the dropped link did not get focus"


def test_f5_and_ctrl_r_reload():
    """A browser's reload keys. Qt's web view comes with none of a browser's own
    shortcuts, so every one of them is the window's to add"""
    app, window = browser()
    tab = window.new_tab()
    reloads = []
    tab.reload = lambda: reloads.append(tab)  # what the reload button calls
    keys = {shortcut.key().toString(): shortcut for shortcut in window.findChildren(QtGui.QShortcut)}
    for key in ("F5", "Ctrl+R"):
        assert key in keys, f"no {key} shortcut, only {sorted(keys)}"
        keys[key].activated.emit()
    assert reloads == [tab, tab], f"the keys reloaded {len(reloads)} times, not twice"


if __name__ == "__main__":
    tests = {
        "reload": test_f5_and_ctrl_r_reload,
        "beside": test_links_open_beside_their_page,
        "drop": test_a_link_dropped_on_the_tabs_opens_where_it_lands,
        "squeeze": test_long_titles_squeeze_instead_of_scrolling,
        "single": test_one_tab_closes_without_asking,
        "veto": test_two_tabs_and_no_keeps_the_window,
        "confirm": test_two_tabs_and_yes_closes,
        "onebyone": test_closing_tabs_one_by_one_never_asks,
        "chromeless": test_a_window_without_tabs_never_asks,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
