## ⬆️ From upstream
### Updated:
- All web requests now accept response headers up to 256 KB. 11.4.16 had raised the limit to 64 KB to fix the "Got more than 8190 bytes" updater error; this switches to upstream's own fix for that error, which leaves more room if GitHub's headers keep growing (by @WillyJL)
