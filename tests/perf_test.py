"""性能基准：搜索与网页抓取的延迟测量（真实联网，结果受网络波动影响）。

用法:
    .venv/Scripts/python -X utf8 tests/perf_test.py
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from online_search_mcp.fetch import fetch_url
from online_search_mcp.search import web_search

SEARCH_CASES = [
    ("搜索-中文基础(5条)", dict(query="人工智能 最新进展", max_results=5)),
    ("搜索-英文基础(8条)", dict(query="rust vs zig performance", max_results=8)),
    ("搜索-时效一周(5条)", dict(query="AI 芯片 新闻", max_results=5, timelimit="w")),
    ("搜索-同query重复", dict(query="人工智能 最新进展", max_results=5)),
]

FETCH_CASES = [
    ("抓取-快路径(博客园)", "https://www.cnblogs.com/ljbguanli/p/19498729"),
    ("抓取-快路径(Python文档)", "https://docs.python.org/3/whatsnew/3.14.html"),
    ("抓取-兜底(Redlib镜像)", "https://redlib.catsarch.com/r/RimWorld/"),
    ("抓取-兜底(百度首页)", "https://www.baidu.com"),
]


def bench_search() -> list[tuple[str, float, str]]:
    rows = []
    for label, kwargs in SEARCH_CASES:
        t0 = time.perf_counter()
        try:
            result = web_search(**kwargs)
            elapsed = time.perf_counter() - t0
            ok = not result.startswith("搜索失败")
            n = result.split("（")[1].split(" 条")[0] if ok and "（" in result else "?"
            status = f"{n}条" if ok else "失败(限流或网络)"
        except Exception as exc:  # noqa: BLE001
            elapsed, status = time.perf_counter() - t0, f"异常: {exc}"
        rows.append((label, elapsed, status))
        time.sleep(1.5)  # 间隔一下，别自己把自己刷限流
    return rows


def bench_fetch() -> list[tuple[str, float, str]]:
    rows = []
    for label, url in FETCH_CASES:
        t0 = time.perf_counter()
        try:
            result = fetch_url(url, max_chars=3000)
            elapsed = time.perf_counter() - t0
            if result.startswith("[正文经无头浏览器"):
                path = "浏览器兜底"
                body = len(result) - 20
            elif result.startswith(("无法", "抓取失败", "目标站点", "该链接", "无效")):
                path, body = "失败", len(result)
            else:
                path, body = "HTTP快路径", len(result)
            status = f"{path} {body}字符"
        except Exception as exc:  # noqa: BLE001
            elapsed, status = time.perf_counter() - t0, f"异常: {exc}"
        rows.append((label, elapsed, status))
    return rows


async def bench_mcp_cold_start() -> tuple[float, float]:
    """MCP 冷启动：拉起服务进程 + initialize + tools/list 的耗时。"""
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    python_exe = str(Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe")
    server = StdioServerParameters(command=python_exe, args=["-m", "online_search_mcp"])

    t0 = time.perf_counter()
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            t_init = time.perf_counter() - t0
            await session.list_tools()
            return t_init, time.perf_counter() - t0


def report(title: str, rows: list[tuple[str, float, str]]) -> None:
    print(f"\n## {title}")
    print(f"{'场景':<28}{'耗时':>10}   结果")
    print("-" * 60)
    for label, elapsed, status in rows:
        print(f"{label:<26}{elapsed:>8.2f}s   {status}")


def main() -> None:
    print("=" * 60)
    print("联网搜索 MCP 性能基准（真实网络，含跨境延迟与被墙引擎超时）")
    print("=" * 60)

    # 冷启动测量必须放在最前：playwright sync API 会在主线程留下运行中的事件循环，
    # 之后的 asyncio.run() 会报错（仅影响本测试进程，MCP 服务本身不受影响）
    t_init, t_total = asyncio.run(bench_mcp_cold_start())
    print("\n## MCP 冷启动（进程拉起→initialize→tools/list）")
    print(f"{'启动+握手':<26}{t_init:>8.2f}s")
    print(f"{'+工具列表':<26}{t_total:>8.2f}s")

    report("搜索（ddgs 多引擎聚合）", bench_search())
    report("网页抓取（两级策略）", bench_fetch())

    print("\n完成。")


if __name__ == "__main__":
    main()
