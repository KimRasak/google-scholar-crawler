# Google Scholar 浏览器爬虫（验证码人工接管）

用真实浏览器（Playwright + 本机 Chrome）执行 Google Scholar 的学术检索：翻页、解析、落盘全部自动完成；一旦出现 reCAPTCHA、`/sorry/` 拦截页、Cookie 同意页或登录墙，程序停下来把浏览器窗口交给人，人处理完成后自动继续。

程序不尝试识别、绕过或隐藏任何验证challenge——验证只由人在可见窗口里完成。

## 工作方式

1. 用**持久化浏览器 profile** 启动有界面的 Chrome（`--profile`，默认 `.scholar-profile`）。人工通过的验证 Cookie 保存在这个目录里，后续运行复用，因此接管次数会越来越少。
2. 每翻一页前随机等待（默认 4–11 秒），每 10 页长冷却 90 秒；页面加载后有滚动和随机停留，避免机器式的固定节奏。
3. 每次导航后先做 challenge 判定：
   - 命中 → 响铃 + 打印提示 + 把窗口提到最前，然后每 2 秒复检页面，人做完后自动回到抓取；
   - 未命中 → 解析当前结果页。
4. 结果**逐页追加**写入 JSONL 并立即 flush，同时把「该查询下一个未抓取的 offset」写入 state 文件。中途 Ctrl+C、崩溃、接管超时都不会丢已抓到的数据，`--resume` 可从断点继续。

判定依据是 URL（`/sorry/`、`consent.google.`、`accounts.google.com`）、DOM 选择器（`#gs_captcha_ccl`、`#gs_captcha_f`、`form#captcha-form`、reCAPTCHA iframe）以及无结果时的正文关键词；解析计数（被引数、版本数）从链接 href 判定，不依赖界面语言。

## 安装

```sh
git clone https://github.com/KimRasak/google-scholar-crawler.git
cd google-scholar-crawler
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium   # 若不用本机 Chrome（--channel ""）
```

依赖 Python 3.10+（用到 `X | None` 与 `slots=True`）。本机已安装 Chrome 时保持默认 `--channel chrome`，指纹更自然。

## 快速开始

```sh
# 单个查询，抓 3 页（每页 10 条）
python3 -m scholar_crawler -q "large language model agents" -p 3 -o out/agents.jsonl

# 限定年份 + 按时间排序 + 同时导出 CSV
python3 -m scholar_crawler -q "retrieval augmented generation" \
  --year-from 2023 --sort-by-date -p 5 -o out/rag.jsonl --csv out/rag.csv

# 批量查询（文件内一行一个，# 开头为注释，见 queries.example.txt），断点续爬
python3 -m scholar_crawler --queries-file queries.example.txt -p 10 --resume -o out/batch.jsonl

# Scholar 高级语法直接写进 query
python3 -m scholar_crawler -q 'author:"Yoshua Bengio" source:"NeurIPS"' -p 2
```

运行中出现验证时终端会打印：

```
[handoff] captcha: matched #gs_captcha_ccl
[handoff] URL: https://www.google.com/sorry/index?continue=...
[handoff] The browser window is yours. Solve the challenge ...
[handoff] cleared — resuming automated crawl.
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `-q/--query`、`--queries-file` | 查询词，可重复；文件一行一个 |
| `-p/--pages`、`--start`、`--resume` | 每个查询抓几页、起始 offset、从 state 断点继续 |
| `--year-from/--year-to`、`--sort-by-date`、`--review-only` | 年份区间、按日期排序、只要综述 |
| `--no-citations`、`--no-patents` | 排除仅引用条目、排除专利 |
| `--lang`、`--host` | 界面语言 `hl`；镜像站如 `https://scholar.google.de` |
| `-o/--out`、`--csv`、`--state` | JSONL 输出、CSV 导出、断点文件 |
| `--profile`、`--channel`、`--locale`、`--timezone`、`--proxy` | 浏览器 profile 与环境伪装参数 |
| `--min-delay/--max-delay`、`--cooldown-every/--cooldown-seconds` | 抓取节奏 |
| `--handoff-timeout`、`--max-handoffs` | 等人多久（0 = 无限等）、一次运行最多接管几次 |
| `--headless` | 无窗口模式；**此时遇到验证会直接终止并提示改用有界面模式** |

`--headless` 与人工接管天然冲突：没有窗口就没人能操作。建议先用有界面模式跑一次、人工通过验证，之后同一个 `--profile` 在 headless 下命中率会明显提高；即使被拦，程序也会带着明确提示退出而不是空转。

## 输出

JSONL 每行一条记录：

```json
{"cluster_id":"7997180733303660440","position":1,"title":"Attention is all you need",
 "link":"https://proceedings.neurips.cc/...","resource_link":"https://.../paper.pdf","resource_type":"PDF",
 "authors":"A Vaswani, N Shazeer, N Parmar","venue":"Advances in neural information processing systems",
 "year":2017,"cited_by_count":123456,"cited_by_url":"https://scholar.google.com/scholar?cites=...",
 "versions_count":89,"versions_url":"...","related_url":"...","citation_only":false,
 "snippet":"We propose a new simple network architecture ...","query":"...","page_start":0,"fetched_at":"..."}
```

去重按 `cluster_id`（缺失时用 标题+链接），跨页和跨次运行都生效——重复追加同一文件不会产生重复行。`cited_by_url` 可直接作为下一轮 `--query` 之外的入口（用 `--host` + 该 URL 手工翻引文列表）。

## 降低验证频率

- 别调小默认延迟；被封的主因是节奏，不是 User-Agent。
- 一个 profile 只做一件事，长期复用，不要每次删掉 `.scholar-profile`。
- 单次运行页数控制在几十页以内；大批量拆成多天。
- 需要稳定大规模元数据时，优先用有正式 API 的来源（Semantic Scholar、OpenAlex、Crossref），把本工具留给 Scholar 独有的检索与被引数据。

## 测试

```sh
python3 -m pytest -q          # 26 个用例，无需联网
```

覆盖：结果页解析（含仅引用条目、PDF 侧链、被引/版本计数、无结果页）、URL 与过滤参数拼装、JSONL 去重与 CSV 导出、断点状态读写、challenge 判定（真实 headless Chromium 加载 DOM）、接管等待/超时/窗口关闭/headless 拒绝、翻页推进与接管预算。

## 合规

Google Scholar 的服务条款不允许自动化抓取，抓到的数据版权归原出版方。请仅用于个人研究规模的检索，遵守目标站点的条款与 `robots.txt`，不要转售或再分发抓取结果。工具刻意保留低速节奏与人工接管，就是为了不把它变成规模化滥用手段。

## 结构

```
scholar_crawler/
  urls.py       查询 URL 与过滤参数
  parser.py     结果页 HTML → 结构化记录
  challenge.py  验证页判定 + 人工接管等待
  browser.py    持久化 profile 的浏览器会话
  crawler.py    抓取循环：节奏、接管、翻页
  storage.py    JSONL/CSV 写入与断点状态
  cli.py        命令行入口
tests/          离线测试（含 headless Chromium 判定测试）
```
