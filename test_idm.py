#!/usr/bin/env python
# Stdlib-only self-check, needs no installed dependencies.
# Run: python test_idm.py
from modules.idm import encode


def test_encode():
    # numbers inline, strings length prefixed with their utf-8 byte count
    assert encode(1, 14, 1, 0, (1,), {6: "http://x/f", 8: 4}) == \
        "MSG#1#14#1#0:1,6=10:http://x/f,8=4;", encode(1, 14, 1, 0, (1,), {6: "http://x/f", 8: 4})
    # an empty arg is sent as 0, and empty or missing attributes are left out entirely
    assert encode(2, 2, 5, 0, (113, None), {51: "", 54: None, 6: "a"}) == "MSG#2#2#5#0:113:0,6=1:a;"
    # length is bytes, not characters, or IDM reads the next field as part of the string
    assert encode(1, 14, 1, 0, (), {6: "hé"}) == "MSG#1#14#1#0,6=3:hé;"


if __name__ == "__main__":
    test_encode()
    print("ok")
