"""GitHub 仓库搜索：基于官方 REST API /search/repositories。

- 免认证可用（限 10 次/分钟）；设置 GITHUB_TOKEN 环境变量提升到 30 次/分钟
- 支持 GitHub 搜索限定符（language: / stars: / topic: / pushed: 等）
"""

from __future__ import annotations

import logging
import sys

import httpx

from .proxy import detect_proxy

logger = logging.getLogger("online_search_mcp.github_search")

_API_URL = "https://api.github.com/search/repositories"
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_SORT_OPTIONS = ("best-match", "stars", "forks", "updated")


def search_github(query: str, max_results: int = 8, sort: str = "best-match") -> str:
    """搜索 GitHub 仓库，返回 Markdown 结果列表。

    Args:
        query: 搜索词，支持 GitHub 限定符，如 "web scraper language:python stars:>500"。
        max_results: 返回条数上限（1-20）。
        sort: 排序方式："best-match"（相关度）/ "stars" / "forks" / "updated"。
    """
    max_results = max(1, min(int(max_results), 20))
    sort = sort if sort in _SORT_OPTIONS else "best-match"

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "online-search-mcp",  # GitHub API 强制要求 UA
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    params: dict[str, str | int] = {"q": query, "per_page": max_results}
    if sort != "best-match":
        params["sort"] = sort
        params["order"] = "desc"

    try:
        # httpx 显式传探测到的代理，与抓取/搜索通道行为一致
        with httpx.Client(headers=headers, timeout=_TIMEOUT, proxy=detect_proxy()) as client:
            resp = client.get(_API_URL, params=params)
    except httpx.HTTPError as exc:
        return (
            f"GitHub API 请求失败（{type(exc).__name__}: {exc}）。"
            "可能网络不通或需要代理（服务会自动跟随系统代理，也可设 HTTPS_PROXY）。"
        )

    if resp.status_code in (403, 429) and "rate limit" in resp.text.lower():
        hint = "（当前未认证，限额 10 次/分钟）" if not token else "（已用 Token，限额 30 次/分钟）"
        return f"触发 GitHub 搜索限流{hint}，请稍等一分钟后重试。"
    if resp.status_code == 422:
        return f"搜索语法有误：{query!r}。请检查限定符写法，如 language:python stars:>100 topic:mcp。"
    if resp.status_code != 200:
        return f"GitHub API 返回错误状态 {resp.status_code}：{resp.text[:200]}"

    data = resp.json()
    items = data.get("items", [])
    total = data.get("total_count", len(items))
    if not items:
        return f"GitHub 上未找到匹配「{query}」的仓库，建议简化关键词或调整限定符。"

    lines = []
    for i, item in enumerate(items[:max_results], 1):
        name = item.get("full_name", "")
        url = item.get("html_url", "")
        stars = item.get("stargazers_count", 0)
        lang = item.get("language") or "-"
        pushed = (item.get("pushed_at") or "")[:10]
        desc = (item.get("description") or "").strip()
        meta = f"⭐{stars} · {lang} · 更新 {pushed}"
        line = f"{i}. [{name}]({url}) — {meta}"
        if desc:
            line += f"\n   {desc}"
        lines.append(line)

    header = f"GitHub 仓库搜索「{query}」（{len(items)} 条，共 {total} 个匹配）"
    if sort != "best-match":
        header += f"，按 {sort} 降序"
    return header + "\n\n" + "\n".join(lines)


def _get_token() -> str | None:
    """GitHub Token 可选：提升搜索限额到 30 次/分钟。"""
    import os

    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var)
        if value:
            return value
    return None


# 确保本模块内日志输出到 stderr，绝不污染 stdout（stdio 协议通道）
if not logger.handlers and logger.level == logging.NOTSET:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
