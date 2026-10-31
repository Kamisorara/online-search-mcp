"""健康自检 CLI：python -m online_search_mcp.doctor [--offline]

命令形态参考 wigolo（AGPL-3.0，仅思路）与 agent-search-mcp（Apache-2.0）的 doctor。
逐项检查环境/代理/浏览器/各网络通道并输出可读报告；单项失败只记录不抛异常，
末尾汇总结论与修复建议。--offline 跳过全部联网检查（快速看配置）。
"""

from __future__ import annotations

import importlib.metadata
import logging
import platform
import sys
import time

_OK, _FAIL, _WARN, _SKIP = "✓", "✗", "⚠", "-"

# 提前导入：browser_fetch 等模块导入时会给自己配置 INFO 级日志，
# 必须先导入完，main() 里的统一压制才能生效
from . import browser_fetch  # noqa: F401,E402
from . import cache, proxy, search as _search  # noqa: F401,E402


def main() -> None:
    offline = "--offline" in sys.argv[1:]
    # doctor 是独立 CLI，允许往 stdout 打印（MCP stdio 服务不走这条路径）；
    # 顺手压掉本包各模块的 INFO 日志（部分模块导入时已显式设级，需逐个覆盖），保持报告整洁
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("online_search_mcp"):
            logging.getLogger(name).setLevel(logging.WARNING)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    from . import __version__

    print(f"online-search-mcp v{__version__} 健康自检" + ("（离线模式，跳过联网检查）" if offline else ""))
    print("=" * 56)
    problems: list[str] = []

    problems += _check_runtime()
    problems += _check_proxy()
    problems += _check_browser()
    if not offline:
        problems += _check_network()
    problems += _check_config()

    print("=" * 56)
    if problems:
        print(f"结论：{_FAIL} {len(problems)} 项需要关注")
        for p in problems:
            print(f"  {_FAIL} {p}")
    else:
        print(f"结论：{_OK} 全部检查通过")


def _row(status: str, name: str, detail: str = "") -> None:
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


def _check_runtime() -> list[str]:
    print("[运行环境]")
    _row(_OK, f"Python {sys.version.split()[0]}（{platform.machine()}）")
    for pkg in ("mcp", "ddgs", "httpx", "trafilatura"):
        try:
            _row(_OK, f"{pkg} {importlib.metadata.version(pkg)}")
        except importlib.metadata.PackageNotFoundError:
            _row(_FAIL, f"{pkg} 未安装")
            return [f"缺依赖 {pkg}：在项目目录执行 uv sync 或 pip install -e ."]
    return []


def _check_proxy() -> list[str]:
    from .proxy import detect_proxy_detail

    proxy, source = detect_proxy_detail()
    print("[代理]")
    if proxy:
        _row(_OK, f"{proxy}", source)
    else:
        _row(_WARN, "直连（无代理）", "大陆网络下 DuckDuckGo 等引擎可能不可用，ddgs 会自动容错；"
              "需要时可打开 Clash 等工具的系统代理开关")
    return []


def _check_browser() -> list[str]:
    from .browser_fetch import browser_available, page_session

    print("[无头浏览器兜底]")
    if not browser_available():
        import os

        if os.environ.get("FETCH_BROWSER", "").strip().lower() in ("off", "0", "false", "no"):
            _row(_SKIP, "已通过 FETCH_BROWSER=off 显式关闭")
            return []
        from .browser_fetch import browser_install_hint

        _row(_WARN, "playwright 未安装", "JS 渲染/反爬兜底不可用（其余功能正常）")
        return ["浏览器兜底未启用：" + browser_install_hint()]
    try:
        started = time.monotonic()
        with page_session(url=None):  # 只开浏览器+页面，不导航，验证 chromium 可启动
            pass
        _row(_OK, "Chromium 可启动", f"{time.monotonic() - started:.1f}s")
        return []
    except Exception as exc:  # noqa: BLE001 - doctor 里任何失败都要转成可读结论
        _row(_FAIL, "Chromium 启动失败", f"{type(exc).__name__}: {str(exc)[:120]}")
        return ["Chromium 启动失败：执行 python -m playwright install chromium 重装内核"]


def _check_network() -> list[str]:
    import httpx

    from .proxy import detect_proxy

    print("[网络通道]（真实小请求探测）")
    problems: list[str] = []
    proxy = detect_proxy()

    # 直连基准：百度国内必达，不通说明本机断网
    if _probe_http("通用 HTTP（百度）", "https://www.baidu.com", proxy):
        problems.append("百度都连不上：本机可能断网，请先检查网络")

    # GitHub API（站点通道 + search_github 依赖）
    try:
        with httpx.Client(timeout=10.0, proxy=proxy) as client:
            resp = client.get("https://api.github.com/rate_limit")
        remaining = resp.json().get("resources", {}).get("search", {}).get("remaining", "?")
        from .github_search import _get_token

        auth = "已带 Token" if _get_token() else "免认证（10 次/分钟）"
        _row(_OK, "GitHub API", f"搜索配额剩余 {remaining}（{auth}）")
    except Exception as exc:  # noqa: BLE001
        _row(_FAIL, "GitHub API", f"{type(exc).__name__}: {str(exc)[:100]}")
        problems.append("GitHub API 不可达：search_github 与 GitHub 站点通道会失败，可能需要代理")

    # PyPI JSON（站点通道依赖）
    try:
        with httpx.Client(timeout=10.0, proxy=proxy) as client:
            resp = client.get("https://pypi.org/pypi/ddgs/json")
        _row(_OK, "PyPI JSON API", f"ddgs 最新版 {resp.json().get('info', {}).get('version', '?')}")
    except Exception as exc:  # noqa: BLE001
        _row(_WARN, "PyPI JSON API", f"{type(exc).__name__}（PyPI 站点通道会回落通用抓取）")

    # ddgs 元搜索（search_web 主通道）
    try:
        from ddgs import DDGS

        started = time.monotonic()
        with DDGS(proxy=proxy, timeout=8) as ddgs:
            results = ddgs.text("hello", max_results=1)
        elapsed = time.monotonic() - started
        _row(_OK, "ddgs 元搜索", f"{elapsed:.1f}s 返回 {len(results)} 条")
    except Exception as exc:  # noqa: BLE001
        _row(_FAIL, "ddgs 元搜索", f"{type(exc).__name__}: {str(exc)[:100]}")
        problems.append("ddgs 搜索失败：将自动回落浏览器搜百度；若也失败请开系统代理后重试")

    return problems


def _probe_http(name: str, url: str, proxy: str | None) -> bool:
    import httpx

    try:
        with httpx.Client(timeout=10.0, proxy=proxy) as client:
            resp = client.head(url, follow_redirects=True)
        status = resp.status_code
        if status < 400:
            _row(_OK, name, f"HTTP {status}")
            return False
        _row(_WARN, name, f"HTTP {status}")
        return False
    except Exception as exc:  # noqa: BLE001
        _row(_FAIL, name, f"{type(exc).__name__}")
        return True


def _check_config() -> list[str]:
    from .cache import _env_int

    print("[服务配置]")
    _row(
        _OK,
        "抓取缓存",
        f"TTL {_env_int('FETCH_CACHE_TTL', 300)}s / 最多 {_env_int('FETCH_CACHE_MAX', 64)} 条（0 为禁用）",
    )
    _row(_OK, "搜索自限流", f"{_env_int('SEARCH_RPM', 6)} 次/分钟（SEARCH_RPM）")
    _row(
        _OK,
        "搜索缓存",
        f"TTL {_env_int('SEARCH_CACHE_TTL', 300)}s / 最多 {_env_int('SEARCH_CACHE_MAX', 32)} 条（0 为禁用）",
    )
    return []


if __name__ == "__main__":
    main()
