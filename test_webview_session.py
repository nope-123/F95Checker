#!/usr/bin/env python
# Run: python test_webview_session.py
# Needs PyQt6 + QtWebEngine (like test_webview_vtabs.py), runs offscreen and touches
# no network: pages are about:blank fragments. Every case points QSettings at a temp
# folder and QStandardPaths at its test location first, so no run reads or writes the
# real browser.ini, nor the real browser profile a non-private window opens.
import json
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"

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
from PyQt6.QtTest import QTest  # noqa: E402

# Quitting closes every visible window, which reaches the "close them all?" guard
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
)


def until(check, ms=10000):
    deadline = QtCore.QDeadlineTimer(ms)
    while not check() and not deadline.hasExpired():
        QTest.qWait(20)
    return check()


def settings():
    return QtCore.QSettings(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope,
        "f95checker", "browser",
    )


def saved():
    return [url.split("#")[-1] for url in json.loads(settings().value("session", "[]"))]


def window_(private=False, tabs=True):
    window = BrowserWindow(
        buttons=tabs, tabs=tabs, private=private, icon=QtGui.QIcon(),
        background_color=QtGui.QColor("#000000"), extension="", rpcproxy=None,
        proxy_auth=None, title="test",
    )
    window.resize(900, 600)
    window.show()
    return window


def app_():
    app = QtWidgets.QApplication(sys.argv)
    QtCore.QStandardPaths.setTestModeEnabled(True)
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, tempfile.mkdtemp(),
    )
    return app


def names(window):
    return [tab.view.url().toString().split("#")[-1] for tab in window.tab_list]


def opened(window, *names):
    """One loaded tab per name. Loaded, because only a tab that got a page of its own
    is one worth reopening"""
    tabs = [window.new_tab(f"about:blank#{name}", background=bool(window.tab_list)) for name in names]
    assert until(lambda: all(tab.view.history.count() for tab in tabs)), "the pages never loaded"
    return tabs


def test_the_open_tabs_are_saved_as_they_change():
    """Saved as they change, not on close: quitting the main app kills the browser
    process outright, and it never gets to close"""
    app = app_()
    window = window_()
    opened(window, "a", "b", "c")
    assert saved() == ["a", "b", "c"], saved()
    window.tabs.tabBar().moveTab(0, 2)
    assert saved() == ["b", "c", "a"], saved()
    window.close_tab(0)
    assert saved() == ["c", "a"], saved()
    window.tab_list[0].load("about:blank#moved")
    assert until(lambda: saved() == ["moved", "a"]), saved()
    # On disk, not just in QSettings' cache, with the window still open: that is all a
    # killed process leaves behind
    path = settings().fileName()
    assert until(lambda: os.path.exists(path) and "moved" in open(path).read()), "never reached the disk"


def test_private_and_one_page_windows_save_nothing():
    app = app_()
    for window in (window_(private=True), window_(tabs=False)):
        opened(window, "a")
        window.restore_session()
    assert not settings().contains("session"), f"saved {saved()}"


def test_the_last_tabs_come_back_before_the_new_link():
    app = app_()
    first = window_()
    opened(first, "a", "b")
    first.close()
    second = window_()
    second.restore_session()  # what create() does before the clicked link's tab
    second.new_tab("about:blank#clicked")
    # Once the window is up, so a first turn of the event loop
    assert until(lambda: names(second) == ["a", "b", "clicked"]), names(second)
    assert second.current_tab is second.tab_list[-1], "the link you clicked is not the tab shown"


def test_with_restore_off_they_wait_for_ctrl_shift_t():
    app = app_()
    first = window_()
    opened(first, "a", "b")
    first.close()
    settings().setValue("restore_tabs", False)
    second = window_()
    second.restore_session()
    second.new_tab("about:blank#clicked")
    assert names(second) == ["clicked"], f"restored with the setting off: {names(second)}"
    second.reopen_closed()
    assert names(second) == ["clicked", "a", "b"], names(second)


def test_ctrl_shift_t_reopens_closed_tabs_where_they_were():
    app = app_()
    window = window_(private=True)  # in memory, so a private window gets it too
    opened(window, "a", "b", "c")
    window.close_tab(1)
    window.close_tab(1)
    window.reopen_closed()
    assert names(window) == ["a", "c"], names(window)
    window.reopen_closed()
    assert names(window) == ["a", "b", "c"], names(window)
    assert window.current_tab is window.tab_list[1], "the reopened tab is not the one shown"
    keys = [s.key().toString() for s in window.findChildren(QtGui.QShortcut)]
    assert "Ctrl+Shift+T" in keys, f"no reopen shortcut, only {keys}"


def test_tabs_the_browser_closed_itself_are_not_offered():
    """An ad popup that never got a page, a tab that only started a download, and an
    off-site redirect that rendered a page: none of those were yours to reopen"""
    app = app_()
    window = window_(private=True)
    opened(window, "a")
    window.new_tab(background=True)  # never loaded anything
    probe, = opened(window, "probe")
    probe.probe = True
    window.close_tab(2)
    window.close_tab(1)
    window.reopen_closed()
    assert names(window) == ["a"], f"reopened {names(window)}"


def test_right_click_menu_offers_both():
    app = app_()
    menus = []
    QtWidgets.QMenu.exec = lambda self, *_: menus.append(self)
    window = window_()
    opened(window, "a", "b")
    bar = window.tabs.tabBar()

    def menu():
        bar.customContextMenuRequested.emit(bar.tabRect(0).center())
        return {a.text(): a for a in menus[-1].actions()}

    items = menu()
    reopen, startup = items["Reopen closed tab\tCtrl+Shift+T"], items["Reopen tabs on startup"]
    assert not reopen.isEnabled(), "offered to reopen with nothing closed"
    assert startup.isCheckable() and startup.isChecked(), "reopening on startup is not on by default"
    startup.trigger()
    assert settings().value("restore_tabs", type=bool) is False, "turning it off was not saved"
    window.close_tab(1)
    menu()["Reopen closed tab\tCtrl+Shift+T"].trigger()
    assert names(window) == ["a", "b"], names(window)
    private = window_(private=True)
    opened(private, "a")
    bar = private.tabs.tabBar()
    assert not menu()["Reopen tabs on startup"].isEnabled(), "a private window offers what it never saves"


if __name__ == "__main__":
    tests = {
        "saved": test_the_open_tabs_are_saved_as_they_change,
        "private": test_private_and_one_page_windows_save_nothing,
        "restore": test_the_last_tabs_come_back_before_the_new_link,
        "off": test_with_restore_off_they_wait_for_ctrl_shift_t,
        "reopen": test_ctrl_shift_t_reopens_closed_tabs_where_they_were,
        "notyours": test_tabs_the_browser_closed_itself_are_not_offered,
        "menu": test_right_click_menu_offers_both,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
