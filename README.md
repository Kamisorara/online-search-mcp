# online-search-mcp

本地联网搜索 MCP（Model Context Protocol）服务，供智能体使用：

- **`search_web`** — DuckDuckGo 联网搜索（免费、无需 API Key），支持时效性过滤和地区偏好；可加 `fetch_top=N` 顺带抓取前几条结果的正文摘要，一步拿到答案
- **`search_github`** — GitHub 仓库搜索（官方 API），支持 `language:` / `stars:` / `topic:` / `pushed:` 等限定符，找开源项目和案例用
- **`fetch_url_content`** — 抓取网页正文并转为 Markdown（自动去导航/广告、处理中文编码，正文前附带标题与发布日期）；GitHub/PyPI/Stack Overflow/Wikipedia 自动走官方数据源，JS 渲染/反爬页面自动降级到无头浏览器渲染

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

## 引导智能体主动使用（重要）

挂上 MCP ≠ 智能体一定会用——模型默认倾向凭训练记忆回答。三层引导，从自动到手动：

1. **工具描述（已内置，无需配置）**：每个工具的说明都写了适用场景与示例，服务级 instructions 会在 MCP 握手时注入客户端。任务里出现「最新的 / 查一下 / 找个开源项目」类字眼时，模型会自动匹配到对应工具。
2. **指令文件（最有效，推荐配置）**：在 ZCode 的 `~/.zcode/AGENTS.md`（全局生效）或项目根的 `AGENTS.md` 里立规矩，把「先搜索」变成默认行为；Claude Code 用 `CLAUDE.md`、Cursor 用 `.cursorrules`，内容可直接复用：

   ```markdown
   ## 联网搜索规则
   - 版本号/发布日期、近期新闻、库的最新 API、开源选型、任何不确定的事实：
     必须先联网验证，禁止凭记忆直接回答
   - 通用检索用 search_web（新闻类加 timelimit="w"；想直接拿答案加 fetch_top=2）
   - 找开源项目/示例代码用 search_github（支持 language:python stars:>500 等限定符）
   - 搜索摘要不够时用 fetch_url_content 抓原文；引用时附来源链接
   ```

   > ZCode 中工具全名为 `mcp__online-search__search_web` 等（带服务器前缀），其它客户端一般是裸名 `search_web`。
3. **提问方式（辅助）**：「搜一下最新版」比「你知道…吗」触发率高得多；客户端里挂的 MCP 越多、工具列表越长，选择精度越稀释，不常用的建议在设置里关掉。

## 工具说明

| 工具 | 参数 | 说明 |
|---|---|---|
| `search_web` | `query`（必填）、`max_results=8`、`timelimit=None`（d/w/m/y）、`region="wt-wt"`（cn-zh 中文）、`fetch_top=0`（0-3） | 返回带链接的 Markdown 结果列表；`fetch_top>0` 时并发抓取前 N 条正文摘要（各约 800 字）；被限流/断网时返回友好错误说明而非崩溃 |
| `search_github` | `query`（必填，支持限定符）、`max_results=8`、`sort="best-match"`（stars/forks/updated） | 官方 API 免认证限 10 次/分钟；设 `GITHUB_TOKEN` 环境变量提升到 30 次/分钟 |
| `fetch_url_content` | `url`（必填）、`max_chars=20000`、`render_mode="auto"` | trafilatura 提取正文转 Markdown（正文前带标题/发布日期），超长截断并标注；智能体高频站点自动走官方数据源 |

### 站点专用通道

`fetch_url_content` 对以下站点自动改走官方数据源（更快、更干净、无反爬），命中时结果开头带 `[经站点专用通道读取（…）]` 标注；通道失败自动回落通用抓取，不影响整体可用性：

| 站点 | 数据源 | 说明 |
|---|---|---|
| `github.com/{o}/{r}` 及 `/tree/…` | GitHub API | 仓库元信息（⭐/语言/更新）+ README 原文 |
| `github.com/…/blob/{ref}/{path}` | raw.githubusercontent | 源文件直读（文本类扩展名，代码自动包代码块） |
| `pypi.org/project/{name}` | PyPI JSON API | 版本/摘要/作者/文档链接/顶级依赖 |
| Stack Overflow 及 Stack Exchange 系问答 | Stack Exchange API | 问题 + 采纳回答排最前、其余按票数 |
| `{lang}.wikipedia.org/wiki/…` | Wikipedia REST | 词条全文（大陆网络需开系统代理） |

### `fetch_url_content` 的抓取策略（四级 + 缓存）

1. **站点专用通道**：上表站点走官方 API/raw 直读（0.3-1s，最快最干净）
2. **快路径**：纯 HTTP + trafilatura 抓取（0.1-1s），绝大多数网页走这条
3. **llms.txt 生态**：正文不够时找站点提供的 Markdown 副本（HTML 里声明的 `text/markdown` 链接，或盲试 `url+".md"`），比浏览器渲染快一个量级
4. **兜底**：仍拿不到足够正文（正文 <500 字、被 403/429/5xx 拦截、或检测到人机验证页）时，自动用无头 Chromium 渲染后再提取（约 3-8s），结果带 `[正文经无头浏览器渲染后提取]` 标记
5. **缓存**：同一 URL 5 分钟内重复抓取直接命中缓存（快数百倍），结果带 `[缓存命中]` 标记；发生重定向时结尾标注最终 URL

`render_mode` 参数可显式控制：`auto`（默认）/ `request`（强制纯 HTTP，最快，跳过浏览器兜底）/ `browser`（强制渲染，已知 JS 页面用）。

### `search_web` 的防线与缓存

- 三层防线：ddgs 多引擎聚合（失败重试 3 次）→ 全部失败时**无头浏览器搜百度兜底** → 仍失败返回友好错误
- **自限流**：默认 6 次/分钟（`SEARCH_RPM` 可调），防止频繁搜索触发引擎对整个 IP 的封锁
- **搜索缓存**：同参数查询 5 分钟内直接命中（不占限流配额，智能体重复搜索很常见），带 `[搜索缓存命中]` 标记

## 健康自检（doctor）

排查问题一键体检，输出各依赖版本、代理来源、浏览器兜底、GitHub/PyPI/ddgs 各通道连通性与配额：

```bash
.venv\Scripts\python -m online_search_mcp.doctor           # 全量检查（含真实网络探测）
.venv\Scripts\python -m online_search_mcp.doctor --offline # 只看配置，不联网
```

相关环境变量：`FETCH_BROWSER=off` 关闭浏览器兜底；`FETCH_BROWSER_TIMEOUT=秒`（默认 20）；`FETCH_CACHE_TTL=秒` / `FETCH_CACHE_MAX=条`（默认 300/64，设 0 禁用）；`SEARCH_RPM=次/分钟`（默认 6）；`SEARCH_CACHE_TTL=秒` / `SEARCH_CACHE_MAX=条`（默认 300/32，设 0 禁用）。

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
- [mcpsurfr](https://github.com/nkapila6/mcpsurfr)（MIT）— 站点专用通道（GitHub/PyPI/Stack Exchange/Wikipedia）、llms.txt Markdown 副本、正文附带标题与日期、搜索缓存
- [agent-search-mcp](https://github.com/lennney/agent-search-mcp)（Apache-2.0）— doctor 自检命令形态
- [wigolo](https://github.com/KnockOutEZ/wigolo)（AGPL-3.0，仅思路参考未搬代码）— doctor 命令、结果附正文的产品思路

## 已知限制

- DuckDuckGo 免费接口有匿名限流，短时间大量搜索可能被暂时封禁（服务内置 3 次退避重试 + 自限流）
- **登录墙绕不过**：无头浏览器能过多数反爬（实测知乎的 403 可以），但需要登录才能看的内容依然拿不到（会返回登录提示原文）
- 无头浏览器也过不了的强反爬（Cloudflare 五秒盾、验证码）依然失败，返回明确提示
- 二进制内容（PDF/图片）不做解析，返回提示信息
- 滚动才懒加载的内容可能缺失（只渲染首屏）
- 连接层失败（域名不通/被墙）不做浏览器兜底，直接返回代理配置提示
- 站点专用通道依赖对应官方 API 的可达性（大陆直连下 Wikipedia 可能不通），失败自动回落通用抓取

## License

[MIT](LICENSE)
