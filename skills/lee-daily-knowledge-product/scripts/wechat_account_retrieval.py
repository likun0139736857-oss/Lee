"""Account-level WeChat Official Account discovery and article reading.

The script keeps coverage semantics explicit:

- confirmed: a configured account feed was fetched successfully;
- partial: an exact-author Sogou WeChat query ran, but the public index is incomplete;
- unavailable: no account-level backend completed.

Creator Buddy hot-list data is intentionally not handled here because it is a
topic-level signal source, not an account feed.
"""

from __future__ import annotations

import argparse
import email.utils
import html as html_std
import json
import os
import pathlib
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
    urlopen,
)

try:
    from lxml import html as lxml_html
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "lxml is required. Run this script with Codex bundled Python."
    ) from exc


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
CHINA_TZ = timezone(timedelta(hours=8))
SOGOU_SEARCH_URL = "https://weixin.sogou.com/weixin"
MAX_ARTICLE_CHARS = 40000


class RetrievalError(RuntimeError):
    """An expected backend or parsing failure."""


def normalize_name(value: object) -> str:
    return re.sub(r"[\s·•._\-—–]+", "", str(value or "")).casefold()


def _node_text(node: Any | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _first(nodes: list[Any]) -> Any | None:
    return nodes[0] if nodes else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CHINA_TZ)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CHINA_TZ)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_from_epoch(value: str) -> str:
    try:
        return datetime.fromtimestamp(int(value), CHINA_TZ).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _html_to_text(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    try:
        document = lxml_html.fromstring(raw)
        return " ".join(document.text_content().split())
    except (ValueError, TypeError):
        return " ".join(html_std.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def _within_window(published_at: object, start: date, end: date) -> bool:
    parsed = _parse_date(published_at)
    if parsed is None:
        return False
    return start <= parsed.astimezone(CHINA_TZ).date() <= end


def _dedupe_articles(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in sorted(
        items,
        key=lambda row: str(row.get("published_at", "")),
        reverse=True,
    ):
        key = (
            str(item.get("stable_url") or item.get("source_url") or "").strip()
            or f"{normalize_name(item.get('account_name'))}:"
            f"{normalize_name(item.get('title'))}:"
            f"{str(item.get('published_at', ''))[:10]}"
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_sogou_results(
    raw_html: str,
    query: str,
    aliases: list[str],
) -> list[dict[str, Any]]:
    """Parse Sogou WeChat article results and mark exact publisher matches."""

    document = lxml_html.fromstring(raw_html)
    rows: list[dict[str, Any]] = []
    normalized_aliases = {normalize_name(alias) for alias in aliases if alias}
    xpath = (
        '//ul[contains(concat(" ",normalize-space(@class)," "),'
        '" news-list ")]/li'
    )
    for item in document.xpath(xpath):
        title_node = _first(item.xpath(".//h3"))
        link_node = _first(item.xpath(".//h3/a"))
        account_node = _first(
            item.xpath(
                './/*[contains(concat(" ",normalize-space(@class)," "),'
                '" s-p ")]'
                '//*[contains(concat(" ",normalize-space(@class)," "),'
                '" all-time-y2 ")]'
            )
        )
        summary_node = _first(
            item.xpath(
                './/*[contains(concat(" ",normalize-space(@class)," "),'
                '" txt-info ")]'
            )
        )
        script_node = _first(
            item.xpath(
                './/*[contains(concat(" ",normalize-space(@class)," "),'
                '" s-p ")]//script'
            )
        )
        title = _node_text(title_node)
        account_name = _node_text(account_node)
        if not title or link_node is None:
            continue
        script_text = _node_text(script_node)
        timestamp_match = re.search(r"timeConvert\(['\"]?(\d+)", script_text)
        published_at = (
            _iso_from_epoch(timestamp_match.group(1)) if timestamp_match else ""
        )
        href = str(link_node.get("href") or "")
        rows.append(
            {
                "title": title,
                "summary": _node_text(summary_node),
                "account_name": account_name,
                "published_at": published_at,
                "sogou_result_url": urljoin(SOGOU_SEARCH_URL, href),
                "search_query": query,
                "identity_match": normalize_name(account_name)
                in normalized_aliases,
                "source_backend": "sogou_wechat",
                "coverage_level": "partial",
                "verification_required": True,
                "link_stability": "ephemeral",
            }
        )
    return rows


def parse_feed(raw: bytes, feed_url: str) -> list[dict[str, Any]]:
    """Parse RSS, Atom, or JSON Feed content."""

    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return []
    if text.startswith("{"):
        payload = json.loads(text)
        result = []
        for item in payload.get("items", []) or []:
            authors = item.get("authors") or []
            author = ""
            if authors and isinstance(authors[0], dict):
                author = str(authors[0].get("name") or "")
            body = str(
                item.get("content_text")
                or _html_to_text(item.get("content_html", ""))
            )
            result.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "summary": str(item.get("summary") or "").strip(),
                    "account_name": author,
                    "published_at": str(
                        item.get("date_published")
                        or item.get("date_modified")
                        or ""
                    ),
                    "source_url": str(
                        item.get("url") or item.get("external_url") or ""
                    ),
                    "stable_url": str(
                        item.get("url") or item.get("external_url") or ""
                    ),
                    "body_text": body[:MAX_ARTICLE_CHARS],
                    "content_available": bool(body),
                    "source_backend": "rss",
                    "coverage_level": "confirmed",
                    "verification_required": True,
                    "link_stability": "stable",
                    "feed_url": feed_url,
                }
            )
        return result

    root = ET.fromstring(raw)
    entries = [
        node
        for node in root.iter()
        if _local_name(node.tag) in {"item", "entry"}
    ]
    result = []
    for entry in entries:
        fields: dict[str, list[ET.Element]] = {}
        for child in list(entry):
            fields.setdefault(_local_name(child.tag), []).append(child)

        def field_text(*names: str) -> str:
            for name in names:
                nodes = fields.get(name, [])
                if nodes:
                    return " ".join("".join(nodes[0].itertext()).split())
            return ""

        source_url = ""
        for link in fields.get("link", []):
            candidate = str(link.attrib.get("href") or link.text or "").strip()
            if candidate:
                source_url = candidate
                if link.attrib.get("rel", "alternate") == "alternate":
                    break
        content = field_text("content", "encoded", "description", "summary")
        result.append(
            {
                "title": field_text("title"),
                "summary": _html_to_text(field_text("summary", "description")),
                "account_name": field_text("author", "creator"),
                "published_at": field_text(
                    "published", "pubdate", "updated", "date"
                ),
                "source_url": source_url,
                "stable_url": source_url,
                "body_text": _html_to_text(content)[:MAX_ARTICLE_CHARS],
                "content_available": bool(content),
                "source_backend": "rss",
                "coverage_level": "confirmed",
                "verification_required": True,
                "link_stability": "stable",
                "feed_url": feed_url,
            }
        )
    return result


@dataclass
class SogouClient:
    timeout: int = 30
    delay_seconds: float = 1.2

    def __post_init__(self) -> None:
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def _open(self, url: str, referer: str | None = None) -> tuple[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if referer:
            headers["Referer"] = referer
        request = Request(url, headers=headers)
        response = self.opener.open(request, timeout=self.timeout)
        raw = response.read()
        content_type = str(response.headers.get("content-type") or "")
        encoding = "gbk" if "gb" in content_type.casefold() else "utf-8"
        return raw.decode(encoding, errors="ignore"), response.geturl()

    def search(self, query: str, aliases: list[str]) -> list[dict[str, Any]]:
        params = urlencode(
            {
                "type": "2",
                "s_from": "input",
                "query": query,
                "ie": "utf8",
            }
        )
        url = f"{SOGOU_SEARCH_URL}?{params}"
        raw, _ = self._open(url)
        if "news-list" not in raw and (
            "访问过于频繁" in raw or "请输入验证码" in raw
        ):
            raise RetrievalError("Sogou WeChat blocked the request")
        time.sleep(max(0, self.delay_seconds))
        return parse_sogou_results(raw, query, aliases)

    def resolve_result(self, result_url: str, search_url: str) -> str:
        raw, _ = self._open(result_url, referer=search_url)
        fragments = re.findall(r"url\s*\+=\s*'([^']*)'", raw)
        target = "".join(fragments).replace("@", "")
        if not target.startswith("https://mp.weixin.qq.com/"):
            raise RetrievalError("Sogou result did not resolve to a WeChat article")
        # Do not run html.unescape over the whole URL: a query parameter such
        # as "&timestamp" would be interpreted as the `&times` entity and
        # turned into a non-ASCII multiplication sign.
        return target.replace("&amp;", "&")

    def read_article(self, url: str) -> dict[str, Any]:
        raw, final_url = self._open(url, referer="https://weixin.sogou.com/")
        if "环境异常" in raw or "访问过于频繁" in raw:
            raise RetrievalError("WeChat article page requires verification")
        document = lxml_html.fromstring(raw)
        title = _node_text(_first(document.xpath('//*[@id="activity-name"]')))
        account_name = _node_text(_first(document.xpath('//*[@id="js_name"]')))
        body_text = _node_text(_first(document.xpath('//*[@id="js_content"]')))
        if not title or not body_text:
            raise RetrievalError("WeChat article body was not found")
        ct_match = re.search(r'var\s+ct\s*=\s*["\']?(\d+)', raw)
        biz_match = re.search(r'var\s+biz\s*=\s*["\']([^"\']+)', raw)
        mid_match = re.search(r'var\s+mid\s*=\s*["\']([^"\']+)', raw)
        idx_match = re.search(r'var\s+idx\s*=\s*["\']([^"\']+)', raw)
        user_name_match = re.search(
            r'var\s+user_name\s*=\s*["\']([^"\']+)', raw
        )
        return {
            "title": title,
            "account_name": account_name,
            "published_at": (
                _iso_from_epoch(ct_match.group(1)) if ct_match else ""
            ),
            "source_url": final_url,
            "body_text": body_text[:MAX_ARTICLE_CHARS],
            "content_available": True,
            "wechat_identity": {
                "biz": biz_match.group(1) if biz_match else "",
                "mid": mid_match.group(1) if mid_match else "",
                "idx": idx_match.group(1) if idx_match else "",
                "user_name": (
                    user_name_match.group(1) if user_name_match else ""
                ),
            },
        }


def fetch_url(url: str, timeout: int = 30) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RetrievalError("Only HTTP(S) feed URLs are allowed")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def load_feed_registry() -> dict[str, list[str]]:
    path = os.environ.get("LEE_WECHAT_FEEDS_JSON", "").strip()
    if not path:
        return {}
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for name, value in payload.items():
        if isinstance(value, str):
            result[str(name)] = [value]
        elif isinstance(value, list):
            result[str(name)] = [str(item) for item in value if item]
    return result


def account_feed_urls(
    account: dict[str, Any], registry: dict[str, list[str]]
) -> list[str]:
    urls = [str(item) for item in account.get("feed_urls", []) if item]
    canonical = str(account.get("canonical_name") or "")
    urls.extend(registry.get(canonical, []))
    return list(dict.fromkeys(urls))


def load_verified_input(
    path: str, run_date: date
) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if str(payload.get("run_date") or "") != run_date.isoformat():
        raise RetrievalError(
            "Verified WeChat input run_date does not match the fetch run_date"
        )
    result: dict[str, dict[str, Any]] = {}
    for account in payload.get("accounts", []):
        canonical = str(account.get("canonical_name") or "").strip()
        if canonical:
            result[normalize_name(canonical)] = account
    return result


def verified_desktop_articles(
    verified: dict[str, Any],
    canonical: str,
    aliases: list[str],
    *,
    source_backend: str = "wechat_desktop",
    coverage_level: str = "confirmed",
) -> list[dict[str, Any]]:
    normalized_aliases = {normalize_name(alias) for alias in aliases}
    result = []
    for item in verified.get("articles", []):
        account_name = str(item.get("account_name") or canonical)
        body_text = str(item.get("body_text") or "")
        result.append(
            {
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "account_name": account_name,
                "published_at": str(item.get("published_at") or ""),
                "source_url": str(item.get("source_url") or ""),
                "stable_url": str(item.get("source_url") or ""),
                "body_text": body_text[:MAX_ARTICLE_CHARS],
                "content_available": bool(body_text),
                "source_backend": source_backend,
                "coverage_level": coverage_level,
                "verification_required": True,
                "link_stability": (
                    "stable" if item.get("source_url") else "session_only"
                ),
                "priority_account": canonical,
                "identity_match": normalize_name(account_name)
                in normalized_aliases,
                "engagement": item.get("engagement", {}),
            }
        )
    return result


def retrieve_accounts(
    source_pool: dict[str, Any],
    run_date: date,
    hot_days: int = 3,
    case_days: int = 30,
    read_limit_per_account: int = 1,
    only_account: str = "",
    timeout: int = 30,
    delay_seconds: float = 1.2,
    verified_accounts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    case_start = run_date - timedelta(days=max(1, case_days) - 1)
    hot_start = run_date - timedelta(days=max(1, hot_days) - 1)
    registry = load_feed_registry()
    verified_accounts = verified_accounts or {}
    client = SogouClient(timeout=timeout, delay_seconds=delay_seconds)
    accounts_out: list[dict[str, Any]] = []

    selected = source_pool.get("priority_accounts", [])
    if only_account:
        wanted = normalize_name(only_account)
        selected = [
            account
            for account in selected
            if wanted
            in {
                normalize_name(account.get("canonical_name")),
                *{
                    normalize_name(alias)
                    for alias in account.get("aliases", [])
                },
            }
        ]
        if not selected:
            raise RetrievalError(f"Unknown priority account: {only_account}")

    for account in selected:
        canonical = str(account.get("canonical_name") or "")
        aliases = [
            canonical,
            *[str(alias) for alias in account.get("aliases", [])],
        ]
        aliases = list(dict.fromkeys(alias for alias in aliases if alias))
        articles: list[dict[str, Any]] = []
        backend_runs: list[dict[str, Any]] = []
        index_freshness = "not_applicable"
        latest_indexed_at = ""
        requires_wechat_verification = False
        verified_success = False
        platform_search_success = False
        verified = verified_accounts.get(normalize_name(canonical))
        if verified:
            identity_verified = bool(
                verified.get("account_identity_verified")
            )
            history_checked = bool(verified.get("history_page_checked"))
            platform_search_checked = bool(
                verified.get("platform_search_checked")
            )
            if identity_verified and history_checked:
                desktop_items = verified_desktop_articles(
                    verified, canonical, aliases
                )
                articles.extend(desktop_items)
                verified_success = True
                backend_runs.append(
                    {
                        "backend": "wechat_desktop",
                        "status": "ok",
                        "checked_at": verified.get("checked_at", ""),
                        "returned": len(desktop_items),
                        "account_identity_verified": True,
                        "history_page_checked": True,
                    }
                )
            elif identity_verified and platform_search_checked:
                desktop_items = verified_desktop_articles(
                    verified,
                    canonical,
                    aliases,
                    source_backend="wechat_authenticated_search",
                    coverage_level="partial",
                )
                articles.extend(desktop_items)
                platform_search_success = True
                requires_wechat_verification = True
                backend_runs.append(
                    {
                        "backend": "wechat_authenticated_search",
                        "status": "ok",
                        "checked_at": verified.get("checked_at", ""),
                        "returned": len(desktop_items),
                        "account_identity_verified": True,
                        "platform_search_checked": True,
                        "history_page_checked": False,
                    }
                )
            else:
                backend_runs.append(
                    {
                        "backend": "wechat_desktop",
                        "status": "rejected",
                        "reason": (
                            "account_identity_verified and "
                            "history_page_checked must both be true"
                        ),
                    }
                )
        feed_success = False
        feed_urls = account_feed_urls(account, registry)

        for feed_url in [] if verified_success else feed_urls:
            try:
                feed_items = parse_feed(fetch_url(feed_url, timeout), feed_url)
                for item in feed_items:
                    if not item.get("account_name"):
                        item["account_name"] = canonical
                    item["priority_account"] = canonical
                    item["identity_match"] = normalize_name(
                        item.get("account_name")
                    ) in {normalize_name(alias) for alias in aliases}
                articles.extend(feed_items)
                feed_success = True
                backend_runs.append(
                    {
                        "backend": "rss",
                        "status": "ok",
                        "feed_url": feed_url,
                        "returned": len(feed_items),
                    }
                )
            except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
                backend_runs.append(
                    {
                        "backend": "rss",
                        "status": "error",
                        "feed_url": feed_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        sogou_success = False
        if not verified_success and not platform_search_success and not feed_success:
            query = canonical
            search_url = (
                f"{SOGOU_SEARCH_URL}?"
                + urlencode(
                    {
                        "type": "2",
                        "s_from": "input",
                        "query": query,
                        "ie": "utf8",
                    }
                )
            )
            try:
                results = client.search(query, aliases)
                sogou_success = True
                exact = [item for item in results if item["identity_match"]]
                dated_exact = [
                    (parsed, item)
                    for item in exact
                    if (parsed := _parse_date(item.get("published_at"))) is not None
                ]
                if dated_exact:
                    latest_date, latest_item = max(
                        dated_exact, key=lambda pair: pair[0]
                    )
                    latest_indexed_at = str(
                        latest_item.get("published_at") or ""
                    )
                    index_freshness = (
                        "current"
                        if latest_date.astimezone(CHINA_TZ).date() >= hot_start
                        else "stale"
                    )
                else:
                    index_freshness = "unknown"
                requires_wechat_verification = True
                for item in exact:
                    item["priority_account"] = canonical
                    item["search_url"] = search_url
                articles.extend(exact)
                backend_runs.append(
                    {
                        "backend": "sogou_wechat",
                        "status": "ok",
                        "query": query,
                        "returned": len(results),
                        "exact_author_matches": len(exact),
                        "latest_exact_author_result_at": latest_indexed_at,
                        "index_freshness": index_freshness,
                    }
                )
            except (HTTPError, URLError, TimeoutError, RetrievalError) as exc:
                backend_runs.append(
                    {
                        "backend": "sogou_wechat",
                        "status": "error",
                        "query": query,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        articles = [
            item
            for item in _dedupe_articles(articles)
            if _within_window(item.get("published_at"), case_start, run_date)
        ]

        if read_limit_per_account > 0:
            unresolved = [
                item
                for item in articles
                if item.get("source_backend") == "sogou_wechat"
                and item.get("sogou_result_url")
            ][:read_limit_per_account]
            for item in unresolved:
                try:
                    resolved = client.resolve_result(
                        str(item["sogou_result_url"]),
                        str(item.get("search_url") or search_url),
                    )
                    item.update(client.read_article(resolved))
                    item["source_backend"] = "sogou_wechat"
                    item["coverage_level"] = "partial"
                    item["link_stability"] = "ephemeral"
                    item["priority_account"] = canonical
                    item["identity_match"] = normalize_name(
                        item.get("account_name")
                    ) in {normalize_name(alias) for alias in aliases}
                except (
                    HTTPError,
                    URLError,
                    TimeoutError,
                    RetrievalError,
                    ValueError,
                ) as exc:
                    item["read_error"] = f"{type(exc).__name__}: {exc}"
                    item["content_available"] = False

        if verified_success:
            coverage_status = "confirmed"
            coverage_reason = (
                "Logged-in WeChat account identity and history page verified"
            )
        elif feed_success:
            coverage_status = "confirmed"
            coverage_reason = "Configured account feed fetched successfully"
        elif platform_search_success:
            coverage_status = "partial"
            coverage_reason = (
                "Logged-in WeChat search verified the account identity and current "
                "results, but the account history page was not checked"
            )
        elif sogou_success:
            coverage_status = "partial"
            if index_freshness == "stale":
                coverage_reason = (
                    "Exact-author public index queried, but its latest result "
                    f"({latest_indexed_at}) is stale and does not verify current "
                    "WeChat updates"
                )
            elif index_freshness == "unknown":
                coverage_reason = (
                    "Exact-author public index queried, but no dated exact-author "
                    "result was available; this does not verify current WeChat updates"
                )
            else:
                coverage_reason = (
                    "Exact-author public index queried; the index is not a complete "
                    "WeChat account feed and still requires WeChat verification"
                )
        else:
            coverage_status = "unavailable"
            coverage_reason = "No account-level backend completed"

        current_hits = [
            item
            for item in articles
            if _within_window(item.get("published_at"), hot_start, run_date)
        ]
        accounts_out.append(
            {
                "canonical_name": canonical,
                "aliases": aliases,
                "focus": account.get("focus", []),
                "coverage_status": coverage_status,
                "coverage_reason": coverage_reason,
                "index_freshness": index_freshness,
                "latest_indexed_at": latest_indexed_at,
                "requires_wechat_verification": requires_wechat_verification,
                "backend_runs": backend_runs,
                "articles": articles,
                "current_hits": len(current_hits),
                "case_hits": len(articles),
                "content_readable": sum(
                    1 for item in articles if item.get("content_available")
                ),
            }
        )

    confirmed = [
        item["canonical_name"]
        for item in accounts_out
        if item["coverage_status"] == "confirmed"
    ]
    partial = [
        item["canonical_name"]
        for item in accounts_out
        if item["coverage_status"] == "partial"
    ]
    unavailable = [
        item["canonical_name"]
        for item in accounts_out
        if item["coverage_status"] == "unavailable"
    ]
    matched = [
        item["canonical_name"] for item in accounts_out if item["articles"]
    ]
    current_matched = [
        item["canonical_name"] for item in accounts_out if item["current_hits"]
    ]
    return {
        "generated_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        "run_date": run_date.isoformat(),
        "hot_window": {
            "start": hot_start.isoformat(),
            "end": run_date.isoformat(),
        },
        "case_window": {
            "start": case_start.isoformat(),
            "end": run_date.isoformat(),
        },
        "accounts": accounts_out,
        "coverage": {
            "confirmed": confirmed,
            "partial": partial,
            "unavailable": unavailable,
            "matched": matched,
            "current_matched": current_matched,
            "not_confirmed": [
                name
                for name in [*partial, *unavailable]
                if name not in confirmed
            ],
        },
        "coverage_contract": {
            "confirmed": "A configured per-account feed completed",
            "wechat_desktop": (
                "Logged-in account identity and history page were verified "
                "for this run date"
            ),
            "partial": "An exact-author public index query completed",
            "unavailable": "No account-level backend completed",
            "creator_buddy": "Topic hot-list only; never counts as account coverage",
        },
    }


def doctor(source_pool: dict[str, Any], probe: bool, timeout: int) -> dict[str, Any]:
    registry = load_feed_registry()
    configured = []
    for account in source_pool.get("priority_accounts", []):
        if account_feed_urls(account, registry):
            configured.append(account.get("canonical_name"))
    result: dict[str, Any] = {
        "account_count": len(source_pool.get("priority_accounts", [])),
        "rss": {
            "status": "configured" if configured else "not_configured",
            "configured_accounts": configured,
            "unattended": True,
            "coverage_level": "confirmed",
        },
        "wechat_desktop": {
            "status": "interactive_only",
            "requirement": (
                "Logged-in WeChat window targetable through Computer Use"
            ),
            "unattended": False,
            "coverage_level": "confirmed_when_ui_results_are_verified",
        },
        "sogou_wechat": {
            "status": "not_probed",
            "unattended": True,
            "coverage_level": "partial",
        },
        "article_reader": {
            "direct_mp_weixin": True,
            "sogou_result_resolution": True,
            "full_text_extraction": True,
        },
    }
    if probe and source_pool.get("priority_accounts"):
        account = source_pool["priority_accounts"][0]
        canonical = str(account.get("canonical_name") or "")
        aliases = [canonical, *account.get("aliases", [])]
        try:
            rows = SogouClient(timeout=timeout, delay_seconds=0).search(
                canonical, aliases
            )
            result["sogou_wechat"].update(
                {
                    "status": "ok",
                    "returned": len(rows),
                    "exact_author_matches": sum(
                        1 for item in rows if item["identity_match"]
                    ),
                    "probe_account": canonical,
                }
            )
        except (HTTPError, URLError, TimeoutError, RetrievalError) as exc:
            result["sogou_wechat"].update(
                {
                    "status": "error",
                    "probe_account": canonical,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return result


def read_one(url: str, timeout: int) -> dict[str, Any]:
    client = SogouClient(timeout=timeout, delay_seconds=0)
    if "weixin.sogou.com/link" in url:
        url = client.resolve_result(url, "https://weixin.sogou.com/")
    return client.read_article(url)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Account-level WeChat discovery and article reader"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config", required=True)
    doctor_parser.add_argument("--probe", action="store_true")
    doctor_parser.add_argument("--timeout", type=int, default=30)

    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--config", required=True)
    fetch_parser.add_argument("--output", required=True)
    fetch_parser.add_argument("--run-date", default=date.today().isoformat())
    fetch_parser.add_argument("--hot-days", type=int, default=3)
    fetch_parser.add_argument("--case-days", type=int, default=30)
    fetch_parser.add_argument("--read-limit-per-account", type=int, default=1)
    fetch_parser.add_argument("--account", default="")
    fetch_parser.add_argument("--timeout", type=int, default=30)
    fetch_parser.add_argument("--delay-seconds", type=float, default=1.2)
    fetch_parser.add_argument("--verified-input", default="")

    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("--url", required=True)
    read_parser.add_argument("--output")
    read_parser.add_argument("--timeout", type=int, default=30)

    args = parser.parse_args()
    if args.command == "doctor":
        config = json.loads(
            pathlib.Path(args.config).read_text(encoding="utf-8")
        )
        print(
            json.dumps(
                doctor(config, args.probe, args.timeout),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "fetch":
        config = json.loads(
            pathlib.Path(args.config).read_text(encoding="utf-8")
        )
        payload = retrieve_accounts(
            config,
            date.fromisoformat(args.run_date),
            hot_days=args.hot_days,
            case_days=args.case_days,
            read_limit_per_account=args.read_limit_per_account,
            only_account=args.account,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
            verified_accounts=load_verified_input(
                args.verified_input, date.fromisoformat(args.run_date)
            ),
        )
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved WeChat account radar: {output}")
        return

    payload = read_one(args.url, args.timeout)
    if args.output:
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved WeChat article: {output}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RetrievalError, HTTPError, URLError, TimeoutError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
