## ⬆️ From upstream
### Fixed:
- Solving a Cloudflare captcha now actually lets F95Checker through: the captcha window opens logged in with your F95zone cookies and saves the cookies it gets back, where before the solved captcha stayed in the window and requests kept getting blocked (by @WillyJL)
- The "Insecure SSL" option works again, and images from F95zone's backup attachments host load again, both failed every request since 11.4.8 (by @WillyJL)
