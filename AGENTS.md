# AGENTS.md — online-search-mcp

本地联网搜索 MCP 服务（Python 3.10+，官方 `mcp` SDK 2.x MCPServer，stdio 传输）。
注册三个工具：`search_web`（DuckDuckGo 元搜索）、`search_github`（GitHub API）、`fetch_url_content`（网页正文 → Markdown）。代码、注释、日志、文档、commit message 均用中文，保持一致。

## 常用命令

```bash
uv sync --extra browser                      # 安装依赖（已配清华 TUNA 镜像；不加 --extra 则无浏览器兜底）
.venv/Scripts/python -X utf8 tests/smoke_test.py   # 端到端冒烟测试：走 stdio 真实调用三个工具（需联网）
.venv/Scripts/python -X utf8 tests/perf_test.py    # 性能基准
.venv/Scripts/python -m online_search_mcp.doctor       # 健康自检（--offline 只看配置不联网）
.venv/Scripts/python -m online_search_mcp              # 手动启动服务；挂住等待 stdio 输入 = 正常
```

- 没有 pytest / lint / typecheck 配置；改动以冒烟测试为准（注意它依赖真实网络）。
- Playwright 内核安装（可选兜底，国内用 npmmirror）：`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright .venv/Scripts/python -m playwright install chromium`。

## 目录与分层

`src/online_search_mcp/` 单层平铺包（src layout）：

- `server.py` — 唯一 MCP 入口，注册工具、写服务级 instructions；只做参数校验和转发
- `search.py` / `github_search.py` — 搜索实现（ddgs 封装 / GitHub REST）
- `fetch.py` — 抓取编排，四级兜底链：`sites.py` 站点专用通道 → 纯 HTTP + trafilatura → llms.txt Markdown 副本 → `browser_fetch.py` 无头浏览器
- `sites.py` — GitHub/PyPI/Stack Exchange/Wikipedia 官方数据源；`try_site_fetch()` 不匹配或失败一律返回 `None` 回落通用路径，**通道故障绝不拖垮整体**
- `browser_fetch.py` / `browser_search.py` — Playwright 兜底；懒加载单例 + 全局锁（sync API 非线程安全）
- `proxy.py` — 代理发现：环境变量（`DDGS_PROXY` > `HTTPS_PROXY` 等）> Windows 注册表系统代理；HTTP 路径每次调用探测，浏览器仅启动时探测
- `cache.py` — 线程安全 TTLCache（LRU），fetch 与 search 缓存共用此实现
- `doctor.py` — 自检 CLI

改工具行为时注意：工具 docstring 和 server 级 instructions 是给调用方智能体看的引导文案（见 README「引导智能体主动使用」），措辞变更属于产品行为变更。README 的工具参数表需同步更新。

## 硬性规则

- **stdout 是 stdio 协议通道，任何代码严禁向 stdout 打印/写日志**；logging 一律走 stderr（`server.py` 已配置，logger 命名 `online_search_mcp.<模块>`）。
- 第三方依赖仅 mcp/ddgs/httpx/trafilatura（+ 可选 playwright），保持精简；同步改 `pyproject.toml` 与 `uv.lock`（跑 `uv sync`）。
- Playwright 未安装时必须优雅降级为纯 HTTP，服务照常可用（`browser_available()` / `browser_install_hint()`）。
- 运行期行为参数走环境变量：`FETCH_BROWSER=off`、`FETCH_BROWSER_TIMEOUT`、`FETCH_CACHE_TTL/MAX`、`SEARCH_RPM`、`SEARCH_TIMEOUT`、`SEARCH_CACHE_TTL/MAX`、`GITHUB_TOKEN`——新增可调项沿用此模式并在 README 环境变量清单登记。
- 所有模块文件头 `from __future__ import annotations`；注释密度与现有风格一致（模块级中文 docstring 说明设计取舍与移植来源）。
- `refs/`（参考项目源码）已 gitignore，不入库、不 import。

## 已知坑

- Windows 优先开发（Git Bash 路径、注册表读系统代理），但代码需跨平台（有 `sys.platform` 判断，别只写 Windows 分支）。
- README 里的绝对路径示例（`D:\Data\...`）是旧机器路径，实际仓库在当前工作区；别照抄进代码。
- `doctor.py` 中「先 import 完自带日志配置的模块、再统一压制」的顺序不能颠倒，否则日志压制失效。
- 大陆网络下 DuckDuckGo/Wikipedia 等需要代理；测试失败先怀疑网络/代理，再怀疑代码。
