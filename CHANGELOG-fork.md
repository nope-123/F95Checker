## ⬆️ From upstream
### Added:
- When F95zone answers with a Cloudflare captcha, a popup now says so and offers to solve it in the integrated browser, instead of reporting it as server downtime (by @WillyJL)

### Updated:
- Requests to the F95zone forum now send the integrated browser's user agent instead of F95Checker's own (by @WillyJL)
- Latest updates and direct download lookups no longer wait in the forum request queue (by @WillyJL)

### Fixed:
- Cloudflare's "Just a moment..." check can be passed in the integrated browser, the F95Checker page script no longer runs while it loads and breaks it (by @WillyJL)
