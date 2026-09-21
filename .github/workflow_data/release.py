import requests
import json
import os
import subprocess


if __name__ == "__main__":
    with open("CHANGELOG-fork.md") as f:
        changelog = f.read()
    # Whatever upstream added to its own changelog since the previous release came in with a
    # merge in this range, so it ships here and never again. Needs fetch-depth 0 in build.yml
    git = lambda *args: subprocess.run(("git", *args), capture_output=True, text=True).stdout
    if previous := git("describe", "--tags", "--abbrev=0", "HEAD^").strip():
        diff = git("diff", f"{previous}..HEAD", "--", "CHANGELOG.md").splitlines()
        if added := [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]:
            changelog += "\n### From upstream:\n" + "\n".join(added) + "\n"
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        event = json.load(f)
    print(f"event = {json.dumps(event, indent=4)}")
    release = requests.get(
        event["release"]["url"],
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {os.environ['GITHUB_TOKEN']}"
        }
    ).json()
    print(f"release = {json.dumps(release, indent=4)}")
    body = "## ⬇️ Download\n"
    for asset_type, asset_icon in [("Windows", "🪟"), ("Linux", "🐧"), ("macOS-x64", "🍎"), ("macOS-arm64", "🍎"), ("Source", "🐍")]:
        print(f"Adding {asset_type}")
        for asset in release["assets"]:
            if asset_type.lower() in asset["name"].lower():
                asset_url = asset["browser_download_url"]
        body += f">### [{asset_type} {asset_icon}]({asset_url}) ([VirusTotal](https://www.virustotal.com/gui/file/))\n\n"
    body += (
        "## ❤️ Support\n" +
        "F95Checker is **Free and Open Source Software**, provided to you **free of cost**. However it is actively **developed by " +
        "one single person only, WillyJL**. Please consider [**donating**](https://linktr.ee/WillyJL) or **sharing this software**!\n\n" +
        "## 🚀 Changelog\n" +
        changelog
    )
    print(f"Full body:\n\n{body}")
    req = requests.patch(
        release["url"],
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {os.environ['GITHUB_TOKEN']}"
        },
        json={
            "body": body
        }
    )
    if not req.ok:
        print(f"{req.status_code = }\n{req.content = }")
