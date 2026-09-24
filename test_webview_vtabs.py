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
