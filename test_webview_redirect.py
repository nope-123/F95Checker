#!/usr/bin/env python
# Needs PyQt6 (modules.webview imports it), touches no network and opens no window.
# Run: python test_webview_redirect.py
from modules import webview


def calls(fn, *args, **kwargs):
    """Run one of the redirect entry points with _click_redirect stubbed out, and
    hand back the javascript it would have clicked with."""
    seen = []
    real, webview._click_redirect = webview._click_redirect, lambda url, js=None, **kw: seen.append((url, js, kw))
    try:
        fn(*args, **kwargs)
    finally:
        webview._click_redirect = real
    return seen[0]


def test_css_redirect():
    url, js, kwargs = calls(webview.css_redirect, "https://f95zone.to/x", "a.host_link", minimal=False)
    assert url == "https://f95zone.to/x", url
    assert js == "document.querySelector('a.host_link')?.click();", js
    # kwargs ride through untouched, or minimal=copy stops reaching the window
    assert kwargs == {"minimal": False}, kwargs
    # no selector is a plain wait-for-the-redirect window, with nothing to click
    assert calls(webview.css_redirect, "https://f95zone.to/x")[1] is None


def test_xpath_redirect():
    # no trailing index means the first match
    _, js, _ = calls(webview.xpath_redirect, "https://f95zone.to/x", "//a[@href]")
    assert "document.evaluate('//a[@href]', document" in js, js
    assert ".snapshotItem(0)?.click();" in js, js
    # a trailing [n] is xpath's 1-based index, and it must come off the expression:
    # evaluate() returns the whole node set and the index picks out of the snapshot
    _, js, _ = calls(webview.xpath_redirect, "https://f95zone.to/x", "//a[@href][3]")
    assert "document.evaluate('//a[@href]', document" in js, js
    assert ".snapshotItem(2)?.click();" in js, js
    # only a trailing index, never a predicate in the middle of the expression
    _, js, _ = calls(webview.xpath_redirect, "https://f95zone.to/x", "//a[2]/b[@x]")
    assert "document.evaluate('//a[2]/b[@x]', document" in js, js
    assert ".snapshotItem(0)?.click();" in js, js
    assert calls(webview.xpath_redirect, "https://f95zone.to/x")[1] is None


if __name__ == "__main__":
    test_css_redirect()
    test_xpath_redirect()
    print("ok")
