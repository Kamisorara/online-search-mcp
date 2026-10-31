"""站点专用通道：对智能体高频访问的站点走官方数据源，更快更干净还能绕反爬。

思路与 URL 模式移植自 mcpsurfr（MIT, nkapila6）的 adapters：
- GitHub 仓库页 → README 原文（GitHub API raw）；/blob/ 文件 → raw.githubusercontent 直读
- PyPI 项目页 → pypi.org 官方 JSON
- Stack Overflow / Stack Exchange 问答 → 官方 API（采纳回答排最前，其次按票数）
- Wikipedia 词条 → REST HTML 后提取正文

入口 try_site_fetch()：URL 命中且通道成功返回 (markdown, via 标注)；
不匹配或通道失败一律返回 None，回落到通用抓取路径——通道故障绝不拖垮整体。
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
from datetime import datetime, timezone

import httpx

from .github_search import _get_token
from .proxy import detect_proxy

logger = logging.getLogger("online_search_mcp.sites")

_TIMEOUT = httpx.Timeout(15.0, connect=8.0)

# --- 路由正则（按专用度排序） ---
_GH_BLOB_RE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/blob/([^?#/][^?#]*)")
_GH_TREE_RE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/tree/[^?#/][^?#]*")
_GH_REPO_RE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:[?#].*)?$")
_PYPI_RE = re.compile(r"^https?://pypi\.org/project/([\w.-]+?)(?:/([\w.!+-]+))?/?$")

# Stack Exchange 系问答站（site 参数直接用域名，API 官方支持）
_SE_HOSTS = (
    "stackoverflow.com",
    "stackexchange.com",
    "superuser.com",
    "askubuntu.com",
    "serverfault.com",
    "mathoverflow.net",
)

# raw 直读仅对文本类文件开放（其余交给通用路径处理二进制提示）
_RAW_TEXT_EXT = (
    ".md", ".markdown", ".mdx", ".txt", ".rst", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".html", ".htm", ".css", ".xml",
    ".rs", ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".php", ".rb",
    ".sh", ".bat", ".ps1", ".sql", ".lua", ".vue", ".svelte", ".swift",
)
_RAW_TEXT_NAMES = {"dockerfile", "makefile", "license", "notice", "cmakelists.txt"}


def try_site_fetch(url: str) -> tuple[str, str] | None:
    """按站点路由抓取。返回 (markdown 正文, via 标注)；未命中/失败返回 None。"""
    try:
        for route in (_route_github, _route_pypi, _route_stackexchange, _route_wikipedia):
            result = route(url)
            if result is not None:
                text, via = result
                if text and text.strip():
                    logger.info("站点通道命中: %s（%s）", url, via)
                    return result
                return None
    except Exception as exc:  # noqa: BLE001 - 通道失败必须静默回落通用路径
        logger.warning("站点通道处理 %s 失败: %s", url, exc)
    return None


# --- GitHub ----------------------------------------------------------------------


def _route_github(url: str) -> tuple[str, str] | None:
    blob = _GH_BLOB_RE.match(url)
    if blob:
        return _github_blob(blob.group(1), blob.group(2), blob.group(3))
    tree = _GH_TREE_RE.match(url)
    if tree:
        return _github_repo(tree.group(1), tree.group(2))
    repo = _GH_REPO_RE.match(url)
    if repo:
        return _github_repo(repo.group(1), repo.group(2))
    return None


def _github_headers(raw: bool = False) -> dict[str, str]:
    headers = {"User-Agent": "online-search-mcp", "Accept": "application/vnd.github+json"}
    if raw:
        headers["Accept"] = "application/vnd.github.raw"  # README 原文而非 base64 JSON
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_repo(owner: str, repo: str) -> tuple[str, str] | None:
    with _client() as client:
        meta_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=_github_headers())
        if meta_resp.status_code == 404:
            return None  # 仓库不存在 → 通用路径给出统一的 404 说明
        meta = meta_resp.json() if meta_resp.status_code == 200 else {}
        readme_resp = client.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme", headers=_github_headers(raw=True)
        )
    if readme_resp.status_code == 404:
        return None  # 空仓库无 README，通用路径更合适
    readme_resp.raise_for_status()

    lines = [f"# {meta.get('full_name') or f'{owner}/{repo}'}"]
    if meta.get("description"):
        lines.append(meta["description"])
    meta_bits = []
    if meta.get("stargazers_count") is not None:
        meta_bits.append(f"⭐{meta['stargazers_count']}")
    if meta.get("language"):
        meta_bits.append(meta["language"])
    if meta.get("license", {}) and meta["license"].get("spdx_id") not in (None, "NOASSERTION"):
        meta_bits.append(meta["license"]["spdx_id"])
    if meta.get("pushed_at"):
        meta_bits.append(f"更新 {str(meta['pushed_at'])[:10]}")
    if meta_bits:
        lines.append(" · ".join(meta_bits))
    lines += ["", "README 原文：", "---", "", readme_resp.text]
    return "\n".join(lines), "GitHub API"


def _github_blob(owner: str, repo: str, rest: str) -> tuple[str, str] | None:
    """blob 链接改抓 raw 直链；rest 形如 'refs/heads/main/path/to/file.py' 或 'main/file.py'。"""
    if "/" not in rest:
        return None
    path = rest.split("/", 1)[1]
    lowered = path.rsplit("/", 1)[-1].lower()
    if not (lowered.endswith(_RAW_TEXT_EXT) or lowered in _RAW_TEXT_NAMES):
        return None  # 非文本文件（图片/压缩包等）→ 通用路径的二进制提示
    with _client() as client:
        resp = client.get(f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if b"\x00" in resp.content[:1024]:
        return None  # 二进制误判兜底
    text = resp.text
    if lowered.endswith((".md", ".markdown", ".mdx")):
        body = text
    else:
        lang = path.rsplit(".", 1)[-1] if "." in lowered else ""
        body = f"```{lang}\n{text}\n```"
    return f"# {owner}/{repo} · {path}\n\n{body}", "GitHub raw 直读"


# --- PyPI ------------------------------------------------------------------------


def _route_pypi(url: str) -> tuple[str, str] | None:
    m = _PYPI_RE.match(url)
    if not m:
        return None
    name, version = m.group(1), m.group(2)
    api = f"https://pypi.org/pypi/{name}/{version + '/' if version else ''}json"
    with _client() as client:
        resp = client.get(api)
    if resp.status_code == 404:
        return None  # 包不存在 → 通用路径 404
    resp.raise_for_status()
    data = resp.json()
    info = data.get("info", {})

    version = info.get("version", "")
    lines = [f"# {info.get('name', name)} {version}".rstrip()]
    if info.get("summary"):
        lines.append(info["summary"])
    author = info.get("author") or info.get("author_email") or ""
    upload = ""
    try:
        files = data.get("releases", {}).get(version) or []
        upload = str(files[0].get("upload_time", ""))[:10]
    except (IndexError, AttributeError):
        pass
    bits = [b for b in (f"作者 {author}" if author else "", f"发布 {upload}" if upload else "") if b]
    if bits:
        lines.append(" · ".join(bits))
    urls = info.get("project_urls") or {}
    for label, key in (("主页", "Homepage"), ("文档", "Documentation"), ("源码", "Source"), ("Bug跟踪", "Tracker")):
        if urls.get(key):
            lines.append(f"- {label}: {urls[key]}")
    if not urls and info.get("home_page"):
        lines.append(f"- 主页: {info['home_page']}")
    top_requires: list[str] = []
    for req in info.get("requires_dist") or []:
        if "; extra ==" in req:  # 可选依赖（extras）不算顶级依赖
            continue
        name = re.split(r"[;<>=~! (\[]", req, 1)[0].strip()
        if name:
            top_requires.append(name)
    if top_requires:
        lines.append(f"- 依赖（顶级）: {', '.join(dict.fromkeys(top_requires))[:300]}")
    return "\n".join(lines), "PyPI 官方 JSON"


# --- Stack Exchange 问答 -----------------------------------------------------------


def _route_stackexchange(url: str) -> tuple[str, str] | None:
    from urllib.parse import urlsplit

    split = urlsplit(url)
    host = (split.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _SE_HOSTS):
        return None
    m = re.match(r"^/questions/(\d+)", split.path or "")
    if not m:
        return None

    qid = m.group(1)
    with _client() as client:
        q_resp = client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}",
            params={"site": host, "filter": "withbody"},
        )
        if q_resp.status_code == 404:
            return None
        q_resp.raise_for_status()
        questions = q_resp.json().get("items") or []
        if not questions:
            return None
        a_resp = client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}/answers",
            params={"site": host, "filter": "withbody", "sort": "votes", "order": "desc", "pagesize": 5},
        )
        answers = a_resp.json().get("items") or [] if a_resp.status_code == 200 else []

    q = questions[0]
    # 阅读顺序：采纳的回答最前，其余按票数（API 已按票序，只需把采纳的提上来）
    answers.sort(key=lambda a: (not a.get("is_accepted"),))
    date = datetime.fromtimestamp(q.get("creation_date", 0), tz=timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# {_html.unescape(q.get('title', ''))}",
        f"提问于 {date} · {q.get('score', 0)} 票 · {q.get('answer_count', len(answers))} 个回答",
        "",
        "## 问题",
        _fragment_to_text(q.get("body", "")),
    ]
    for a in answers:
        kind = "采纳的回答" if a.get("is_accepted") else "回答"
        lines += ["", f"## {kind}（{a.get('score', 0)} 票）", _fragment_to_text(a.get("body", ""))]
    return "\n".join(lines), "Stack Exchange 官方 API"


def _fragment_to_text(fragment: str) -> str:
    """HTML 片段（API 返回的正文）转纯文本，保留代码块的换行。"""
    text = re.sub(r"<(?:br|/p|/li|/pre|/blockquote|/h[1-6]|/tr)[^>]*>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return _html.unescape(text).strip()


# --- Wikipedia --------------------------------------------------------------------


def _route_wikipedia(url: str) -> tuple[str, str] | None:
    from urllib.parse import unquote, urlsplit

    split = urlsplit(url)
    host = (split.hostname or "").lower()
    m = re.match(r"^([a-z-]+)\.(?:m\.)?wikipedia\.org$", host)
    if not m:
        return None
    title_path = (split.path or "").removeprefix("/wiki/")
    if not title_path or ":" in unquote(title_path):
        return None  # Special:/File:/Talk: 等非词条页

    from .fetch import _extract_markdown  # 延迟导入，避免与 fetch.py 循环依赖

    with _client() as client:
        resp = client.get(
            f"https://{m.group(1)}.wikipedia.org/api/rest_v1/page/html/{title_path}",
            headers={"User-Agent": "online-search-mcp (MCP search service)"},
        )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    title = unquote(title_path).replace("_", " ")
    text = _extract_markdown(resp.text, url) or _fragment_to_text(resp.text)
    return f"# {title}\n\n{text}", "Wikipedia REST API"


# --- 公共 --------------------------------------------------------------------------


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, follow_redirects=True, proxy=detect_proxy())


# 确保本模块内日志输出到 stderr，绝不污染 stdout（stdio 协议通道）
if not logger.handlers and logger.level == logging.NOTSET:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
