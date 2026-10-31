# online-search-mcp

本地联网搜索 MCP（Model Context Protocol）服务，供智能体使用：

- **`search_web`** — DuckDuckGo 联网搜索（免费、无需 API Key），支持时效性过滤和地区偏好
- **`search_github`** — GitHub 仓库搜索（官方 API），支持 `language:` / `stars:` / `topic:` / `pushed:` 等限定符，找开源项目和案例用
- **`fetch_url_content`** — 抓取网页正文并转为 Markdown（自动去导航/广告、处理中文编码）；JS 渲染/反爬页面自动降级到无头浏览器渲染

基于 Python + 官方 `mcp` SDK 2.x（MCPServer），stdio 传输，兼容所有标准 MCP 客户端。

## 安装

### 方式一：uv（推荐，已配好国内镜像）

镜像源直接写在 `pyproject.toml` 里（清华 TUNA，`[[tool.uv.index]]`），并禁用了 uv 从 GitHub 下载托管 Python（国内会超时，自动改用系统 Python），克隆即用：

```bash
cd D:\Data\Code\Projects\online_search_mcp
uv venv --python 3.14
uv sync --extra browser   # 不需要浏览器兜底则去掉 --extra browser
```

日常改动依赖后执行 `uv sync --extra browser` 即可，锁定文件 `uv.lock` 保证环境可复现。

### 方式二：pip

```bash
cd D:\Data\Code\Projects\online_search_mcp
python -m venv .venv
.venv\Scripts\pip install -e ".[browser]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证服务能启动（会挂住等待 stdio 输入，Ctrl+C 退出即为正常）：

```bash
.venv\Scripts\python -m online_search_mcp
```

### 无头浏览器兜底（推荐）

默认安装只能读服务端渲染的页面（「查看网页源代码」里有正文的）。启用 Playwright 兜底后，JS 渲染的 SPA、部分反爬站点也能读：

```bash
# 已含在上面的 uv sync --extra browser / pip install ".[browser]" 中，还需下载 Chromium 内核（约 120MB，国内用 npmmirror 镜像）
PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright .venv\Scripts\python -m playwright install chromium
```

未安装 playwright 时服务照常工作（纯 HTTP），失败信息里会提示启用方法。

## 代理配置（重要：自动跟随系统代理）

服务**自动跟随 Windows 系统代理**，语义与浏览器一致，零配置：

- **打开 Clash/V2Ray 等工具的「系统代理」开关** → 搜索和抓取自动走代理（所有引擎可达，搜索稳定在 3-6s）
- **关闭系统代理** → 直连（ddgs 多引擎容错，yahoo/mojeek 等可用引擎兜着，3-22s 波动）
- 环境变量可强制指定（优先级高于系统代理）：`DDGS_PROXY`（仅搜索）> `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`；socks5 写 `socks5://127.0.0.1:1080`

> 无头浏览器在**启动时**探测一次代理（会话期间跟随），HTTP 抓取和搜索则是**每次调用时**探测——中途开关系统代理，搜索和快路径抓取立即生效。

实测对比（同 query、8 条结果）：直连 14.6s / 22.8s，走代理 5.7s / 3.1s。

## 接入 ZCode

编辑 `C:\Users\<你>\.zcode\cli\config.json`，在 `mcp.servers` 下添加（用户级，所有项目可用）：

```json
{
  "mcp": {
    "servers": {
      "online-search": {
        "command": "D:\\Data\\Code\\Projects\\online_search_mcp\\.venv\\Scripts\\python.exe",
        "args": ["-m", "online_search_mcp"],
        "env": {
          "HTTPS_PROXY": "http://127.0.0.1:7890"
        }
      }
    }
  }
}
```

> 不需要代理就把 `env` 整段删掉。改完重启 ZCode 会话，在 Settings → MCP 里确认 `online-search` 已连接，工具名为 `mcp__online-search__search_web` 和 `mcp__online-search__fetch_url_content`。

只想在某个项目里启用的话，把同样的 `online-search` 条目写进该项目的 `<repo>\.zcode\config.json` 即可。

## 接入其他 MCP 客户端（Claude Desktop / Cherry Studio / Cursor 等）

标准 `mcpServers` 格式：

```json
{
  "mcpServers": {
    "online-search": {
      "command": "D:\\Data\\Code\\Projects\\online_search_mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "online_search_mcp"],
      "env": {
        "HTTPS_PROXY": "http://127.0.0.1:7890"
      }
    }
  }
}
```

## 工具说明

| 工具 | 参数 | 说明 |
|---|---|---|
| `search_web` | `query`（必填）、`max_results=8`、`timelimit=None`（d/w/m/y）、`region="wt-wt"`（cn-zh 中文） | 返回带链接的 Markdown 结果列表；被限流/断网时返回友好错误说明而非崩溃 |
| `search_github` | `query`（必填，支持限定符）、`max_results=8`、`sort="best-match"`（stars/forks/updated） | 官方 API 免认证限 10 次/分钟；设 `GITHUB_TOKEN` 环境变量提升到 30 次/分钟 |
| `fetch_url_content` | `url`（必填）、`max_chars=20000` | trafilatura 提取正文转 Markdown，超长截断并标注 |

`fetch_url_content` 的抓取策略（两级 + 缓存）：

1. **快路径**：纯 HTTP 抓取（0.1-1s），绝大多数网页走这条
2. **兜底**：HTTP 拿不到足够正文（正文 <500 字、被 403/429/5xx 拦截、或检测到人机验证页）时，自动用无头 Chromium 渲染后再提取（约 3-8s），结果带 `[正文经无头浏览器渲染后提取]` 标记
3. **缓存**：同一 URL 5 分钟内重复抓取直接命中缓存（快数百倍），结果带 `[缓存命中]` 标记；发生重定向时结尾标注最终 URL

`render_mode` 参数可显式控制：`auto`（默认）/ `request`（强制纯 HTTP，最快）/ `browser`（强制渲染，已知 JS 页面用）。

`search_web` 的三层防线：ddgs 多引擎聚合（失败重试 3 次）→ 全部失败时**无头浏览器搜百度兜底** → 仍失败返回友好错误。同时内置**自限流**（默认 6 次/分钟，`SEARCH_RPM` 可调），防止频繁搜索触发引擎对整个 IP 的封锁。

相关环境变量：`FETCH_BROWSER=off` 关闭浏览器兜底；`FETCH_BROWSER_TIMEOUT=秒`（默认 20）；`FETCH_CACHE_TTL=秒` / `FETCH_CACHE_MAX=条`（默认 300/64，设 0 禁用）；`SEARCH_RPM=次/分钟`（默认 6）。

## 开发

```bash
# 端到端冒烟测试（走 stdio 真实调用三个工具）
.venv\Scripts\python -X utf8 tests\smoke_test.py
# 性能基准（搜索/抓取/冷启动延迟测量）
.venv\Scripts\python -X utf8 tests\perf_test.py
```

### 致谢与参考实现

部分设计移植自以下开源项目（许可证均宽松，源码在 `refs/` 供查阅，不入库）：

- [duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server)（MIT）— TTL 缓存、滑动窗口限流
- [open-webSearch](https://github.com/Aas-ee/open-webSearch)（Apache-2.0）— renderMode 三档、人机验证页检测
- [one-search-mcp](https://github.com/yokingma/one-search-mcp)（MIT）— 浏览器本地搜索引擎兜底思路

## 已知限制

- DuckDuckGo 免费接口有匿名限流，短时间大量搜索可能被暂时封禁（服务内置 3 次退避重试）
- **登录墙绕不过**：无头浏览器能过多数反爬（实测知乎的 403 可以），但需要登录才能看的内容依然拿不到（会返回登录提示原文）
- 无头浏览器也过不了的强反爬（Cloudflare 五秒盾、验证码）依然失败，返回明确提示
- 二进制内容（PDF/图片）不做解析，返回提示信息
- 滚动才懒加载的内容可能缺失（只渲染首屏）
- 连接层失败（域名不通/被墙）不做浏览器兜底，直接返回代理配置提示
