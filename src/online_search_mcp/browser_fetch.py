"""无头浏览器抓取兜底：HTTP 抓不到正文时，用 Playwright Chromium 渲染后提取。

- 懒加载单例浏览器，全局锁串行访问（playwright sync API 非线程安全）
- 轻量反检测：真实 Chrome UA、隐藏 webdriver 标记、禁用 AutomationControlled
- 拦截图片/媒体/字体请求，只渲染内容所需的 JS/CSS，加快加载
- 环境变量：FETCH_BROWSER=off 关闭兜底；FETCH_BROWSER_TIMEOUT=秒（默认 20）
"""

from __future__ import annotations

import logging
import os
import sys
import threading

logger = logging.getLogger("online_search_mcp.browser_fetch")

_LOCK = threading.Lock()
_BROWSER = None  # 懒加载单例，成功后常驻复用（省去每次 1-2s 的启动开销）

# 与本机真实 Chrome 一致的 UA（去掉 HeadlessChrome 标记，降低被识别概率）
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def browser_available() -> bool:
    """是否启用浏览器兜底：未显式关闭且 playwright 已安装。"""
    if os.environ.get("FETCH_BROWSER", "").strip().lower() in ("off", "0", "false", "no"):
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def browser_install_hint() -> str:
    return (
        "可启用无头浏览器兜底以读取 JS 渲染页面：在本项目目录执行 "
        '"pip install -e .[browser]"，再执行 "python -m playwright install chromium"。'
    )


def render_page(url: str) -> tuple[str, str] | None:
    """用无头 Chromium 渲染页面，返回 (渲染后HTML, content-type)；失败返回 None。"""
    try:
        with page_session(url=url) as (page, resp):
            resp_headers = resp.headers.get("content-type", "") if resp is not None else ""
            return page.content(), resp_headers
    except Exception as exc:  # noqa: BLE001 - 浏览器路径任何异常都不应上抛
        logger.warning("浏览器渲染失败 %s: %s", url, exc)
        return None


def page_session(url: str | None = None, block_resources: bool = True):
    """在单例浏览器上开一个独立 context+page 的上下文管理器，yield (page, response)。

    playwright sync API 非线程安全，内部持全局锁串行访问；
    其他浏览器任务（如 Bing 搜索兜底）复用此入口共享单例浏览器。
    """
    import contextlib

    timeout_ms = int(os.environ.get("FETCH_BROWSER_TIMEOUT", "20")) * 1000

    @contextlib.contextmanager
    def _ctx():
        with _LOCK:
            browser = _get_browser()
            context = browser.new_context(
                user_agent=_UA,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1280, "height": 800},
            )
            try:
                if block_resources:
                    # 拦截图片/媒体/字体，正文提取用不到，能明显加快渲染
                    context.route(
                        "**/*",
                        lambda route: (
                            route.abort()
                            if route.request.resource_type in _BLOCKED_RESOURCE_TYPES
                            else route.continue_()
                        ),
                    )
                page = context.new_page()
                # 隐藏 navigator.webdriver 标记（最常见的无头检测点）
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                resp = None
                if url:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        # SPA 常驻连接（轮询/长连接）到不了 networkidle，5 秒上限即可
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:  # noqa: BLE001
                        pass
                    page.wait_for_timeout(1500)  # 给前端框架留渲染时间
                yield page, resp
            finally:
                context.close()

    return _ctx()


def _get_browser():
    global _BROWSER
    if _BROWSER is not None and _BROWSER.is_connected():
        return _BROWSER
    from playwright.sync_api import sync_playwright

    from .proxy import detect_proxy

    proxy = detect_proxy()  # 浏览器启动时探测一次（会话期间跟随当时的代理状态）
    pw = sync_playwright().start()
    _BROWSER = pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
        proxy={"server": proxy} if proxy else None,
    )
    _BROWSER._osm_playwright = pw  # 持有引用防止被 GC 导致连接关闭
    logger.info("无头浏览器已启动（常驻复用%s）", f"，代理 {proxy}" if proxy else "")
    return _BROWSER


# 确保本模块内日志输出到 stderr，绝不污染 stdout（stdio 协议通道）
if not logger.handlers and logger.level == logging.NOTSET:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
