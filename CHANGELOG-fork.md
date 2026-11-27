### Updated:
- The integrated browser runs on Qt 6.12 and Chromium 140, up from Qt 6.7 and Chromium 118, which no longer get security fixes. Windows and Linux only, macOS keeps its own version. The Windows download grows by about 36 MB

### Fixed:
- Opening links into new tabs in the integrated browser no longer leaves tabs blank. A page shared its page process with the tabs it opened, and Qt crashed that process when handing them over, taking the page and all of those tabs down at once

## ⬆️ From upstream
### Fixed:
- Launching a game whose executable is a folder, such as a macOS `.app` bundle, now works instead of failing with an error (by WillyJL)
