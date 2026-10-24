## ⬆️ From upstream
### Fixed:
- A slow or failing Cache API no longer ends a refresh with a "Fast check error": F95Checker waits up to 10 minutes for it instead of 2, and any Cloudflare error page it returns (such as a timeout) now shows the "Server downtime" warning, which before only a 502 or 521 did (by @WillyJL)
- With F95Checker Beta updates on, a full release no longer offers to "update" to a beta build older than itself (by @WillyJL)
