# Web research (optional)

OMOS never invents research. Without a search provider, research answers say
that nothing was looked up. To enable real, free, local web search:

1. Install Docker Desktop and start it.
2. Edit `config/searxng/settings.yml` and replace `secret_key` with your own value.
3. Run `docker compose -f config/searxng/docker-compose.yml up -d`.
4. Start OMOS with the environment variable
   `WEB_SEARCH_URL=http://127.0.0.1:8888/search`.
5. Ask a research question. The work package under a campaign lists the
   sources only when a real search returned them.

SearXNG queries public search engines on your behalf, so your search terms
leave your computer. Fetched pages are checked so research cannot reach
local or private addresses. Any other SearXNG-compatible JSON endpoint works
the same way; OMOS needs `{"results": [{"url", "title", "content"}]}`.
