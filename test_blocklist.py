#!/usr/bin/env python
# Stdlib-only self-check, needs no installed dependencies.
# Run: python test_blocklist.py
from modules.blocklist import blocked, parse_blocklist

LIST = """\
# Title: HaGeZi's Pro DNS Blocklist
# Number of entries: 3
#
ads.example.com
tracker.net
boredcrown.com
"""


def test_parse_blocklist():
    hosts = parse_blocklist(LIST)
    assert hosts == {"ads.example.com", "tracker.net", "boredcrown.com"}, hosts
    assert not any(h.startswith("#") for h in hosts), "comment lines leaked in"
    assert "" not in hosts, "blank line leaked in"


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
    # degenerate hosts
    assert not blocked("localhost", hosts)
    assert not blocked("", hosts)
    # a malformed bare-TLD entry must never black out the web
    assert not blocked("example.com", {"com"})


if __name__ == "__main__":
    test_parse_blocklist()
    test_blocked()
    print("ok")
