"""端到端冒烟测试：通过 stdio 启动 MCP 服务，真实调用两个工具。

用法：
    .venv/Scripts/python tests/smoke_test.py
    .venv/Scripts/python tests/smoke_test.py --no-proxy-test
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"

SEARCH_QUERY = "Python 3.14 新特性"
FETCH_URL = "https://www.baidu.com"


async def run_smoke_test() -> int:
    server = StdioServerParameters(
        command=str(PYTHON_EXE),
        args=["-m", "online_search_mcp"],
        cwd=str(PROJECT_DIR),
    )
    failures: list[str] = []

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("== 1. initialize OK ==")

            # ---- tools/list ----
            tools = (await session.list_tools()).tools
            names = sorted(t.name for t in tools)
            print(f"== 2. tools/list: {names} ==")
            for expected in ("search_web", "fetch_url_content"):
                if expected not in names:
                    failures.append(f"缺少工具: {expected}")

            # ---- search_web ----
            print(f"== 3. search_web: {SEARCH_QUERY!r} ==")
            result = await session.call_tool(
                "search_web", {"query": SEARCH_QUERY, "max_results": 5, "timelimit": "y"}
            )
            text = _result_text(result)
            print(text[:1500])
            if result.is_error or "失败" in text[:80]:
                failures.append(f"search_web 未返回有效结果: {text[:200]}")

            # ---- 3b. 搜索缓存：同参数第二次调用应命中 ----
            result = await session.call_tool(
                "search_web", {"query": SEARCH_QUERY, "max_results": 5, "timelimit": "y"}
            )
            text = _result_text(result)
            if "搜索缓存命中" not in text:
                failures.append("search_web 第二次同参调用未命中搜索缓存")
            else:
                print("== 3b. 搜索缓存命中 OK ==")

            # ---- 3c. fetch_top 正文速览 ----
            result = await session.call_tool(
                "search_web", {"query": "python httpx tutorial", "max_results": 3, "fetch_top": 2}
            )
            text = _result_text(result)
            if result.is_error or "正文速览" not in text:
                failures.append(f"fetch_top=2 未返回正文速览: {text[:200]}")
            else:
                print("== 3c. fetch_top 正文速览 OK ==")

            # ---- search_github ----
            github_query = "mcp server language:python stars:>1000"
            print(f"\n== 4. search_github: {github_query!r} ==")
            result = await session.call_tool(
                "search_github", {"query": github_query, "max_results": 5, "sort": "stars"}
            )
            text = _result_text(result)
            print(text[:800])
            if result.is_error or "未找到" in text[:60]:
                failures.append(f"search_github 未返回有效结果: {text[:200]}")

            # ---- fetch_url_content ----
            print(f"\n== 5. fetch_url_content: {FETCH_URL} ==")
            result = await session.call_tool(
                "fetch_url_content", {"url": FETCH_URL, "max_chars": 3000}
            )
            text = _result_text(result)
            print(text[:800])
            if result.is_error:
                failures.append(f"fetch_url_content 出错: {text[:200]}")

            # ---- 5b/5c/5d. 站点专用通道：PyPI / GitHub / Stack Overflow ----
            for label, site_url, expect in (
                ("PyPI", "https://pypi.org/project/httpx/", "PyPI 官方 JSON"),
                ("GitHub", "https://github.com/encode/httpx", "GitHub API"),
                (
                    "StackOverflow",
                    "https://stackoverflow.com/questions/231767/what-does-the-yield-keyword-do-in-python",
                    "Stack Exchange 官方 API",
                ),
            ):
                result = await session.call_tool(
                    "fetch_url_content", {"url": site_url, "max_chars": 2000}
                )
                text = _result_text(result)
                if result.is_error or expect not in text[:100]:
                    failures.append(f"{label} 站点通道未生效（缺「{expect}」标注）: {text[:150]}")
                else:
                    print(f"== 5x. {label} 站点通道 OK ==")

        # ---- 6. doctor 健康自检（离线模式，子进程跑） ----
        import subprocess

        proc = subprocess.run(
            [str(PYTHON_EXE), "-m", "online_search_mcp.doctor", "--offline"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_DIR),
            timeout=120,
        )
        if proc.returncode != 0 or "全部检查通过" not in (proc.stdout or ""):
            failures.append(f"doctor --offline 异常: {proc.stdout[-300:] if proc.stdout else proc.stderr[-300:]}")
        else:
            print("== 6. doctor --offline OK ==")

    print("\n" + "=" * 50)
    if failures:
        print("冒烟测试未通过:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("冒烟测试全部通过 ✔")
    return 0


def _result_text(result) -> str:
    return "\n".join(
        c.text for c in result.content if hasattr(c, "text")
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(run_smoke_test()))
