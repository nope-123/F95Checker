import time

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
    if path.is_file() and time.time() - path.stat().st_mtime < 7 * 86400:
        return
    try:
        # cookies=False is mandatory: api.request defaults to attaching
        # globals.cookies, which would leak F95zone session cookies to GitHub.
        # The explicit timeout overrides request_timeout, tuned for small calls.
        data = await api.fetch("GET", BLOCKLIST_URL, cookies=False, timeout=120)
        if data and len(parse_blocklist(data.decode(errors="replace"))) >= MIN_BLOCKLIST_ENTRIES:
            path.write_bytes(data)
    except Exception:
        pass  # a nicety, never surface and never block
