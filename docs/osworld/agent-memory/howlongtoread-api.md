---
name: howlongtoread-api
description: Undocumented JSON API behind howlongtoread.com for book word counts and reading times
metadata:
  type: reference
---

howlongtoread.com is a React SPA; its data comes from `https://api.howlongtoread.com` (no auth needed):

- `GET /books/search/<url-encoded query>` → list of `{id, title, author, averageReadingTime}`
- `GET /books/id/<id>` → full details including `wordCount: {value, verified, estimationExplanation}`, `readingTime` (seconds), `numPages`
- `GET /books/goodreads/<goodreadsId>` → `{id}` mapping
- `GET /books/isbn/<isbn>`, `/books/series/<id>`, `/books/similar-to/<id>`

The page HTML contains no word count, so scraping `howlongtoread.com/books/<id>` is useless — hit the API. Beware: search returns box sets and study guides; match on author. Related: [[libreoffice-headless-instance-quirk]]
