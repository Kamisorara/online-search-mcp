"""内存 TTL 缓存（LRU 淘汰）。

设计移植自 duckduckgo-mcp-server（MIT, Nick Clyde）的 TTLCache：
key -> (过期时间戳, 值)，插入序即 LRU 序，set 时顺手清理过期项。

用于 fetch_url_content：同一 URL 在 TTL 内重复抓取直接命中缓存，
分页式多次读取（不同 max_chars）复用同一次下载与解析。

环境变量：FETCH_CACHE_TTL=秒（默认 300，0 禁用）、FETCH_CACHE_MAX=条数（默认 64，0 禁用）。
"""

from __future__ import annotations

import os
import threading
import time


class TTLCache:
    """线程安全的内存 TTL 缓存，LRU 淘汰，无第三方依赖。"""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 64):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(0, int(max_entries))
        self._store: dict = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0 and self.max_entries > 0

    def get(self, key: str):
        """命中返回值并标记为最近使用；未命中/过期返回 None。"""
        if not self.enabled:
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                self._store.pop(key, None)
                return None
            self._store.pop(key)
            self._store[key] = entry  # 移到末尾 = 最近使用
            return value

    def set(self, key: str, value) -> None:
        if not self.enabled:
            return
        with self._lock:
            now = time.monotonic()
            expired = [k for k, (exp, _) in self._store.items() if now >= exp]
            for k in expired:
                self._store.pop(k, None)
            if key in self._store:
                self._store.pop(key)
            elif len(self._store) >= self.max_entries:
                self._store.pop(next(iter(self._store)))  # 淘汰最久未用
            self._store[key] = (now + self.ttl_seconds, value)


def normalize_url(url: str) -> str:
    """URL 规范化为缓存键：去 fragment、主机小写、去默认端口。"""
    import urllib.parse

    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


#: fetch_url_content 的全局缓存实例
fetch_cache = TTLCache(ttl_seconds=_env_int("FETCH_CACHE_TTL", 300), max_entries=_env_int("FETCH_CACHE_MAX", 64))
