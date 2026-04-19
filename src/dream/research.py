"""
dream/research.py — Reliable-source retrieval for the Dream loop.

This module adds a lightweight internet research lane that:
  - searches for trusted sources on the open web
  - fetches concise excerpts from those sources
  - distills candidate factual statements for Dream to verify

The search engine is used only for discovery. Dream stores and reasons over
content fetched from trusted domains directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

from dream.config import DreamConfig

logger = logging.getLogger("dream.research")

USER_AGENT = "QwenCode-Dream/0.1 (+https://example.local)"
TRUSTED_SUFFIXES = (".gov", ".edu", ".mil", ".int")
TRUSTED_DOMAINS = {
    "wikipedia.org",
    "britannica.com",
    "arxiv.org",
    "rfc-editor.org",
    "ietf.org",
    "w3.org",
    "developer.mozilla.org",
    "docs.python.org",
    "learn.microsoft.com",
    "postgresql.org",
    "numpy.org",
    "pytorch.org",
    "tensorflow.org",
    "rust-lang.org",
    "go.dev",
    "kubernetes.io",
    "nodejs.org",
    "riscv.org",
    "noaa.gov",
    "weather.gov",
    "climate.gov",
    "nasa.gov",
    "nih.gov",
    "cdc.gov",
    "who.int",
    "ncbi.nlm.nih.gov",
    "medlineplus.gov",
    "federalreserve.gov",
    "fred.stlouisfed.org",
    "sec.gov",
    "treasury.gov",
    "imf.org",
    "worldbank.org",
    "ametsoc.org",
}
NOISE_PHRASES = (
    "from wikipedia",
    "free encyclopedia",
    "click here",
    "read more",
    "javascript is disabled",
)


@dataclass
class ResearchSource:
    title: str
    url: str
    domain: str
    snippet: str
    query: str

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "snippet": self.snippet,
            "query": self.query,
        }


@dataclass
class ResearchPacket:
    query: str
    focus_terms: list[str]
    sources: list[ResearchSource]
    candidate_statements: list[str]

    def evidence_block(self, max_chars: int = 2400) -> str:
        parts: list[str] = []
        total = 0
        for idx, source in enumerate(self.sources, start=1):
            block = (
                f"[{idx}] {source.title} ({source.domain})\n"
                f"URL: {source.url}\n"
                f"Summary: {source.snippet}"
            )
            if total and total + len(block) + 2 > max_chars:
                break
            parts.append(block)
            total += len(block) + 2
        return "\n\n".join(parts)

    def to_memory_payload(self) -> dict:
        return {
            "query": self.query,
            "focus_terms": self.focus_terms,
            "sources": [source.as_dict() for source in self.sources],
            "candidate_statements": list(self.candidate_statements),
        }

    @classmethod
    def from_memory_payload(cls, payload: dict) -> "ResearchPacket":
        return cls(
            query=str(payload.get("query", "")),
            focus_terms=[str(item) for item in payload.get("focus_terms", []) if str(item).strip()],
            sources=[
                ResearchSource(
                    title=str(source.get("title", "Untitled source")),
                    url=str(source.get("url", "")),
                    domain=str(source.get("domain", "")),
                    snippet=str(source.get("snippet", "")),
                    query=str(source.get("query", payload.get("query", ""))),
                )
                for source in payload.get("sources", [])
                if isinstance(source, dict) and source.get("url")
            ],
            candidate_statements=[
                str(item).strip()
                for item in payload.get("candidate_statements", [])
                if str(item).strip()
            ],
        )


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str]] = []
        self._capture_link = False
        self._current_href = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_map = dict(attrs)
        if tag == "a" and "result__a" in (attr_map.get("class") or ""):
            self._capture_link = True
            self._current_href = attr_map.get("href") or ""
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._capture_link:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_link:
            title = _clean_text("".join(self._current_text))
            if title and self._current_href:
                self.results.append((self._current_href, title))
            self._capture_link = False
            self._current_href = ""
            self._current_text = []


class _HTMLExcerptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.paragraphs: list[str] = []
        self._skip_depth = 0
        self._capture_title = False
        self._capture_block = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_map = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = True
            self._buffer = []
            return
        if tag == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.meta_description:
                self.meta_description = _clean_text(attr_map.get("content") or "")
            return
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self._capture_block = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._capture_title or self._capture_block:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title" and self._capture_title:
            self.title = _clean_text("".join(self._buffer))
            self._capture_title = False
            self._buffer = []
            return
        if tag in {"p", "li", "h1", "h2", "h3"} and self._capture_block:
            text = _clean_text("".join(self._buffer))
            if text and len(text) >= 40:
                self.paragraphs.append(text)
            self._capture_block = False
            self._buffer = []


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()
    return cleaned


def _clean_sentence(sentence: str) -> str:
    cleaned = _clean_text(sentence)
    cleaned = cleaned.strip(" -;:,")
    if not cleaned:
        return ""
    if not cleaned.endswith((".", "!", "?")):
        cleaned += "."
    return cleaned


def _decode_duckduckgo_url(url: str) -> str:
    candidate = url
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    uddg = query.get("uddg")
    if uddg:
        return unquote(uddg[0])
    return candidate


def _domain_for(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def _is_trusted_domain(domain: str) -> bool:
    if not domain:
        return False
    if any(domain.endswith(suffix) for suffix in TRUSTED_SUFFIXES):
        return True
    return any(domain == trusted or domain.endswith("." + trusted) for trusted in TRUSTED_DOMAINS)


class DreamResearcher:
    def __init__(self, cfg: DreamConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def build_query(
        topic: str,
        subtopics: list[str],
        weak_areas: list[str],
        reinforcement_focus: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        focus_terms: list[str] = []
        for item in (reinforcement_focus or []) + weak_areas[:3] + subtopics[:3]:
            cleaned = item.strip() if item else ""
            if cleaned and cleaned not in focus_terms:
                focus_terms.append(cleaned)
            if len(focus_terms) >= 3:
                break
        query = topic.strip()
        if focus_terms:
            query += " " + " ".join(focus_terms)
        return query.strip(), focus_terms

    async def collect(
        self,
        topic: str,
        subtopics: list[str],
        weak_areas: list[str],
        reinforcement_focus: list[str] | None = None,
    ) -> ResearchPacket:
        query, focus_terms = self.build_query(topic, subtopics, weak_areas, reinforcement_focus)
        return await self._collect_impl(topic, query, focus_terms)

    async def _collect_impl(self, topic: str, query: str, focus_terms: list[str]) -> ResearchPacket:
        sources: list[ResearchSource] = []
        seen_urls: set[str] = set()

        wikipedia_source = await self._fetch_wikipedia_source(topic, query)
        if wikipedia_source:
            sources.append(wikipedia_source)
            seen_urls.add(wikipedia_source.url)

        discovery_queries = [query]
        if self.cfg.research_max_sources > len(sources):
            discovery_queries.extend(
                [
                    f"{query} site:.gov",
                    f"{query} site:.edu",
                    f"{query} site:britannica.com",
                ]
            )

        for discovery_query in discovery_queries:
            discovered = await self._search_duckduckgo(discovery_query)
            for url, title in discovered:
                if len(sources) >= self.cfg.research_max_sources:
                    break
                if url in seen_urls:
                    continue
                domain = _domain_for(url)
                if sources and domain.endswith("wikipedia.org"):
                    continue
                if not _is_trusted_domain(domain):
                    continue
                source = await self._fetch_page_source(url, title, query)
                if not source:
                    continue
                sources.append(source)
                seen_urls.add(source.url)
            if len(sources) >= self.cfg.research_max_sources:
                break

        candidate_statements = self._distill_candidate_statements(sources)
        logger.info(
            "[research] query=%r | sources=%d | candidate_statements=%d",
            query,
            len(sources),
            len(candidate_statements),
        )
        return ResearchPacket(
            query=query,
            focus_terms=focus_terms,
            sources=sources,
            candidate_statements=candidate_statements,
        )

    async def _fetch_wikipedia_source(self, topic: str, query: str) -> Optional[ResearchSource]:
        return await asyncio.to_thread(self._fetch_wikipedia_source_sync, topic, query)

    def _fetch_wikipedia_source_sync(self, topic: str, query: str) -> Optional[ResearchSource]:
        try:
            search_url = (
                "https://en.wikipedia.org/w/api.php?action=opensearch&limit=1&namespace=0&format=json&search="
                + quote(topic)
            )
            with urlopen(Request(search_url, headers={"User-Agent": USER_AGENT}), timeout=self.cfg.research_timeout_seconds) as resp:
                matches = json.loads(resp.read().decode("utf-8", "ignore"))
            title = ""
            if isinstance(matches, list) and len(matches) >= 2 and matches[1]:
                title = str(matches[1][0]).strip()
            if not title:
                return None

            summary_url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"))
            with urlopen(Request(summary_url, headers={"User-Agent": USER_AGENT}), timeout=self.cfg.research_timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            snippet = _clean_text(data.get("extract") or "")
            page_url = (
                data.get("content_urls", {})
                .get("desktop", {})
                .get("page")
                or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            )
            if not snippet:
                return None
            return ResearchSource(
                title=str(data.get("title") or title),
                url=page_url,
                domain="wikipedia.org",
                snippet=snippet[: self.cfg.research_chars_per_source],
                query=query,
            )
        except Exception as exc:
            logger.debug("[research] wikipedia fetch failed: %s", exc)
            return None

    async def _search_duckduckgo(self, query: str) -> list[tuple[str, str]]:
        return await asyncio.to_thread(self._search_duckduckgo_sync, query)

    def _search_duckduckgo_sync(self, query: str) -> list[tuple[str, str]]:
        try:
            url = "https://duckduckgo.com/html/?q=" + quote(query)
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=self.cfg.research_timeout_seconds) as resp:
                html = resp.read().decode("utf-8", "ignore")
        except Exception as exc:
            logger.warning("[research] search failed for %r: %s", query, exc)
            return []

        parser = _DuckDuckGoResultParser()
        parser.feed(html)

        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href, title in parser.results:
            decoded = _decode_duckduckgo_url(href)
            if not decoded.startswith("http"):
                continue
            if decoded in seen:
                continue
            seen.add(decoded)
            results.append((decoded, title))
            if len(results) >= max(self.cfg.research_max_sources * 3, 8):
                break
        return results

    async def _fetch_page_source(self, url: str, title_hint: str, query: str) -> Optional[ResearchSource]:
        return await asyncio.to_thread(self._fetch_page_source_sync, url, title_hint, query)

    def _fetch_page_source_sync(self, url: str, title_hint: str, query: str) -> Optional[ResearchSource]:
        domain = _domain_for(url)
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=self.cfg.research_timeout_seconds) as resp:
                content_type = resp.headers.get_content_type()
                if "html" not in content_type and "xml" not in content_type:
                    return None
                raw = resp.read(250_000).decode("utf-8", "ignore")
        except Exception as exc:
            logger.debug("[research] source fetch failed for %s: %s", url, exc)
            return None

        parser = _HTMLExcerptParser()
        parser.feed(raw)
        snippet = parser.meta_description or " ".join(parser.paragraphs[:3])
        snippet = _clean_text(snippet)[: self.cfg.research_chars_per_source]
        title = parser.title or _clean_text(title_hint) or domain
        if not snippet:
            return None

        return ResearchSource(
            title=title[:200],
            url=url,
            domain=domain,
            snippet=snippet,
            query=query,
        )

    def _distill_candidate_statements(self, sources: list[ResearchSource]) -> list[str]:
        statements: list[str] = []
        seen: set[str] = set()
        max_statements = self.cfg.research_statement_limit

        for source in sources:
            sentences = re.split(r"(?<=[.!?])\s+", source.snippet)
            for sentence in sentences:
                cleaned = _clean_sentence(sentence)
                lowered = cleaned.lower()
                if len(cleaned) < 40 or len(cleaned) > 260:
                    continue
                if any(phrase in lowered for phrase in NOISE_PHRASES):
                    continue
                norm = lowered.strip()
                if norm in seen:
                    continue
                seen.add(norm)
                statements.append(cleaned)
                if len(statements) >= max_statements:
                    return statements

        return statements
