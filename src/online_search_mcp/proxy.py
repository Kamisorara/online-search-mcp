"""代理发现：环境变量优先，其次自动跟随 Windows 系统代理（注册表）。

语义与浏览器一致：
- 用户开了系统代理（Clash/V2Ray 等设置的 ProxyEnable=1）→ 自动走代理
- 系统代理关闭 → 直连（配合 ddgs 多引擎容错）
- DDGS_PROXY / HTTPS_PROXY 等环境变量可强制指定，优先级最高
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("online_search_mcp.proxy")

_ENV_VARS = (
    "DDGS_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


def detect_proxy() -> str | None:
    """返回代理 URL（如 http://127.0.0.1:7897）；无代理返回 None。"""
    proxy, _ = detect_proxy_detail()
    return proxy


def detect_proxy_detail() -> tuple[str | None, str]:
    """返回 (代理URL, 来源说明)，doctor 自检用来展示代理是从哪来的。"""
    for var in _ENV_VARS:
        value = os.environ.get(var)
        if value:
            logger.info("使用环境变量 %s 指定的代理", var)
            return value, f"环境变量 {var}"
    system_proxy = _windows_system_proxy()
    if system_proxy:
        return system_proxy, "Windows 系统代理（注册表）"
    return None, "未检测到代理（直连）"


def _windows_system_proxy() -> str | None:
    """读取 Windows 用户级系统代理（IE/Edge 设置，Clash 等工具的「系统代理」开关写的正是这里）。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enable:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    proxy = _normalize(server)
    if proxy:
        logger.info("自动跟随 Windows 系统代理: %s", proxy)
    return proxy


def _normalize(server: str) -> str | None:
    """把注册表里的 ProxyServer 各种写法统一成代理 URL。

    支持格式：'127.0.0.1:7897'、'http=...;https=...'（按协议分设）。
    """
    server = (server or "").strip()
    if not server:
        return None
    if "=" in server:  # 按协议分设格式，优先 https，其次 http/socks
        parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
        for scheme in ("https", "http", "socks"):
            if parts.get(scheme):
                server = parts[scheme]
                break
        else:
            return None
    if "://" not in server:
        server = "http://" + server
    return server
