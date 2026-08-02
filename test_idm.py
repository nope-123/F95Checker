#!/usr/bin/env python
# Stdlib-only self-check, needs no installed dependencies.
# Run: python test_idm.py
from modules.idm import cookie_header, encode

# (domain, path, secure, name, value)
JAR = [
    (".gofile.io", "/", False, "accountToken", "abc123"),
    ("f95zone.to", "/", False, "xf_session", "secret"),
    ("store1.gofile.io", "/download", False, "scoped", "yes"),
    (".gofile.io", "/", True, "onlyhttps", "tls"),
]


def test_encode():
    # numbers inline, strings length prefixed with their utf-8 byte count
    assert encode(1, 14, 1, 0, (1,), {6: "http://x/f", 8: 4}) == \
        "MSG#1#14#1#0:1,6=10:http://x/f,8=4;", encode(1, 14, 1, 0, (1,), {6: "http://x/f", 8: 4})
    # an empty arg is sent as 0, and empty or missing attributes are left out entirely
    assert encode(2, 2, 5, 0, (113, None), {51: "", 54: None, 6: "a"}) == "MSG#2#2#5#0:113:0,6=1:a;"
    # length is bytes, not characters, or IDM reads the next field as part of the string
    assert encode(1, 14, 1, 0, (), {6: "hé"}) == "MSG#1#14#1#0,6=3:hé;"


def test_cookie_header():
    # the download host is a subdomain of the cookie domain: the session goes with it
    assert cookie_header(JAR, "store1.gofile.io", "/download/web/x", True) == \
        "accountToken=abc123; scoped=yes; onlyhttps=tls"
    # over plain http the secure cookie stays behind
    assert cookie_header(JAR, "store1.gofile.io", "/download/web/x", False) == \
        "accountToken=abc123; scoped=yes"
    # path scoped cookie does not leak to another path
    assert cookie_header(JAR, "store1.gofile.io", "/other", True) == "accountToken=abc123; onlyhttps=tls"
    # and nothing at all goes to an unrelated host -- this is the whole point of
    # matching: the file host must never be handed the forum session
    assert cookie_header(JAR, "cdn.example.com", "/file.zip", True) == ""
    # a suffix that is not a label boundary is a different host
    assert cookie_header(JAR, "notf95zone.to", "/", True) == ""
    assert cookie_header(JAR, "f95zone.to", "/", True) == "xf_session=secret"


if __name__ == "__main__":
    test_encode()
    test_cookie_header()
    print("ok")
