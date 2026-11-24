### Fixed:
- Opening links into new tabs in the integrated browser no longer leaves tabs blank. A page shared its page process with the tabs it opened, and Qt crashed that process when handing them over, taking the page and all of those tabs down at once

## ⬆️ From upstream
### Fixed:
- Launching a game whose executable is a folder, such as a macOS `.app` bundle, now works instead of failing with an error (by WillyJL)
