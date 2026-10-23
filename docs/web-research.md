# Web research (optional)

OMOS never invents research. Without a search provider it says nothing was
looked up, and the whole business workflow (goal, campaign, review, approval,
export) keeps working. **You do not need Docker unless you want web research.**

## How sources are shown

- **Search result only - page not checked**: a search engine returned the link
  and OMOS has not opened the page. Treat it as a lead to verify.
- **Page opened and read by OMOS**: OMOS fetched the page itself.

Sources are saved with your project, so they are still there after a reload or
restart. They appear under "Found online" in a campaign's work package, and
never replace or change approved campaign content.

## Turn on free, local web search

1. Install Docker Desktop and start it.
2. Run `python scripts/setup_searxng.py`. It creates `config/searxng/.env` with
   a private random secret. The file is git-ignored; never share it. Docker
   Compose refuses to start without it, so no shared or placeholder secret can
   be used.
3. Run `docker compose -f config/searxng/docker-compose.yml up -d`.
4. Start OMOS with `WEB_SEARCH_URL=http://127.0.0.1:8888/search`.

The container image is pinned to a dated release and digest. To update it,
choose a newer tag on Docker Hub, confirm its digest, and edit both in
`config/searxng/docker-compose.yml`.

## Privacy and network

- SearXNG listens on `127.0.0.1:8888` only. Do not publish that port.
- **SearXNG itself contacts external search engines** on your behalf, so your
  search terms leave your computer. OMOS does not send them anywhere else.
- Pages OMOS opens are checked so research cannot reach local or private
  addresses.
- Any SearXNG-compatible JSON endpoint works; OMOS needs
  `{"results": [{"url", "title", "content"}]}`.
