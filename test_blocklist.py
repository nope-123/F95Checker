#!/usr/bin/env python
# Stdlib-only self-check, needs no installed dependencies.
# Run: python test_blocklist.py
from modules.blocklist import blocked, parse_blocklist

LIST = """\
# Title: HaGeZi's Pro DNS Blocklist
# Number of entries: 4
#
ads.example.com
tracker.net
boredcrown.com
MixedCase.Test
"""


def test_parse_blocklist():
    hosts = parse_blocklist(LIST)
    assert hosts == {"ads.example.com", "tracker.net", "boredcrown.com", "mixedcase.test"}, hosts
    assert not any(h.startswith("#") for h in hosts), "comment lines leaked in"
    assert "" not in hosts, "blank line leaked in"
    # mixed-case entries are normalized to lowercase
    assert "mixedcase.test" in hosts, "mixed-case entry should be lowercased"


def test_blocked():
    hosts = parse_blocklist(LIST)
    # listed domains and any depth of subdomain
    assert blocked("ads.example.com", hosts)
    assert blocked("a.b.ads.example.com", hosts)
    assert blocked("cdn.tracker.net", hosts)
    # not listed
    assert not blocked("f95zone.to", hosts)
    # parent of a listed domain must not be blocked
    assert not blocked("example.com", hosts)
    # a suffix match is not a subdomain match
    assert not blocked("notads.example.com", hosts)
    # a listed name appearing as a leading label is not a match
    assert not blocked("ads.example.com.evil.tld", hosts)
    # case insensitivity: uppercase host against lowercase entry
    assert blocked("ADS.EXAMPLE.COM", hosts)
    # case insensitivity: mixed-case subdomain
    assert blocked("CDN.Tracker.Net", hosts)
    # case insensitivity: mixed-case entry matched by lowercase query
    assert blocked("mixedcase.test", hosts)
    assert blocked("MIXEDCASE.TEST", hosts)
    # degenerate hosts
    assert not blocked("localhost", hosts)
    assert not blocked("", hosts)
    # a malformed bare-TLD entry must never black out the web
    assert not blocked("example.com", {"com"})


if __name__ == "__main__":
    test_parse_blocklist()
    test_blocked()
    print("ok")
