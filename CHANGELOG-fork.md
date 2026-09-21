### Added:
- Tabs in the integrated browser: opening multiple pages reuses one window instead of spawning a new process each time
  - Tabs share one profile, so a login carries across them
  - Middle-click a tab to close it
- Find in page in the integrated browser: Ctrl+F, with a match counter and Enter / Shift+Enter to step through matches
- Warning before closing the integrated browser with more than one tab open
- Ad and tracker blocking in the integrated browser, in Settings > Browser > Block ads:
  - Uses HaGeZi's Pro DNS blocklist, downloaded in the background and refreshed weekly
  - Ad popups are dropped instead of opening a tab
- Option to hand off integrated browser downloads to an external download manager, in Settings > Browser > Download manager:
  - Browse to the executable and set its arguments, `{url}` is where the download link goes
  - For IDM on Windows keep the default arguments, adding `/n` makes it start downloading immediately instead of asking for a name and folder
  - IDM is handed the whole request over its own local websocket, the way its browser extension does, so the cookies, referer and user agent go with it: hosts that only serve a file to the browser that asked for it (gofile and friends) download properly instead of saving a "please log in" page. Falls back to the plain command line if IDM is not listening
  - Leave the executable empty to keep saving downloads normally
- F95zone attachments are downloaded instead of being rendered as a page

### Updated:
- Updates are checked against this fork's releases; running from a git checkout instead reports how many commits upstream has to incorporate
- An updated game keeps its half-checked "outdated finished" mark, and clicking it now clears the mark instead of re-finishing at the new version

### Fixed:
- Fix clipboard access not working in the integrated browser
- Fix the game list moving under you while a refresh brings in updates, and when it re-sorts
- Fix a same-site popup being closed as an ad, so Google Drive's "download anyway?" confirmation survives
- Fix the back and forward buttons not navigating in the integrated browser
- Fix pages and redirects stealing the tab you are reading, and keep masked f95zone links in the tab you clicked them in
- Fix the login and resolver windows staying on top of everything
- Fix a background tab laying out at the wrong size until it is shown
- Fix message boxes in the integrated browser not following the app theme
