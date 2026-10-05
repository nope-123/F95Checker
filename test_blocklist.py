#!/usr/bin/env python
# Stdlib-only self-check, needs no installed dependencies.
# Run: python test_blocklist.py
from modules.blocklist import blocked, parse_blocklist, same_site

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


def test_same_site():
    # the page itself moving around: same host, or its own subdomains either way
    assert same_site("f95zone.to", "f95zone.to")
    assert same_site("attachments.f95zone.to", "f95zone.to")
    assert same_site("f95zone.to", "www.f95zone.to")
    assert same_site("F95Zone.to", "WWW.f95zone.TO")
    # a click that leaves the site, which is what an ad redirect looks like
    assert not same_site("rovno.xyz", "vikingf1le.us.to")
    assert not same_site("mega.nz", "mega.io")
    # a suffix that is not a label boundary is a different site
    assert not same_site("notf95zone.to", "f95zone.to")
    # sibling subdomains of one registrable domain, neither a suffix of the other:
    # this is the hop Google Drive makes to hand over a file
    assert same_site("drive.usercontent.google.com", "drive.google.com")
    assert same_site("doc-0s-bs-docs.googleusercontent.com", "googleusercontent.com")
    # but a registry suffix is not a site: two unrelated .co.uk share only the registry
    assert not same_site("shop.example.co.uk", "ads.evil.co.uk")
    assert not same_site("a.example.com", "b.other.com")


def test_ensure_blocklist():
    import asyncio
    import contextlib
    import pathlib
    import tempfile
    import types
    import modules
    from modules.blocklist import ensure_blocklist

    # A fake GitHub answering If-None-Match the way raw.githubusercontent.com does
    server = {"status": 200, "etag": '"v1"', "body": (LIST + "\n".join(f"d{i}.test" for i in range(1000))).encode()}
    sent = []

    @contextlib.asynccontextmanager
    async def request(method, url, cookies=True, headers={}, **kwargs):
        assert cookies is False, "F95zone cookies must never go to GitHub"
        sent.append(headers.get("If-None-Match"))
        if sent[-1] == server["etag"]:
            yield b"", types.SimpleNamespace(status=304, headers={})
        else:
            yield server["body"], types.SimpleNamespace(status=server["status"], headers={"ETag": server["etag"]})

    with tempfile.TemporaryDirectory() as tmp:
        # ensure_blocklist imports these lazily, so stubs keep this stdlib-only
        modules.api = types.SimpleNamespace(request=request)
        modules.globals = types.SimpleNamespace(data_path=pathlib.Path(tmp), settings=types.SimpleNamespace(browser_adblock=True))
        path = pathlib.Path(tmp) / "blocklist.txt"
        check = lambda: asyncio.run(ensure_blocklist())

        check()
        assert sent[-1] is None and path.read_bytes() == server["body"], "first run must download"
        v1 = server["body"]
        check()
        assert sent[-1] == '"v1"' and path.read_bytes() == v1, "unchanged list must be a conditional request"
        server.update(etag='"v2"', body=v1 + b"\nnew.test")
        check()
        assert sent[-1] == '"v1"' and path.read_bytes() == server["body"], "changed list must be downloaded"
        path.unlink()
        check()
        assert sent[-1] is None and path.is_file(), "a deleted list must come back even with a matching ETag"
        v2 = server["body"]
        server.update(status=404, etag='"v3"', body=b"404: Not Found")
        check()
        assert path.read_bytes() == v2 and (path.with_suffix(".etag")).read_text() == '"v2"', "an error page must not replace the list"


if __name__ == "__main__":
    test_parse_blocklist()
    test_blocked()
    test_same_site()
    test_ensure_blocklist()
    print("ok")
