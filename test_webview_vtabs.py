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

LEFT = QtCore.Qt.MouseButton.LeftButton
MIDDLE = QtCore.Qt.MouseButton.MiddleButton
NONE = QtCore.Qt.MouseButton.NoButton


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


def visible(window):
    items = window.sidebar.list
    return [i for i in range(items.count()) if not items.item(i).isHidden()]


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


def test_right_click_menu_holds_the_toggle():
    app, window = browser()
    opened(window, "a", "b")
    # A real menu spins its own event loop and would hang the run, so exec just
    # records the menu. Never restored: each case is its own process
    menus = []
    QtWidgets.QMenu.exec = lambda self, *_: menus.append(self)
    bar = window.tabs.tabBar()
    bar.customContextMenuRequested.emit(bar.tabRect(0).center())
    assert menus[-1].actions()[0].text() == "Turn on vertical tabs", menus[-1].actions()[0].text()
    menus[-1].actions()[0].trigger()
    assert window.vertical, "the top strip's menu did not turn vertical tabs on"
    view = window.sidebar.list.viewport()
    pos = row_center(window, 0)
    QtWidgets.QApplication.sendEvent(view, QtGui.QContextMenuEvent(
        QtGui.QContextMenuEvent.Reason.Mouse, pos, view.mapToGlobal(pos),
    ))
    assert menus[-1].actions()[0].text() == "Turn off vertical tabs", menus[-1].actions()[0].text()
    menus[-1].actions()[0].trigger()
    assert not window.vertical, "the sidebar's menu did not turn vertical tabs off"


def test_long_titles_fit_the_sidebar():
    """Every row is as wide as the list, never as wide as its title. A row wider than
    the list scrolls it sideways, and puts the hover x past the edge on every row"""
    app, window = browser()
    window.toggle_vertical()
    short, long = opened(window, "short", "long")
    long.load("data:text/html,<title>" + "A long thread title " * 10 + "</title>")
    items = window.sidebar.list
    assert until(lambda: items.item(1).text().startswith("A long")), "the page never loaded"
    view = items.viewport()
    assert items.visualItemRect(items.item(0)).width() <= view.width(), (
        f"a row is {items.visualItemRect(items.item(0)).width()} wide in a {view.width()} list"
    )
    assert not items.horizontalScrollBar().isVisible(), "the list scrolls sideways"
    mouse(view, QtCore.QEvent.Type.MouseMove, row_center(window, 0), NONE, NONE)
    closer = window.sidebar.closer.geometry()
    assert view.rect().contains(closer), f"the close button sits at {closer}, outside {view.rect()}"


def test_closing_a_row_keeps_the_list_where_it_is():
    """How a long list gets cleaned up: close old tabs near the top while the current
    one sits far below. Each close renumbers the current row, and that is no reason to
    scroll the list down to it"""
    app, window = browser()
    window.resize(900, 300)  # rows are short offscreen: forty only overflow a short window
    window.toggle_vertical()
    opened(window, *map(str, range(40)))
    window.tabs.setCurrentIndex(39)
    scroll = window.sidebar.list.verticalScrollBar()
    assert scroll.maximum() > 0, "not enough tabs to scroll, so this proves nothing"
    scroll.setValue(0)
    window.close_tab(3)
    assert scroll.value() == 0, f"the list jumped to {scroll.value()}"
    window.tabs.setCurrentIndex(0)
    window.tabs.setCurrentIndex(38)
    assert scroll.value() == scroll.maximum(), "switching tabs no longer brings the row into view"


if __name__ == "__main__":
    tests = {
        "toggle": test_toggle_swaps_the_strips_and_is_remembered,
        "menu": test_right_click_menu_holds_the_toggle,
        "mirror": test_the_list_mirrors_tab_list,
        "width": test_sidebar_width_is_remembered,
        "oneview": test_a_window_without_tabs_gets_neither_and_keeps_the_choice,
        "findbar": test_the_find_bar_follows_the_page_when_toggled,
        "scroll": test_a_long_list_keeps_its_place_through_updates,
        "search": test_search_filters_by_title_and_url,
        "enter": test_enter_switches_to_the_first_match,
        "drag": test_dragging_a_row_reorders_the_tabs,
        "filtered": test_dragging_is_off_while_filtered,
        "middle": test_middle_click_closes_without_switching,
        "hover": test_the_hover_close_button_closes_that_row,
        "drop": test_a_link_dropped_on_the_list_opens_at_that_row,
        "filtereddrop": test_a_link_dropped_while_filtered_lands_by_tab_order,
        "fit": test_long_titles_fit_the_sidebar,
        "closekeeps": test_closing_a_row_keeps_the_list_where_it_is,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
