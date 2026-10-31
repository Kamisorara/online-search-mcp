"""MCP 服务入口：注册 search_web / fetch_url_content 两个工具，stdio 传输。"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from .fetch import fetch_url
from .github_search import search_github as _github_search_impl
from .search import web_search

# 日志走 stderr（stdout 是 stdio 协议通道，绝不能写入）
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# ddgs/primp/httpx 的 INFO 日志（引擎失败重试、每个 HTTP 响应）过于嘈杂，压到 WARNING
logging.getLogger("ddgs").setLevel(logging.WARNING)
logging.getLogger("primp").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("online_search_mcp.server")

mcp = MCPServer(
    name="online-search",
    instructions=(
        "联网搜索服务：通用检索用 search_web；找开源项目/示例代码用 search_github"
        "（支持 language:/stars:/topic: 等限定符）；"
        "再用 fetch_url_content 读取具体网页或仓库 README 的正文。"
        "搜索结果里的摘要不完整时务必抓取原文。"
    ),
)


@mcp.tool()
def search_web(
    query: str,
    max_results: int = 8,
    timelimit: str | None = None,
    region: str = "wt-wt",
) -> str:
    """联网搜索（DuckDuckGo），返回带链接的结果列表。

    Args:
        query: 搜索关键词，支持中英文。
        max_results: 返回条数上限，默认 8（1-20）。
        timelimit: 时效性过滤：None 不限 / "d" 一天 / "w" 一周 / "m" 一月 / "y" 一年。新闻类查询建议 "w" 或 "d"。
        region: 地区语言偏好："wt-wt" 全球 / "cn-zh" 中文 / "us-en" 美国英文。
    """
    logger.info("search_web: %r", query)
    return web_search(query, max_results=max_results, timelimit=timelimit, region=region)


@mcp.tool()
def fetch_url_content(
    url: str,
    max_chars: int = 20000,
    render_mode: str = "auto",
) -> str:
    """抓取网页正文并转为 Markdown（自动去导航/广告，处理中文编码）。

    5 分钟内重复抓取同一 URL 直接命中缓存。发生重定向时结尾标注最终 URL。

    Args:
        url: 目标网址（http/https）。
        max_chars: 返回正文最大字符数，默认 20000，超出截断并标注。
        render_mode: "auto"（默认，HTTP 优先、失败时无头浏览器兜底）/
            "request"（强制纯 HTTP，最快，不启动浏览器）/
            "browser"（强制无头浏览器渲染，已知是 JS 页面时用，省一次无效 HTTP）。
    """
    logger.info("fetch_url: %r (render=%s)", url, render_mode)
    return fetch_url(url, max_chars=max_chars, render_mode=render_mode)


@mcp.tool()
def search_github(query: str, max_results: int = 8, sort: str = "best-match") -> str:
    """搜索 GitHub 仓库（官方 API，找开源项目/案例/轮子用这个）。

    支持限定符组合，示例：
        - "mcp server language:python stars:>1000"（Python 高星 MCP 服务）
        - "rimworld mod topic:rimworld-mod"（按主题）
        - "web crawler pushed:>2025-01-01 language:go"（近期活跃的 Go 爬虫）
    常用限定符：language: / stars:>N / topic: / pushed:>YYYY-MM-DD / license: / in:name,description

    Args:
        query: 搜索词，可含上面的限定符。
        max_results: 返回条数上限，默认 8（1-20）。
        sort: "best-match"（相关度）/ "stars" / "forks" / "updated"。
    """
    logger.info("search_github: %r", query)
    return _github_search_impl(query, max_results=max_results, sort=sort)


def main() -> None:
    """MCP stdio 服务入口。"""
    logger.info("online-search-mcp 启动（stdio）")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
