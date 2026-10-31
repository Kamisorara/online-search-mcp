"""网页正文抓取：httpx 下载 + trafilatura 提取正文并转 Markdown。

正文拿不到时（页面靠 JS 渲染、被反爬拦截等）自动降级到无头浏览器渲染。
"""

from __future__ import annotations

import logging
import re
import sys

import httpx

from .browser_fetch import browser_available, browser_install_hint, render_page
from .cache import fetch_cache, normalize_url
from .proxy import detect_proxy

logger = logging.getLogger("online_search_mcp.fetch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_TIMEOUT = httpx.Timeout(30.0, connect=15.0)
_MAX_BYTES = 2 * 1024 * 1024  # 响应体上限 2MB
_TEXT_CONTENT_PREFIXES = ("text/", "application/xhtml", "application/xml", "application/json", "application/rss")

_MIN_BODY_CHARS = 500  # HTTP 提取结果少于此长度视为没拿到正文（页脚/空壳），触发浏览器兜底
_NO_FALLBACK_STATUS = {404, 410}  # 这些状态码浏览器也救不了

# 机器人验证页特征（HTTP 200 但页面其实是人机验证，参考 open-webSearch 的 challenge 检测思路）
_CHALLENGE_MARKERS = (
    "verify you're human",
    "unusual traffic",
    "人机验证",
    "百度安全验证",
    "请完成安全验证",
    "access denied",
)


def fetch_url(url: str, max_chars: int = 20000, render_mode: str = "auto") -> str:
    """抓取网页并返回 Markdown 正文。

    Args:
        url: 目标网址，必须是 http/https。
        max_chars: 返回正文的最大字符数（1000-100000），超出部分截断。
        render_mode: "auto"（默认，HTTP 优先失败时浏览器兜底）/ "request"（强制纯 HTTP，最快）/
            "browser"（强制无头浏览器渲染，用于已知 JS 页面，省一次 HTTP 尝试）。
    """
    max_chars = max(1000, min(int(max_chars), 100_000))
    if render_mode not in ("request", "auto", "browser"):
        render_mode = "auto"

    if not url.startswith(("http://", "https://")):
        return f"无效的 URL：{url!r}，仅支持 http/https 链接。"

    # ---- TTL 缓存命中（同一 URL 短时间内重复抓取零成本） ----
    cache_key = normalize_url(url)
    cached = fetch_cache.get(cache_key)
    if cached is not None:
        text, final_url, via = cached
        note = f"[缓存命中，{via}]" + (f" | 最终URL: {final_url}" if final_url != url else "")
        return note + "\n\n" + _finalize(text, max_chars)

    http_text: str | None = None
    final_url = url
    status_code: int | None = None
    failure: str | None = None

    # ---- 快路径：纯 HTTP 抓取（render_mode="browser" 时跳过） ----
    if render_mode != "browser":
        try:
            html, content_type, final_url = _download(url)
            if not content_type.startswith(_TEXT_CONTENT_PREFIXES):
                # 二进制内容浏览器渲染也没有意义，直接拒绝
                return (
                    f"该链接不是网页文本（Content-Type: {content_type}），无法提取正文。"
                    "如需二进制内容请直接下载后处理。"
                )
            http_text = _extract_markdown(html, url)
            # 验证页检测：HTTP 200 但内容是人机验证页 → 视为没拿到正文，交给兜底
            if http_text and _looks_like_challenge(http_text):
                http_text = None
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            failure = f"目标站点返回错误状态 {status_code}"
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # 连接层失败：域名不通/被墙/站点下线，浏览器同样连不上，不做兜底
            return (
                f"无法连接到 {url}（{type(exc).__name__}）。"
                "可能原因：网络不通、需要代理（可设置 HTTPS_PROXY 环境变量）、或目标站点已下线。"
            )
        except httpx.HTTPError as exc:
            failure = f"抓取失败（{type(exc).__name__}: {exc}）"

        if http_text is not None and len(http_text.strip()) >= _MIN_BODY_CHARS:
            fetch_cache.set(cache_key, (http_text, final_url, "HTTP 快路径"))
            return _finalize(http_text, max_chars) + _redirect_note(url, final_url)

    # ---- 慢路径：无头浏览器渲染兜底 ----
    if render_mode != "request" and status_code not in _NO_FALLBACK_STATUS and browser_available():
        rendered = render_page(url)
        if rendered is not None:
            r_html, r_ctype = rendered
            if r_ctype.startswith(_TEXT_CONTENT_PREFIXES) or not r_ctype:
                browser_text = _extract_markdown(r_html, url)
                if browser_text and len(browser_text.strip()) > len((http_text or "").strip()):
                    fetch_cache.set(cache_key, (browser_text, url, "浏览器渲染"))
                    return (
                        "[正文经无头浏览器渲染后提取]\n\n"
                        + _finalize(browser_text, max_chars)
                    )

    # ---- 兜底也没拿到更多，返回现有结果/失败说明 ----
    if http_text is not None:
        # 有短正文总比没有强
        fetch_cache.set(cache_key, (http_text, final_url, "HTTP 快路径"))
        return _finalize(http_text, max_chars) + _redirect_note(url, final_url)

    if failure:
        msg = f"{failure}：{url}"
        if status_code in (401, 403, 429):
            msg += "。该站有反爬/登录墙，无头浏览器也未能通过。"
        if render_mode == "request":
            msg += "。（render_mode=request 已禁用浏览器兜底，可改用 auto 重试）"
        if not browser_available():
            msg += "。" + browser_install_hint()
        return msg
    return f"无法从 {url} 提取正文（页面可能无有效文本内容）。" + (
        "" if browser_available() else browser_install_hint()
    )


def _redirect_note(url: str, final_url: str) -> str:
    """发生重定向时在结尾标注最终 URL，方便智能体核对来源。"""
    if final_url and final_url != url:
        return f"\n\n[已重定向到: {final_url}]"
    return ""


def _looks_like_challenge(text: str) -> bool:
    """检测提取文本是否是人机验证页而非正文。"""
    low = text[:2000].lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _finalize(text: str, max_chars: int) -> str:
    """去除首尾空白并按上限截断。"""
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[内容已截断，原文共 {len(text)} 字符，可调大 max_chars 或抓取具体章节]"
    return text


def _download(url: str) -> tuple[str, str, str]:
    """GET 下载，限制响应体大小，返回 (html文本, content-type, 最终URL)。"""
    # 显式传入探测到的代理（环境变量或 Windows 系统代理），httpx 默认只认环境变量
    with httpx.Client(
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        timeout=_TIMEOUT,
        follow_redirects=True,
        proxy=detect_proxy(),
    ) as client:
        # 流式读取并限制大小，避免超大页面撑爆内存
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            final_url = str(resp.url)
            chunks: list[bytes] = []
            received = 0
            for chunk in resp.iter_bytes(64 * 1024):
                chunks.append(chunk)
                received += len(chunk)
                if received >= _MAX_BYTES:
                    logger.warning("响应超过 %d 字节，截断: %s", _MAX_BYTES, url)
                    break
            content = b"".join(chunks)
            content_type = resp.headers.get("content-type", "text/html").split(";")[0].strip().lower()

    # 编码探测：取响应头/页面 meta 里声明的 charset，未声明则 utf-8 容错解码
    m = re.search(rb"""charset=["']?([\w-]+)""", content[:4096], re.IGNORECASE)
    encoding = (m.group(1).decode("ascii", errors="ignore") if m else "utf-8") or "utf-8"
    if encoding.lower() in ("gb2312", "gbk"):
        encoding = "gb18030"  # 用超集编码，避免生僻字解码失败
    return content.decode(encoding, errors="replace"), content_type, final_url


def _extract_markdown(html: str, url: str) -> str | None:
    """用 trafilatura 提取正文转 Markdown，失败时降级为纯文本。"""
    import trafilatura  # 延迟导入，加快 MCP 启动

    try:
        extracted = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            url=url,
        )
        if extracted and extracted.strip():
            return extracted
    except Exception as exc:  # noqa: BLE001
        logger.warning("Markdown 提取失败，降级纯文本: %s", exc)

    try:
        plain = trafilatura.extract(html, output_format="txt", url=url)
        if plain and plain.strip():
            return plain
    except Exception as exc:  # noqa: BLE001
        logger.warning("纯文本提取也失败: %s", exc)
    return None


# 确保本模块内日志输出到 stderr，绝不污染 stdout（stdio 协议通道）
if not logger.handlers and logger.level == logging.NOTSET:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
