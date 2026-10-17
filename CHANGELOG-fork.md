## ⬆️ From upstream
### Added:
- Parallelized image I/O and decoding (by @WillyJL):
  - Multiple threads can now be allocated for image loading
  - Customizable parameters In Settings > Images for how many threads to use for I/O, decoding and limit to concurrent GIF decoding
  - Means that next images in queue can start being fetched from disk while previous images are still decoding, and multiple of these can happen at once
  - Source images (with Tex compress option disabled) can now load much faster, like they should have to begin with
  - Compressed images (with Tex compress option enabled) can now load even faster, making loading truly unnoticeable, though the tradeoff might not be worth to most users
  - Due to this, Settings > Images > Unload off-screen is now on by default, and existing users who have it off should consider enabling it to save RAM and VRAM
  - The default (4 I/O threads, 3 Decode threads, 2 Decode GIFs) should be fine for most users, but tweaking for your hardware can help (if you go all out you might get stutters)
  - If you want the previous behavior (one image at a time) set all 3 parameters (I/O threads, Decode threads and Decode GIFs) to 1
  - Note that changing these parameters requires restarting the app
- When F95zone answers with a Cloudflare captcha, a popup now says so and offers to solve it in the integrated browser, instead of reporting it as server downtime (by @WillyJL)

### Updated:
- Adding an executable to an animation or comic (Video, Pinup and such) now opens the folder picker by default (by @WillyJL)
- Requests to the F95zone forum now send the integrated browser's user agent instead of F95Checker's own (by @WillyJL)
- Latest updates and direct download lookups no longer wait in the forum request queue (by @WillyJL)

### Fixed:
- Cloudflare's "Just a moment..." check can be passed in the integrated browser, the F95Checker page script no longer runs while it loads and breaks it (by @WillyJL)
