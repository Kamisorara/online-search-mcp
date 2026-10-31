"""搜索引擎浏览器兜底：无头浏览器直接开百度搜索页并解析结果列表。

思路参考 one-search-mcp（MIT, zac_ma）的 agent-browser 本地搜索。
当 ddgs 的全部引擎都不可用/被限流时的最后防线。

为什么选百度而不是 Bing：实测 Bing 对无头浏览器返回 JS 验证页
（li.b_algo 零匹配），而百度 SERP（div#content_left .result）稳定可解析。
百度的结果链接是 baidu.com/link 跳转链，会并发解析成真实 URL 再返回。
"""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import httpx

from .proxy import detect_proxy

logger = logging.getLogger("online_search_mcp.browser_search")

# 百度 SERP 的稳定结构：#content_left 下的 .result / .result-op，标题在 h3>a，摘要在 content-right 类块
_EXTRACT_JS = """
() => Array.from(document.querySelectorAll(
    'div#content_left .result, div#content_left .result-op'
)).slice(0, 15).map(li => ({
    title: li.querySelector('h3')?.innerText?.trim() || '',
    url: li.querySelector('h3 a')?.href || '',
    snippet: (
        li.querySelector("[class*='content-right'], .c-abstract, [class*='abstract']")
        ?.innerText?.trim() || ''
    )
})).filter(r => r.title && r.url)
"""


def browser_fallback_search(query: str, max_results: int = 8) -> list[dict[str, str]]:
    """用无头浏览器搜百度，返回 [{title, url, body}]；失败返回空列表。"""
    from .browser_fetch import browser_available, page_session

    if not browser_available():
        return []
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={max_results}"
    try:
        with page_session(url=url, block_resources=True) as (page, resp):
            if resp is None or resp.status >= 400:
                logger.warning("百度浏览器搜索 HTTP %s", resp.status if resp else "?")
                return []
            items = page.evaluate(_EXTRACT_JS) or []
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "body": r.get("snippet", "")}
                for r in items
                if r.get("url", "").startswith("http")
            ]
    except Exception as exc:  # noqa: BLE001 - 兜底路径任何异常都不应上抛
        logger.warning("百度浏览器搜索失败: %s", exc)
        return []

    results = results[:max_results]
    _resolve_baidu_links(results)  # baidu.com/link 跳转链 → 真实 URL
    logger.info("百度浏览器兜底: %r -> %d 条", query, len(results))
    return results


def _resolve_baidu_links(items: list[dict[str, str]]) -> None:
    """把百度跳转链接原地替换为真实 URL（并发、尽力而为，失败保留跳转链）。"""

    def resolve(item: dict[str, str]) -> None:
        url = item["url"]
        if "baidu.com/link" not in url:
            return
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True, proxy=detect_proxy()) as client:
                item["url"] = str(client.get(url).url)
        except httpx.HTTPError:
            pass  # 保留原跳转链（可正常访问，只是不直观）

    if not any("baidu.com/link" in it["url"] for it in items):
        return
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        list(pool.map(resolve, items))


# 确保本模块内日志输出到 stderr，绝不污染 stdout（stdio 协议通道）
if not logger.handlers and logger.level == logging.NOTSET:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
