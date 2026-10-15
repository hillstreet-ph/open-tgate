"""Safe fetch + text extraction for 'import AI knowledge from a URL'.

This powers the operator action of turning a web page or text file into an
approved-knowledge source (the "extract artifact" step). It is only reachable
by an authenticated operator holding the ``knowledge:write`` scope, and the
extracted title + text are returned to the operator to review before they save
them — a human always stays in the loop; nothing is auto-approved.

The fetch is hardened against SSRF so the operator (or a stolen key) cannot use
it to reach internal services:

* http/https only.
* The host is DNS-resolved and *every* resolved address is checked against
  private / loopback / link-local / reserved / multicast ranges — this blocks
  cloud metadata endpoints (169.254.169.254) and internal hosts.
* Redirects are followed manually, at most a few hops, re-validating the host
  of every hop.
* Hard caps on time and downloaded bytes, and only textual content types.

HTML is reduced to plain text with the standard library only (no new
dependency). Headings and block elements become line breaks so the extracted
text stays readable.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2 * 1024 * 1024  # 2 MiB download cap
MAX_CHARS = 100_000  # matches the knowledge-source content limit
MAX_TITLE = 160  # matches the knowledge-source title limit
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 10.0
_ALLOWED_CONTENT = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown")
_USER_AGENT = "OpenTGate-KnowledgeImport/1.0"


class UrlImportError(Exception):
    """A user-facing reason a URL could not be imported."""


def _assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise UrlImportError("The link could not be resolved.") from None
    if not infos:
        raise UrlImportError("The link could not be resolved.")
    for info in infos:
        raw = info[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UrlImportError("That address is not allowed (internal or reserved).")


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlImportError("Only http and https links can be imported.")
    if not parsed.hostname:
        raise UrlImportError("The link has no host.")
    _assert_public_host(parsed.hostname)
    return url


class _TextExtractor(HTMLParser):
    """Collapse an HTML document into readable plain text + its <title>."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
    _BLOCK = {
        "p", "div", "section", "article", "li", "ul", "ol", "br", "tr", "table",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "main", "nav", "blockquote",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            # Title text is metadata, never body text — capture it and stop so it
            # does not leak into the extracted content (the <title> may sit
            # outside a <head>, e.g. in a fragment).
            if self.title is None:
                stripped = data.strip()
                if stripped:
                    self.title = stripped[:MAX_TITLE]
            return
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _extract(content_type: str, body: str, url: str) -> tuple[str, str]:
    host = urlparse(url).hostname or "Imported source"
    if "html" in content_type:
        parser = _TextExtractor()
        parser.feed(body)
        text = parser.text()
        title = parser.title or host
    else:
        text = body.strip()
        title = host
    if not text:
        raise UrlImportError("No readable text was found at that link.")
    return title[:MAX_TITLE], text[:MAX_CHARS]


def import_url(url: str) -> dict:
    """Fetch ``url`` safely and return ``{title, content, source_url}``.

    Raises :class:`UrlImportError` with an operator-facing message on any
    disallowed target, transport problem, or empty result.
    """

    current = _validate_url(url.strip())
    original = current
    redirects = 0
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain,text/markdown;q=0.9"}
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, headers=headers) as client:
        while True:
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        redirects += 1
                        if redirects > MAX_REDIRECTS:
                            raise UrlImportError("The link has too many redirects.")
                        location = resp.headers.get("location")
                        if not location:
                            raise UrlImportError("A redirect was missing its target.")
                        current = _validate_url(urljoin(current, location))
                        continue
                    if resp.status_code != 200:
                        raise UrlImportError(f"The link returned HTTP {resp.status_code}.")
                    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if ctype and ctype not in _ALLOWED_CONTENT:
                        raise UrlImportError("Only web pages and text files can be imported.")
                    total = 0
                    chunks: list[bytes] = []
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= MAX_BYTES:
                            break
                    encoding = resp.encoding or "utf-8"
            except httpx.HTTPError as exc:
                raise UrlImportError("The link could not be fetched.") from exc
            break
    body = b"".join(chunks)[:MAX_BYTES].decode(encoding, errors="replace")
    title, content = _extract(ctype, body, original)
    return {"title": title, "content": content, "source_url": original}
