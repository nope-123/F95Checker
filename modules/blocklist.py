BLOCKLIST_URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/pro-onlydomains.txt"


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
