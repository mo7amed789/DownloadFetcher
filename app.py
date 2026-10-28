from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DOWNLOAD_DIR = Path.home() / "Downloads"
HOST = "127.0.0.1"
PORT = 8765
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
FILE_EXTS = {
    ".7z", ".apk", ".avi", ".csv", ".deb", ".dmg", ".doc", ".docx", ".exe",
    ".flac", ".gif", ".gz", ".ico", ".iso", ".jpeg", ".jpg", ".json", ".m4a",
    ".mkv", ".mov", ".mp3", ".mp4", ".msi", ".ogg", ".pdf", ".png", ".ppt",
    ".pptx", ".rar", ".rpm", ".svg", ".tar", ".tgz", ".txt", ".wav", ".webm",
    ".webp", ".xls", ".xlsx", ".xml", ".zip",
}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml", "image/bmp"}
VIDEO_TYPES = {"video/mp4", "video/webm", "video/ogg"}
AUDIO_TYPES = {"audio/mpeg", "audio/mp3", "audio/ogg", "audio/wav", "audio/webm", "audio/flac"}
TEXT_TYPES = {
    "text/plain", "text/csv", "text/xml", "application/json",
    "application/xml", "application/javascript",
}
FILTERS = {
    "any": "",
    "windows": "windows official download",
    "pdf": "filetype:pdf",
    "image": "image",
    "video": "video official",
    "installer": "official installer download windows",
}

opener = build_opener()
progress_lock = threading.Lock()
downloads: dict[str, dict[str, Any]] = {}


def http_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
    max_bytes: int | None = None,
) -> tuple[int, dict[str, str], bytes, str]:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    request = Request(url, data=data, headers=hdrs, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
            final_url = response.geturl()
            if max_bytes is None:
                body = response.read()
            else:
                body = response.read(max_bytes)
            return status, resp_headers, body, final_url
    except HTTPError as exc:
        body = exc.read() if exc.fp else b""
        resp_headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        return exc.code, resp_headers, body, url


def safe_filename(name: str) -> str:
    name = unquote(name).split("?")[0].split("#")[0]
    name = os.path.basename(name).strip() or "download"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:180]


def filename_from_headers(url: str, headers: dict[str, str]) -> str:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if match:
        return safe_filename(unquote(match.group(1)))
    match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    if match:
        return safe_filename(match.group(1))
    return safe_filename(urlparse(url).path or "download")


def looks_like_file(url: str) -> bool:
    return Path(urlparse(url).path.lower()).suffix in FILE_EXTS


def looks_like_download(headers: dict[str, str], url: str) -> bool:
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    disposition = headers.get("content-disposition", "")
    return (
        looks_like_file(url)
        or "attachment" in disposition.lower()
        or content_type.startswith(("image/", "video/", "audio/"))
        or content_type in {"application/pdf", "application/octet-stream", "application/zip"}
    )


def content_kind(content_type: str, url: str) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in IMAGE_TYPES or ctype.startswith("image/"):
        return "image"
    if ctype in VIDEO_TYPES or ctype.startswith("video/"):
        return "video"
    if ctype in AUDIO_TYPES or ctype.startswith("audio/"):
        return "audio"
    if ctype in TEXT_TYPES or (ctype.startswith("text/") and ctype != "text/html"):
        return "text"
    if ctype == "application/pdf":
        return "pdf"
    if ctype in {"text/html", "application/xhtml+xml"} or not ctype:
        return "file" if looks_like_file(url) else "html"
    return "file"


def human_size(num: int | None) -> str | None:
    if num is None or num < 0:
        return None
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def unwrap_ddg(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        query = parse_qs(parsed.query)
        if "uddg" in query:
            return unquote(query["uddg"][0])
    return url


def decode_body(body: bytes, headers: dict[str, str]) -> str:
    ctype = headers.get("content-type", "")
    match = re.search(r"charset=([\w-]+)", ctype, re.I)
    encoding = match.group(1) if match else "utf-8"
    return body.decode(encoding, errors="replace")


def strip_tags(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|iframe|object|embed).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"(?s)<[^>]+>", "\n", html)
    text = unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:4500]


def extract_file_links(page_url: str, html: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = unescape(match.group(1).strip())
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = unwrap_ddg(urljoin(page_url, href))
        if absolute in seen or not looks_like_file(absolute):
            continue
        seen.add(absolute)
        label = " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) or filename_from_headers(absolute, {})
        found.append({"url": absolute, "label": label[:120], "name": filename_from_headers(absolute, {})})
        if len(found) >= 24:
            break
    return found


def extract_direct_links(page_url: str, html: str) -> list[dict[str, str]]:
    """Find downloadable URLs embedded in a result page.

    This intentionally stays conservative: only links that look like known
    file types or carry a download hint are returned. The page URL itself is
    handled separately by the caller when it is already a file URL.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<a\b([^>]*?)href=["\']([^"\']+)["\']([^>]*)>(.*?)</a\s*>',
        html,
        re.I | re.S,
    ):
        before, href, after, label_html = match.groups()
        attrs = f"{before} {after}"
        href = unescape(href.strip())
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = unwrap_ddg(urljoin(page_url, href))
        download_hint = re.search(r"\bdownload\b", attrs, re.I) is not None
        if not looks_like_file(absolute) and not download_hint:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        label = " ".join(re.sub(r"<[^>]+>", " ", label_html).split())
        found.append({
            "url": absolute,
            "label": label[:120] or filename_from_headers(absolute, {}),
            "name": filename_from_headers(absolute, {}),
        })
        if len(found) >= 12:
            break

    for match in re.finditer(
        r'<(?:source|video|audio|iframe)\b([^>]*?)(?:href|src)=["\']([^"\']+)["\']([^>]*)/?>',
        html,
        re.I | re.S,
    ):
        before, href, after = match.groups()
        href = unescape(href.strip())
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = unwrap_ddg(urljoin(page_url, href))
        if absolute in seen or not looks_like_file(absolute):
            continue
        seen.add(absolute)
        found.append({
            "url": absolute,
            "label": filename_from_headers(absolute, {}),
            "name": filename_from_headers(absolute, {}),
        })
        if len(found) >= 12:
            break

    # Many modern download pages keep the real URL in JSON or JavaScript
    # instead of a normal anchor. Only keep URLs that look like file assets.
    for match in re.finditer(
        r'(?P<quote>["\'])(?P<url>https?://[^"\'<>\\\s]+)(?P=quote)',
        html,
        re.I,
    ):
        absolute = unwrap_ddg(unescape(match.group("url")))
        if absolute in seen or not looks_like_file(absolute):
            continue
        seen.add(absolute)
        found.append({
            "url": absolute,
            "label": filename_from_headers(absolute, {}),
            "name": filename_from_headers(absolute, {}),
        })
        if len(found) >= 12:
            break
    return found


def verify_direct_link(candidate: dict[str, str]) -> dict[str, str] | None:
    """Follow redirects and reject HTML pages masquerading as downloads."""
    url = candidate["url"]
    try:
        status, headers, _body, final_url = http_request(
            url,
            method="HEAD",
            timeout=12,
            max_bytes=0,
        )
        if status >= 400 or not headers:
            status, headers, _body, final_url = http_request(
                url,
                headers={"Range": "bytes=0-0"},
                timeout=15,
                max_bytes=1,
            )
        if status >= 400 or not looks_like_download(headers, final_url):
            return None
        name = filename_from_headers(final_url, headers)
        length = headers.get("content-length")
        return {
            "url": final_url,
            "label": candidate.get("label") or name,
            "name": name,
            "content_type": (headers.get("content-type") or "unknown").split(";", 1)[0],
            "size": human_size(int(length)) if length and length.isdigit() else "size unknown",
        }
    except (URLError, TimeoutError, OSError):
        return None


def search_tokens(query: str) -> list[str]:
    stop_words = {
        "download", "direct", "link", "file", "for", "the", "and", "with",
        "free", "official", "latest", "please", "get", "find", "what",
        "من", "في", "على", "إلى", "عن", "هذا", "هذه", "كتاب", "تحميل",
    }
    tokens = re.findall(r"[^\W_]+", query.lower(), re.UNICODE)
    return [token for token in tokens if token not in stop_words]


def rank_direct_link(link: dict[str, str], query: str) -> tuple[int, bool]:
    """Score a candidate against the user's search and identify page clutter."""
    tokens = search_tokens(query)
    haystack = " ".join((link.get("name", ""), link.get("label", ""), link.get("url", ""))).lower()
    score = 0
    for token in tokens:
        if token in haystack:
            score += 5
    extension = Path(urlparse(link.get("url", "")).path.lower()).suffix
    if extension and extension.lstrip(".") in tokens:
        score += 8
    noise_terms = ("favicon", "logo", "sprite", "tracking", "analytics", "pixel", "newsletter", "newspaper")
    image_query = any(token in {"image", "photo", "picture", "png", "jpg", "svg", "icon"} for token in tokens)
    image_asset = extension in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico"}
    is_noise = any(term in haystack for term in noise_terms) or (image_asset and not image_query)
    if is_noise:
        score -= 20
    if extension in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico"} and not any(
        token in {"image", "photo", "picture", "png", "jpg", "svg", "icon"} for token in tokens
    ):
        score -= 6
    return score, is_noise


def direct_links_for(url: str, query: str = "") -> list[dict[str, str]]:
    if looks_like_file(url):
        verified = verify_direct_link({"url": url, "label": filename_from_headers(url, {})})
        return [verified] if verified else []
    status, headers, body, final_url = http_request(url, timeout=18, max_bytes=1_500_000)
    if status >= 400 or content_kind(headers.get("content-type", ""), final_url) != "html":
        return []
    candidates = extract_direct_links(final_url, decode_body(body, headers))
    verified: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(verify_direct_link, candidate) for candidate in candidates]
        for job in as_completed(jobs):
            result = job.result()
            if result:
                verified.append(result)
    ranked = [(rank_direct_link(link, query), link) for link in verified]

    def direct_sort_key(item: tuple[tuple[int, bool], dict[str, str]]) -> tuple[Any, ...]:
        (score, _noise), link = item
        name = link.get("name", "").lower()
        numbers = tuple(int(value) for value in re.findall(r"\d+", name))
        return (-score, numbers or (10**9,), name)

    ranked.sort(key=direct_sort_key)
    if query and ranked:
        # Hide obvious page assets when the page contains meaningful matches.
        meaningful = [item for item in ranked if not item[0][1]]
        if meaningful:
            ranked = meaningful
        else:
            return []
    return [link for _score, link in ranked[:12]]


def parse_ddg_html(html: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<a[^>]+class="[^"]*(?:result__a|result-link)[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>', html, re.I | re.S)
    matches = list(pattern.finditer(html))
    if not matches:
        matches = list(re.finditer(r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S))
    for index, match in enumerate(matches):
        url = unwrap_ddg(unescape(match.group(1)))
        if not url.startswith("http") or "duckduckgo.com" in urlparse(url).netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        title = " ".join(re.sub(r"<[^>]+>", " ", unescape(match.group(2))).split()) or url
        snippet = ""
        if index < len(snippets):
            snippet = " ".join(re.sub(r"<[^>]+>", " ", unescape(snippets[index])).split())
        results.append(
            {
                "title": title[:180],
                "url": url,
                "snippet": snippet[:280],
                "thumb": "",
                "kind": "file" if looks_like_file(url) else "page",
            }
        )
        if len(results) >= 14:
            break
    return results


def expand_search_query(query: str) -> str:
    """Add useful intent terms without changing what the user typed."""
    normalized = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    additions: list[str] = []
    if re.search(r"\b(win|windows)\s*(10|11)\b", normalized) or re.search(r"\bwin(10|11)\b", normalized):
        additions.extend(["ISO", "download"])
    if re.search(r"\b(iso|installer|setup|exe|apk|pdf)\b", normalized):
        additions.append("official download")
    return f"{query} {' '.join(dict.fromkeys(additions))}".strip()


def rank_search_result(result: dict[str, str], query: str) -> int:
    tokens = search_tokens(query)
    title = result.get("title", "").lower()
    snippet = result.get("snippet", "").lower()
    url = result.get("url", "").lower()
    score = 0
    for token in tokens:
        if token in title:
            score += 8
        if token in snippet:
            score += 3
        if token in url:
            score += 2
    normalized = re.sub(r"[^a-z0-9]+", " ", query.lower())
    if "windows" in normalized or re.search(r"\bwin\d+\b", normalized):
        if any(term in f"{title} {snippet} {url}" for term in ("windows", "win10", "win11")):
            score += 10
        else:
            score -= 12
    if "iso" in normalized or "windows" in normalized:
        if ".iso" in url or " iso" in f" {title} {snippet}":
            score += 12
    if any(term in title for term in ("favicon", "logo", "template", "wallpaper", "news")):
        score -= 20
    return score


def ai_rank_results(results: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Use Gemini as an optional intent-aware re-ranker.

    Only public result metadata is sent. URLs are never accepted from the
    model; its response can only reorder the already verified search results.
    """
    api_key = os.environ.get("DIRECTFETCH_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key or len(results) < 2:
        return results
    compact = [
        {"id": index, "title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("snippet", "")}
        for index, item in enumerate(results)
    ]
    prompt = (
        "You are a search-result ranking assistant. Rank these existing results for the exact user query. "
        "Prefer results that directly satisfy the requested product, version, language, file type, and download intent. "
        "Reject unrelated assets, ads, logos, mirrors with poor descriptions, and pages that do not match the request. "
        "Return JSON only in the form {\"order\":[ids in best-first order]}. Use each id at most once and never invent ids.\n\n"
        f"USER QUERY: {query}\nRESULTS: {json.dumps(compact, ensure_ascii=False)}"
    )
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=" + quote(api_key, safe="")
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}).encode()
    try:
        status, _headers, body, _final = http_request(
            endpoint,
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=18,
            max_bytes=100_000,
        )
        if status >= 400:
            return results
        response = json.loads(body.decode("utf-8", errors="replace"))
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        ranking = json.loads(text)
        order = ranking.get("order", [])
        by_id = {index: item for index, item in enumerate(results)}
        ordered = [by_id[index] for index in order if isinstance(index, int) and index in by_id]
        ordered_ids = {id(item) for item in ordered}
        ordered.extend(item for item in results if id(item) not in ordered_ids)
        return ordered
    except (KeyError, IndexError, TypeError, ValueError, URLError, TimeoutError, OSError):
        return results


def search_web(query: str, kind: str, page: int = 0) -> list[dict[str, str]]:
    extra = FILTERS.get(kind, "")
    full_query = f"{expand_search_query(query)} {extra}".strip()
    if query.startswith(("http://", "https://")):
        return [{"title": query, "url": query, "snippet": "Direct address you entered.", "thumb": "", "kind": "page"}]

    offset = max(0, page) * 30
    body = urlencode({"q": full_query, "b": "", "s": str(offset)}).encode()
    last_error = None
    for target in (
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ):
        try:
            status, headers, raw, _final = http_request(
                target,
                method="POST",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=25,
            )
            if status >= 400:
                last_error = f"Search HTTP {status}"
                continue
            results = parse_ddg_html(decode_body(raw, headers))
            if results:
                ranked = sorted(results, key=lambda item: rank_search_result(item, query), reverse=True)
                scores = [rank_search_result(item, query) for item in ranked]
                if len(search_tokens(query)) >= 2 and scores and scores[0] >= 8:
                    ranked = [item for item, score in zip(ranked, scores) if score >= max(4, scores[0] - 18)]
                return ai_rank_results(ranked[:10], query)
        except (URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            continue
    if last_error:
        raise RuntimeError(last_error)
    return []


def preview_url(url: str) -> dict[str, Any]:
    status, headers, body, final_url = http_request(url, method="GET", timeout=25, max_bytes=900_000)
    if status >= 400 and not body:
        raise RuntimeError(f"Could not open that page (HTTP {status}).")
    content_type = headers.get("content-type", "")
    length_raw = headers.get("content-length")
    size = int(length_raw) if length_raw and length_raw.isdigit() else len(body)
    name = filename_from_headers(final_url, headers)
    kind = content_kind(content_type, final_url)
    payload: dict[str, Any] = {
        "url": final_url,
        "original_url": url,
        "name": name,
        "content_type": content_type.split(";")[0].strip() or "unknown",
        "size": size,
        "size_label": human_size(size),
        "kind": kind,
        "status": status,
        "text": "",
        "files": [],
        "preview_url": f"/api/media?url={quote(final_url, safe='')}",
    }
    if kind == "html":
        html = decode_body(body, headers)
        payload["text"] = strip_tags(html)
        payload["files"] = extract_file_links(final_url, html)
    elif kind == "text":
        payload["text"] = decode_body(body, headers)[:8000]
    elif kind == "file":
        payload["text"] = "This looks like a downloadable file. Preview is metadata only — download when you are sure this is what you want."
    return payload


def unique_path(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        next_path = directory / f"{stem} ({index}){suffix}"
        if not next_path.exists():
            return next_path
    return directory / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"


def run_download(job_id: str, url: str, suggested: str) -> None:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        with opener.open(request, timeout=60) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            name = filename_from_headers(response.geturl(), headers)
            if "." not in name and "." in suggested:
                name = suggested
            if name in {"download", ""}:
                guessed = mimetypes.guess_extension((headers.get("content-type") or "").split(";")[0].strip())
                name = f"{name}{guessed or ''}"
            path = unique_path(DOWNLOAD_DIR, safe_filename(name))
            total_raw = headers.get("content-length")
            total = int(total_raw) if total_raw and total_raw.isdigit() else None
            received = 0
            with progress_lock:
                downloads[job_id].update(
                    {"status": "downloading", "total": total, "path": str(path), "name": path.name}
                )
            with path.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    with progress_lock:
                        downloads[job_id]["received"] = received
        with progress_lock:
            downloads[job_id].update({"status": "done", "received": received, "path": str(path)})
    except Exception as exc:  # noqa: BLE001
        with progress_lock:
            downloads[job_id].update({"status": "error", "error": str(exc)})


def json_bytes(payload: Any, status: int = 200) -> tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8")


def handle_api(path: str, method: str, body: bytes) -> tuple[int, str, bytes]:
    parsed = urlparse(path)
    route = parsed.path
    if method == "POST" and route == "/api/search":
        data = json.loads(body or b"{}")
        query = str(data.get("query") or "").strip()
        kind = str(data.get("filter") or "any").strip().lower()
        try:
            page = max(0, int(data.get("page") or 0))
        except (TypeError, ValueError):
            page = 0
        if not query:
            return json_bytes({"error": "Type what you want to download."}, 400)
        if kind not in FILTERS:
            kind = "any"
        try:
            results = search_web(query, kind, page)
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "reason", exc)
            return json_bytes({"error": f"Search failed: {type(exc).__name__}: {detail}"}, 502)
        if not results:
            return json_bytes({"error": "No results. Try a more specific name."}, 404)
        return json_bytes({
            "results": results,
            "query": query,
            "page": page,
            "has_more": len(results) >= 5,
            "ai_ranking": bool(os.environ.get("DIRECTFETCH_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")),
        })

    if method == "POST" and route == "/api/preview":
        data = json.loads(body or b"{}")
        url = str(data.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return json_bytes({"error": "That is not a web address I can open."}, 400)
        try:
            return json_bytes(preview_url(url))
        except Exception as exc:  # noqa: BLE001
            return json_bytes({"error": str(exc)}, 502)

    if method == "POST" and route == "/api/direct-links":
        data = json.loads(body or b"{}")
        url = str(data.get("url") or "").strip()
        query = str(data.get("query") or "").strip()
        if not url.startswith(("http://", "https://")):
            return json_bytes({"error": "That is not a web address I can inspect."}, 400)
        try:
            return json_bytes({"url": url, "links": direct_links_for(url, query)})
        except Exception as exc:  # noqa: BLE001
            return json_bytes({"error": str(exc), "links": []}, 502)

    if method == "POST" and route == "/api/download":
        data = json.loads(body or b"{}")
        url = str(data.get("url") or "").strip()
        suggested = safe_filename(str(data.get("name") or "download"))
        if not url.startswith(("http://", "https://")):
            return json_bytes({"error": "Invalid url"}, 400)
        job_id = uuid.uuid4().hex[:12]
        with progress_lock:
            downloads[job_id] = {
                "id": job_id,
                "status": "starting",
                "received": 0,
                "total": None,
                "path": "",
                "error": None,
                "name": suggested,
            }
        threading.Thread(target=run_download, args=(job_id, url, suggested), daemon=True).start()
        return json_bytes({"id": job_id})

    if method == "GET" and route.startswith("/api/download/"):
        job_id = route.rsplit("/", 1)[-1]
        with progress_lock:
            job = downloads.get(job_id)
        if not job:
            return json_bytes({"error": "Unknown download"}, 404)
        return json_bytes(job)

    if method == "GET" and route == "/api/media":
        query = parse_qs(parsed.query)
        url = (query.get("url") or [""])[0]
        if not url.startswith(("http://", "https://")):
            return json_bytes({"error": "Invalid url"}, 400)
        status, headers, raw, _final = http_request(url, timeout=30, max_bytes=12 * 1024 * 1024)
        content_type = headers.get("content-type", "application/octet-stream")
        kind = content_kind(content_type, url)
        if kind not in {"image", "video", "audio", "pdf"}:
            return json_bytes({"error": "No visual preview for this type."}, 415)
        return status, content_type, raw

    return json_bytes({"error": "Not found"}, 404)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            path = "/index.html"
        if path.startswith("/api/"):
            status, content_type, body = handle_api(self.path, "GET", b"")
            self._send(status, content_type, body)
            return
        static_name = path[8:] if path.startswith("/static/") else path.lstrip("/")
        file_path = (STATIC / static_name).resolve()
        if STATIC not in file_path.parents and file_path != STATIC:
            self._send(403, "text/plain", b"Forbidden")
            return
        if not file_path.is_file():
            self._send(404, "text/plain", b"Not found")
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self._send(200, content_type, file_path.read_bytes())

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, content_type, payload = handle_api(self.path, "POST", body)
        self._send(status, content_type, payload)


def open_window(url: str) -> None:
    time.sleep(0.5)
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and Path(path).exists():
            subprocess.Popen([path, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    threading.Thread(target=open_window, args=(url,), daemon=True).start()
    print(f"DirectFetch is running at {url}", flush=True)
    print("Files save to your Downloads folder after you preview them.", flush=True)
    print("Close this window to quit.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
