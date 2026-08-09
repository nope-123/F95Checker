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


if __name__ == "__main__":
    tests = {
        "hidden": test_bar_starts_hidden,
        "shortcut": test_ctrl_f_shortcut_is_registered,
        "position": test_bar_sits_over_the_top_right_of_the_page,
        "tabbar": test_bar_clears_the_tab_bar_when_a_second_tab_opens,
        "dismiss": test_dismiss_hides_the_bar,
        "count": test_typing_reports_every_match,
        "nomatch": test_a_query_that_matches_nothing_says_so,
        "empty": test_emptying_the_box_clears_the_counter,
        "escape": test_escape_closes_but_keeps_the_query,
    }
    # One QApplication per process, so each case runs as its own subprocess
    if len(sys.argv) > 1:
        tests[sys.argv[1]]()
    else:
        import subprocess
        for case in tests:
            subprocess.run([sys.executable, __file__, case], check=True)
        print("ok")
