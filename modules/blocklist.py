BLOCKLIST_URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/pro-onlydomains.txt"

# Sanity floor for a downloaded blocklist: the real list carries ~220,000
# entries, while a GitHub 404 body is a single line and an HTML error page is
# a few dozen. 1000 separates garbage responses decisively, with a 200x
# margin below the real list.
MIN_BLOCKLIST_ENTRIES = 1000


def parse_blocklist(text: str):
    return {line.lower() for line in text.splitlines() if line and line[0] != "#"}


def blocked(host: str, hosts: set[str]):
    # The list holds base domains, so walk up the labels. The "." condition is
    # also the safety rail: a bare TLD entry can never match anything.
    host = host.lower()
    while "." in host:
        if host in hosts:
            return True
        host = host.partition(".")[2]
    return False


# Generic second level labels: co.uk and com.au are a registry, not a site, so two
# names sharing only those last two labels share nothing at all
GENERIC_2LD = {"co", "com", "net", "org", "edu", "gov", "ac"}


def same_site(a: str, b: str):
    # Suffix match rather than a public suffix list: www/cdn/attachments subdomains
    # have to count as the same site, and a real PSL is a second 200KB list to ship
    # and refresh for a question only asked about the page you are already on.
    # Sibling subdomains count too, and neither is a suffix of the other: Google Drive
    # sends you from drive.google.com to drive.usercontent.google.com to get the file.
    # So fall back to the registrable domain, guessed as the last two labels.
    # ponytail: worst case an ad hosted under the same suffix as the page (both on a
    # dyndns domain like us.to) reads as same-site; add a PSL if that shows up
    a, b = a.lower(), b.lower()
    if a == b or a.endswith("." + b) or b.endswith("." + a):
        return True
    a, b = a.split("."), b.split(".")
    return a[-2:] == b[-2:] and a[-2] not in GENERIC_2LD


def blocklist_path():
    from modules import globals
    return globals.data_path / "blocklist.txt"


async def ensure_blocklist():
    from modules import (
        api,
        globals,
    )
    if not globals.settings.browser_adblock:
        return
    path = blocklist_path()
    etag_path = path.with_suffix(".etag")
    try:
        # GitHub's ETag is a hash of the list, so while it still matches the server
        # answers 304 with no body and nothing is downloaded. Only sent while the list
        # is on disk: a deleted list has to come back even if it never changed.
        # Kept as GitHub sent it, never hashed locally: it is not the file's SHA-256,
        # and the gzipped response carries a different one
        headers = {}
        if path.is_file() and etag_path.is_file():
            headers["If-None-Match"] = etag_path.read_text()
        # cookies=False is mandatory: api.request defaults to attaching
        # globals.cookies, which would leak F95zone session cookies to GitHub.
        # The explicit timeout overrides request_timeout, tuned for small calls.
        async with api.request("GET", BLOCKLIST_URL, cookies=False, timeout=120, headers=headers) as (data, res):
            if res.status == 200 and len(parse_blocklist(data.decode(errors="replace"))) >= MIN_BLOCKLIST_ENTRIES:
                # List first: dying in between leaves the old ETag, so it just downloads again
                path.write_bytes(data)
                etag_path.write_text(res.headers.get("ETag", ""))
    except Exception:
        pass  # a nicety, never surface and never block
