"""DuckDuckGo 网页搜索封装（基于 ddgs 库）。

- 代理透传：显式读取环境变量传给 ddgs（大陆网络访问 DuckDuckGo 必须走代理）
- 限流重试：简单退避重试，失败后返回对智能体友好的错误说明
- 全局截止时间：ddgs 单次尝试内部会串多个引擎（每个各吃满引擎超时），
  限流期最坏可达两分钟以上，远超 MCP 客户端超时；SEARCH_TIMEOUT（默认 45s）
  给整次搜索兜底，到点必定返回结果或友好错误，绝不挂死到客户端超时
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .cache import TTLCache
from .proxy import detect_proxy

logger = logging.getLogger("online_search_mcp.search")

# 重试配置：最多 3 次，退避 1s / 3s
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (1, 3)

_VALID_TIMELIMITS = {None, "d", "w", "m", "y"}


class SlidingWindowLimiter:
    """滑动窗口自限流：60 秒内最多 N 次搜索，超了快速失败。

    设计移植自 duckduckgo-mcp-server（MIT, Nick Clyde）的 RateLimiter，
    简化为同步版：不做等待排队（避免工具调用长时间阻塞），
    直接告知调用方多少秒后重试。
    """

    def __init__(self, requests_per_minute: int):
        self.rpm = max(1, int(requests_per_minute))
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """通过限流返回 0；被限返回需等待的秒数（不阻塞）。"""
        with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
            if len(self._timestamps) < self.rpm:
                self._timestamps.append(now)
                return 0.0
            return 60.0 - (now - self._timestamps[0])


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


#: 搜索全局限流器（保护匿名引擎配额，防止自杀式触发封锁）
_search_limiter = SlidingWindowLimiter(_env_int("SEARCH_RPM", 6))

#: 单次搜索总时长上限（秒，0 禁用）：必须在 MCP 客户端超时前返回，见模块 docstring
_SEARCH_TIMEOUT = _env_int("SEARCH_TIMEOUT", 45, minimum=0)

#: 搜索结果缓存（同参数重复搜索直接命中，不占限流配额；mcpsurfr 思路）
search_cache = TTLCache(
    ttl_seconds=_env_int("SEARCH_CACHE_TTL", 300), max_entries=_env_int("SEARCH_CACHE_MAX", 32)
)


def get_proxy() -> str | None:
    """探测代理：环境变量 > Windows 系统代理（Clash 等工具的开关）。"""
    return detect_proxy()


def _ddgs_text_once(
    query: str,
    max_results: int,
    timelimit: str | None,
    region: str,
    proxy: str | None,
    engine_timeout: float,
) -> list[dict]:
    """同步执行一次 ddgs 搜索（供工作线程调用）。

    注意：engine_timeout 是单个引擎的上限，ddgs 内部会串行尝试多个引擎，
    单次调用最坏可耗时「引擎数 × engine_timeout」，无法从内部打断——
    所以外层用线程 + join 截止时间来兜底。
    """
    from ddgs import DDGS  # 延迟导入，加快 MCP 启动

    with DDGS(proxy=proxy, timeout=engine_timeout) as ddgs:
        return ddgs.text(
            query,
            region=region,
            safesearch="moderate",
            timelimit=timelimit,
            max_results=max_results,
        )


def _ddgs_attempt_bounded(
    wait_seconds: float,
    **ddgs_kwargs,
) -> tuple[list[dict] | None, bool]:
    """带截止时间地执行一次 ddgs 尝试。

    返回 (结果列表, 是否到点放弃)：到点未返回时放弃等待（守护线程自行结束，
    结果作废），确保调用方总耗时不超过 wait_seconds。
    """
    box: dict = {}

    def _work() -> None:
        try:
            box["items"] = _ddgs_text_once(**ddgs_kwargs)
        except Exception as exc:  # noqa: BLE001 - 异常带回主线程统一处理
            box["error"] = exc

    worker = threading.Thread(target=_work, daemon=True, name="ddgs-attempt")
    worker.start()
    worker.join(wait_seconds)
    if worker.is_alive():
        return None, True
    if "error" in box:
        raise box["error"]
    return box.get("items") or None, False


def web_search(
    query: str,
    max_results: int = 8,
    timelimit: str | None = None,
    region: str = "wt-wt",
    fetch_top: int = 0,
) -> str:
    """执行搜索并返回 Markdown 格式结果列表。

    Args:
        query: 搜索关键词。
        max_results: 返回结果条数上限（1-20）。
        timelimit: 时间范围过滤，None 不限 / "d" 一天 / "w" 一周 / "m" 一月 / "y" 一年。
        region: 地区与语言，如 "wt-wt"（全球/英文偏重）、"cn-zh"（中文）。
        fetch_top: 附带抓取前 N 条（0-3）结果的正文摘要（每条约 800 字，纯 HTTP 并发），
            需要直接答案而非链接清单时开启；默认 0 关闭以保持低延迟。
        """
    max_results = max(1, min(int(max_results), 20))
    fetch_top = max(0, min(int(fetch_top), 3))
    if timelimit not in _VALID_TIMELIMITS:
        timelimit = None

    # ---- 缓存命中：直接返回，不占限流配额（智能体重复搜索很常见） ----
    cache_key = f"{query}|{max_results}|{timelimit}|{region}|{fetch_top}"
    cached = search_cache.get(cache_key)
    if cached is not None:
        return "[搜索缓存命中]\n\n" + cached

    # 自限流保护：超频快速失败，不做长阻塞（工具调用挂在等待里对智能体更糟）
    wait = _search_limiter.acquire()
    if wait > 0:
        return (
            f"搜索请求过于频繁（自限流保护：{_search_limiter.rpm} 次/分钟），请在 {int(wait) + 1} 秒后重试。"
            "频繁硬搜会触发引擎对整个 IP 的封锁。"
        )

    proxy = get_proxy()
    last_error: Exception | None = None
    items: list[dict] | None = None
    via: str | None = None
    deadline_hit = False

    # 全局截止时间：整次搜索（重试链 + 浏览器兜底）必须在 SEARCH_TIMEOUT 内收口，
    # 否则限流期引擎逐个挂起会把调用拖到两分钟以上，MCP 客户端早已判定超时
    deadline = time.monotonic() + _SEARCH_TIMEOUT if _SEARCH_TIMEOUT > 0 else None

    def _remaining() -> float:
        return (deadline - time.monotonic()) if deadline else float("inf")

    for attempt in range(_MAX_ATTEMPTS):
        remaining = _remaining()
        if remaining <= 2:  # 剩余时间不够再试一轮，收口
            deadline_hit = deadline is not None
            break
        # timeout=10：单个引擎上限 10 秒，避免被墙引擎拖慢整体（可用的引擎通常 2-5 秒返回）
        engine_timeout = max(3, min(10, remaining))
        try:
            items, timed_out = _ddgs_attempt_bounded(
                wait_seconds=remaining,
                query=query,
                max_results=max_results,
                timelimit=timelimit,
                region=region,
                proxy=proxy,
                engine_timeout=engine_timeout,
            )
            if timed_out:
                deadline_hit = True
                last_error = TimeoutError(f"ddgs 引擎链 {remaining:.0f}s 内未返回（疑似被限流挂起）")
                logger.warning("搜索第 %d 次到截止时间未返回", attempt + 1)
                break
            if items:
                break
        except Exception as exc:  # noqa: BLE001 - 网络/限流异常种类繁多，统一处理
            last_error = exc
            logger.warning("搜索第 %d 次失败: %s", attempt + 1, exc)
            if attempt < _MAX_ATTEMPTS - 1:
                backoff = min(_BACKOFF_SECONDS[attempt], max(0.0, _remaining()))
                if backoff > 0:
                    time.sleep(backoff)

    if not items:
        # 全引擎失败或零结果 → 浏览器搜百度兜底（百度国内直连，SERP 对真实浏览器稳定）。
        # 兜底自身需要启动/渲染（最多 20s+），剩余时间不足就跳过，保证总时长可控
        if _remaining() >= 15:
            from .browser_search import browser_fallback_search

            fallback = browser_fallback_search(query, max_results)
            if fallback:
                items, via = fallback, "浏览器搜百度兜底"
        elif deadline is not None:
            deadline_hit = True
        if not items:
            if last_error is not None:
                note = (
                    f"（已达单次搜索总时长上限 {_SEARCH_TIMEOUT}s，提前返回——"
                    "引擎大概率正被限流，请稍等 1-2 分钟再试）"
                    if deadline_hit
                    else None
                )
                return _error_message(last_error, proxy, extra=note)  # 失败结果不缓存
            return f"未找到与「{query}」相关的搜索结果，建议更换关键词或调整 timelimit 后重试。"

    formatted = _format_results(query, items, timelimit, proxy, via=via)
    if fetch_top > 0:
        formatted += _speedview(items, fetch_top)
    search_cache.set(cache_key, formatted)
    return formatted


def _speedview(items: list[dict], n: int) -> str:
    """并发抓取前 N 条结果的正文摘要，拼成「正文速览」小节（mcpsurfr passages 思路的轻量版）。"""
    from .fetch import quick_excerpt

    targets = [
        ((it.get("href") or it.get("url") or ""), (it.get("title") or "").strip())
        for it in items[:n]
    ]
    targets = [(u, t) for u, t in targets if u.startswith("http")]
    if not targets:
        return ""
    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
        excerpts = list(pool.map(lambda ut: quick_excerpt(ut[0]), targets))

    parts = [f"\n\n## 前 {len(targets)} 条正文速览（纯 HTTP 并发抓取，各约 800 字）"]
    for (u, t), ex in zip(targets, excerpts):
        parts.append(f"\n### {t or u}\n{u}\n{ex or '（速览抓取失败，可用 fetch_url_content 单独重试）'}")
    return "\n".join(parts)


def _format_results(
    query: str,
    items: list[dict],
    timelimit: str | None,
    proxy: str | None,
    via: str | None = None,
) -> str:
    """把 ddgs 或 Bing 兜底的结果列表格式化为 Markdown。"""
    # ddgs 新旧版本字段名不同：href / url
    lines = []
    for i, item in enumerate(items, 1):
        title = (item.get("title") or "").strip() or "(无标题)"
        url = item.get("href") or item.get("url") or ""
        snippet = (item.get("body") or item.get("excerpt") or "").strip()
        lines.append(f"{i}. [{title}]({url})\n   {snippet}" if url else f"{i}. {title}\n   {snippet}")

    header = f"搜索「{query}」的结果（{len(lines)} 条）"
    if timelimit:
        ranges = {"d": "一天内", "w": "一周内", "m": "一月内", "y": "一年内"}
        header += f"，时间范围: {ranges.get(timelimit, timelimit)}"
    if proxy:
        header += "，已通过代理访问"
    if via:
        header += f"，{via}"
    return header + "\n\n" + "\n".join(lines)


def _error_message(exc: Exception | None, proxy: str | None, extra: str | None = None) -> str:
    """生成对智能体友好的失败说明；extra 追加在末尾的补充提示。"""
    detail = f"（最后错误：{type(exc).__name__}: {exc}）" if exc else ""
    if extra:
        detail += extra
    if proxy:
        return (
            f"搜索失败，已重试 {_MAX_ATTEMPTS} 次{detail}。"
            "当前使用代理仍无法访问 DuckDuckGo，请检查代理是否可用，"
            "或稍后重试（可能被限流）。"
        )
    return (
        f"搜索失败，已重试 {_MAX_ATTEMPTS} 次{detail}。"
        "常见原因：1) 本机网络无法直连 DuckDuckGo（中国大陆网络需设置 "
        "HTTPS_PROXY 环境变量指向可用代理后重启 MCP 服务）；2) 触发了限流，请稍后重试。"
    )


# 确保本模块内日志输出到 stderr，绝不污染 stdout（stdio 协议通道）
if not logger.handlers and logger.level == logging.NOTSET:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
