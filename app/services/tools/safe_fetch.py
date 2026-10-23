"""SSRF-hardened HTTP(S) fetch for external research.

Guarantees (all enforced per hop, including every redirect):
- only http/https, no embedded credentials, only ports 80/443 unless the
  caller explicitly allows more;
- the hostname is resolved once and EVERY resolved address must be globally
  routable (loopback, private, link-local, metadata 169.254.x, CGNAT,
  multicast, reserved and unspecified are refused, including IPv4-mapped
  IPv6 forms and integer/hex host spellings, which the resolver normalises);
- the TCP connection is pinned to the validated address, so a second DNS
  answer (DNS rebinding) can never redirect the request;
- redirects are followed manually (max 5) and re-validated;
- environment proxies are never used (http.client ignores them), so a proxy
  cannot be used to reach internal hosts;
- bounded connect/read timeout, bounded total wall clock, bounded body size,
  and only text-like content types are read;
- no cookies, auth headers or OMOS credentials are ever attached.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 1_000_000
DEFAULT_TIMEOUT_S = 10
DEFAULT_TOTAL_S = 20
ALLOWED_PORTS = frozenset({80, 443})
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_USER_AGENT = "Mozilla/5.0 (compatible; OMOS/1.0; website-audit)"


class UnsafeURLError(ValueError):
    """The URL (or a redirect/resolution of it) is not allowed."""


# NAT64 / 6to4 / Teredo can embed an internal IPv4 address; refuse them.
_TRANSLATION_NETS = tuple(ipaddress.ip_network(n) for n in
                          ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32"))


def _check_ip(raw: str) -> ipaddress._BaseAddress:
    ip = ipaddress.ip_address(raw.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.version == 6 and any(ip in net for net in _TRANSLATION_NETS):
        raise UnsafeURLError("destination address is not publicly routable")
    if not ip.is_global or ip.is_multicast:
        raise UnsafeURLError("destination address is not publicly routable")
    return ip


def resolve_public(host: str, port: int, resolver=None) -> list[str]:
    """Resolve *host* and return validated addresses; raise if any is unsafe."""
    if not host:
        raise UnsafeURLError("missing host")
    resolver = resolver or socket.getaddrinfo
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURLError("host could not be resolved") from exc
    addrs: list[str] = []
    for info in infos:
        addr = info[4][0]
        _check_ip(addr)  # every answer must be public, not just the first
        if addr not in addrs:
            addrs.append(addr)
    if not addrs:
        raise UnsafeURLError("host could not be resolved")
    return addrs


def validate_url(url: str, *, allowed_ports=ALLOWED_PORTS):
    """Static URL checks. Returns the parsed URL."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("malformed url") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError("only http and https are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("credentials in urls are not allowed")
    if not parts.hostname:
        raise UnsafeURLError("missing host")
    effective = port or (443 if parts.scheme.lower() == "https" else 80)
    if allowed_ports and effective not in allowed_ports:
        raise UnsafeURLError("port is not allowed")
    return parts


class _PinnedMixin:
    _pinned_ip = ""

    def _connect_pinned(self):
        return socket.create_connection(
            (self._pinned_ip, self.port), self.timeout)


class _PinnedHTTP(_PinnedMixin, http.client.HTTPConnection):
    def connect(self):
        self.sock = self._connect_pinned()


class _PinnedHTTPS(_PinnedMixin, http.client.HTTPSConnection):
    def connect(self):
        sock = self._connect_pinned()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _open_pinned(parts, ip: str, timeout: float):
    https = parts.scheme.lower() == "https"
    port = parts.port or (443 if https else 80)
    if https:
        conn = _PinnedHTTPS(parts.hostname, port, timeout=timeout,
                            context=ssl.create_default_context())
    else:
        conn = _PinnedHTTP(parts.hostname, port, timeout=timeout)
    conn._pinned_ip = ip
    return conn


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


def safe_get(url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
             total_s: float = DEFAULT_TOTAL_S, max_bytes: int = MAX_BODY_BYTES,
             resolver=None, opener=None) -> dict:
    """Fetch *url* safely. Raises UnsafeURLError / OSError on any failure.

    Returns {"final_url", "status", "headers", "body" (bytes), "charset"}.
    ``opener(parts, ip, timeout)`` and ``resolver`` are injectable for tests.
    """
    opener = opener or _open_pinned
    deadline = time.monotonic() + total_s
    current = str(url).strip()
    for _hop in range(MAX_REDIRECTS + 1):
        parts = validate_url(current)
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        addrs = resolve_public(parts.hostname, port, resolver)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        resp = conn = None
        for ip in addrs:  # every address is already validated as public
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("fetch exceeded its time budget")
            conn = opener(parts, ip, min(timeout_s, remaining))
            try:
                conn.request("GET", path, headers={
                    "Host": parts.netloc, "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "identity", "Connection": "close"})
                resp = conn.getresponse()
                break
            except OSError:
                _close(conn)
                resp = None
        if resp is None:
            raise OSError("could not connect")
        try:
            status = int(resp.status)
            if status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    raise UnsafeURLError("redirect without destination")
                current = urllib.parse.urljoin(current, location)
                continue
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and not ctype.startswith(_TEXT_TYPES):
                raise UnsafeURLError("content type is not text")
            chunks, size, truncated = [], 0, False
            while size <= max_bytes:
                if time.monotonic() > deadline:
                    raise TimeoutError("fetch exceeded its time budget")
                chunk = resp.read(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            body = b"".join(chunks)
            if len(body) > max_bytes:
                body, truncated = body[:max_bytes], True
            return {"final_url": current, "status": status,
                    "headers": dict(resp.getheaders()), "body": body,
                    "truncated": truncated,
                    "charset": resp.headers.get_content_charset() or "utf-8"}
        finally:
            _close(conn)
    raise UnsafeURLError("too many redirects")
