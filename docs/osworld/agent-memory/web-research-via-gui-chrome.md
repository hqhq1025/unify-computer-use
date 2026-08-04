---
name: web-research-via-gui-chrome
description: On this VM, search engines block curl/headless Chrome; use GUI Chrome on DISPLAY=:0 and read results from the accessibility tree.
metadata:
  type: project
---

Web research on this VM must go through the GUI browser, not the shell. Verified 2026-08-04: Bing, Google, Baidu, Sogou, DuckDuckGo, Mojeek and searx all return captcha/anti-bot pages to `curl` AND to `google-chrome --headless=new --dump-dom` from this IP. The same Bing query in **GUI Chrome** returns full results.

**Why:** the machine's egress IP is flagged as automated traffic; only a real browser session gets through.

**How to apply:**
1. The Bash tool has no `DISPLAY` set — launch GUI apps with `DISPLAY=:0 setsid nohup <app> ... </dev/null >/tmp/x.log 2>&1 & disown`, then `sleep ~15`.
2. Read the results with `mcp__ocu__get_app_state --app chrome` (a11y tree carries the snippets and headings).
3. Click through to a result, then read the landed URL from the `entry "Address and search bar"` value — content sites themselves (e.g. bendibao.com) are NOT blocked, so once you have the URL you can `curl` the article and parse it cheaply in the shell.

See [[libreoffice-docs-opened-without-display]].
