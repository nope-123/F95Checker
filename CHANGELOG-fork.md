## ⬆️ From upstream
### Updated:
- Threads that can't be found on F95zone are now listed together in one popup at the end of a refresh, where Remove, Convert or Archive applies to all of them, instead of one popup per game (by @WillyJL)
- When the Cache API has an error for a thread, the last data it had cached is still saved, and the thread is checked again on every refresh until the error clears (by @WillyJL)
- A new refresh replaces the updates, API errors and missing threads popups left over from the previous one, so errors that have since cleared don't linger (by @WillyJL)

### Fixed:
- A Cache API error on one thread no longer stops the whole refresh with an error popup, the refresh finishes and all errors are shown together in one warning at the end (by @WillyJL)
