# Google Scholar 浏览器爬虫（验证码人工接管）

[![tests](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml/badge.svg)](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml)
[English](README.en.md) | 中文

用真实浏览器（Playwright + 本机 Chrome）执行 Google Scholar 的学术检索：翻页、解析、落盘全部自动完成；一旦出现 reCAPTCHA、`/sorry/` 拦截页、Cookie 同意页或登录墙，程序停下来把浏览器窗口交给人，人处理完成后自动继续。

程序不尝试识别、绕过或隐藏任何验证 challenge——验证只由人在可见窗口里完成。

## 工作方式

1. 用**持久化浏览器 profile** 启动有界面的 Chrome（`--profile`，默认 `.scholar-profile`）。人工通过的验证 Cookie 保存在这个目录里，后续运行复用，因此接管次数会越来越少。
2. 每次请求前随机等待（默认 4–11 秒），每 10 次请求长冷却 90 秒；计数覆盖整轮运行的全部请求（跨查询、跨作者、含 BibTeX 导出），换查询时不归零。页面加载后有滚动和随机停留，避免机器式的固定节奏。
3. 每次导航后先做 challenge 判定：
   - 命中 → 响铃 + 打印提示 + 把窗口提到最前，然后每 2 秒复检页面，人做完后自动回到抓取，并把页间延迟按 `--backoff-factor` 放大（默认 ×1.6），越被拦越慢；
   - 未命中 → 解析当前结果页。
4. 结果**逐页追加**写入 JSONL 并立即 flush，同时把「该查询下一个未抓取的 offset」写入 state 文件。中途 Ctrl+C、崩溃、接管超时都不会丢已抓到的数据，`--resume` 可从断点继续。

判定依据是 URL（`/sorry/`、`consent.google.`、`accounts.google.com`）、DOM 选择器（`#gs_captcha_ccl`、`#gs_captcha_f`、`form#captcha-form`、reCAPTCHA iframe）以及无结果时的正文关键词；解析计数（被引数、版本数）从链接 href 判定，不依赖界面语言。

## 安装

```sh
git clone https://github.com/KimRasak/google-scholar-crawler.git
cd google-scholar-crawler
python3 -m pip install -e .          # 提供 scholar-crawler 命令；或 pip install -r requirements.txt
python3 -m playwright install chromium   # 若不用本机 Chrome（--channel ""）
```

需要 Python 3.10+。本机已安装 Chrome 时保持默认 `--channel chrome`，指纹更自然。装好后 `scholar-crawler ...` 与 `python3 -m scholar_crawler ...` 等价。

## 快速开始

```sh
# 关键词检索，抓 3 页（每页 10 条）
scholar-crawler -q "large language model agents" -p 3 -o out/agents.jsonl

# 限定年份 + 按时间排序 + 同时导出 CSV，最多 40 条
scholar-crawler -q "retrieval augmented generation" \
  --year-from 2023 --sort-by-date -n 40 -o out/rag.jsonl --csv out/rag.csv

# 批量查询（文件内一行一个，# 开头为注释，见 queries.example.txt），断点续爬
scholar-crawler --queries-file queries.example.txt -p 10 --resume -o out/batch.jsonl

# 顺着引文网络走：把上一步结果里的 cited_by_url / versions_url 直接粘进来
scholar-crawler --cites "https://scholar.google.com/scholar?cites=2454404157773228931" -p 5 -o out/citing.jsonl
scholar-crawler --cluster 2454404157773228931 -o out/versions.jsonl

# 顺手导出 BibTeX（每条多 2 次页面加载，慢但可直接进文献管理器）
scholar-crawler -q "diffusion models" -p 2 --bibtex out/refs.bib -o out/diffusion.jsonl

# 抓作者主页：一次请求最多 100 篇；主页头部（引用总数、h-index、i10、研究兴趣）单独落盘
scholar-crawler --author kukA0LcAAAAJ -o out/bengio.jsonl --profiles-out out/profiles.jsonl
scholar-crawler --author "https://scholar.google.com/citations?user=kukA0LcAAAAJ&hl=en" --sort-by-date -p 2

# Scholar 高级语法直接写进 query
scholar-crawler -q 'author:"Yoshua Bengio" source:"NeurIPS"' -p 2
```

运行中出现验证时终端会打印：

```
[handoff] captcha: matched #gs_captcha_ccl
[handoff] URL: https://www.google.com/sorry/index?continue=...
[handoff] The browser window is yours. Solve the challenge ...
[handoff] cleared — resuming automated crawl.
[pace] backing off to 6.4-17.6s between pages
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `-q/--query`、`--queries-file` | 关键词检索，可重复；文件一行一个 |
| `--cites`、`--cluster` | 抓某文的引证文献 / 全部版本；接受数字 id 或结果里的 `cited_by_url`、`versions_url`，可重复 |
| `--author` | 抓作者主页论文列表；接受 12 位 user id 或主页 URL，可重复；配合 `--sort-by-date` 按年份排序 |
| `-p/--pages`、`-n/--max-results` | 每个入口抓几页 / 最多抓几条（末页精确截断）。检索页每页 10 条，作者主页每页 100 篇 |
| `--start`、`--resume` | 起始 offset；从 state 断点继续 |
| `--year-from/--year-to`、`--sort-by-date`、`--review-only` | 年份区间、按日期排序、只要综述 |
| `--no-citations`、`--no-patents` | 排除仅引用条目、排除专利 |
| `--lang`、`--host` | 界面语言 `hl`；镜像站如 `https://scholar.google.de` |
| `-o/--out`、`--csv`、`--state` | JSONL 输出、CSV 导出、断点文件 |
| `--bibtex` | 同时导出 BibTeX 到 `.bib` 文件；按引用键去重，记录里写入 `extra.bibtex_key` 便于关联 |
| `--profiles-out`、`--dump-html` | 作者主页头部记录（每位作者一行，重复抓取覆盖旧值）、抓到的原始 HTML（排查解析问题用） |
| `--profile`、`--channel`、`--locale`、`--timezone`、`--proxy` | 浏览器 profile 与环境参数 |
| `--min-delay/--max-delay`、`--cooldown-every/--cooldown-seconds` | 抓取节奏 |
| `--handoff-timeout`、`--max-handoffs`、`--backoff-factor` | 等人多久（0 = 无限等）、最多接管几次、每次接管后延迟放大倍数 |
| `--headless` | 无窗口模式；**此时遇到验证会直接终止并提示改用有界面模式** |

`--headless` 与人工接管天然冲突：没有窗口就没人能操作。建议先用有界面模式跑一次、人工通过验证，之后同一个 `--profile` 在 headless 下命中率会明显提高；即使被拦，程序也会带着明确提示退出而不是空转。

节奏参数写错（负数、`--min-delay` 大于 `--max-delay`、`--backoff-factor` 小于 1）会在启动时立刻报错退出，不会静默按奇怪的时序跑。

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

去重按 `cluster_id`（缺失时用 标题+链接），跨页和跨次运行都生效——重复追加同一文件不会产生重复行。`query` 字段记录来源入口（关键词，或 `cites:<id>`、`cluster:<id>`、`author:<id>`）。

作者主页的论文写进同一个 JSONL（`extra.citation_id` 记录 Scholar 的 citation id），主页头部另外写进 `--profiles-out`：

```json
{"user_id":"kukA0LcAAAAJ","name":"Yoshua Bengio",
 "affiliation":"Professor of computer science, University of Montreal, Mila, IVADO, CIFAR",
 "organization":"University of Montreal","homepage":"https://yoshuabengio.org/",
 "verified_email":"Verified email at umontreal.ca",
 "interests":["Machine learning","deep learning","artificial intelligence"],
 "cited_by_total":1149112,"cited_by_recent":764217,"h_index":259,"h_index_recent":208,
 "i10_index":1106,"i10_index_recent":947,"fetched_at":"..."}
```

## 关于 BibTeX 导出

`--bibtex` 每条记录要多走两次页面加载：先打开 Scholar 的 "Cite" 弹窗，再打开弹窗里带签名的 `scholar.bib` 链接（签名参数无法自己拼出来）。因此：

- 一次抓 10 条的页面，开了 `--bibtex` 就是 21 次请求而不是 1 次，整体耗时约慢一个数量级，被验证拦的概率也随之上升。建议配合 `-n` 只对确定要用的结果导出。
- 这两次加载都走可见窗口的正常导航，因此同样受节奏控制和人工接管保护。不能改用后台 HTTP 请求：Scholar 对浏览器导航之外发起的同样请求直接返回 429。
- 作者主页的论文条目没有 Scholar 的 `data-cid`，无法走这条导出路径，会被跳过并在开头提示一次。

## 降低验证频率

- 别调小默认延迟；被封的主因是节奏，不是 User-Agent。
- 一个 profile 只做一件事，长期复用，不要每次删掉 `.scholar-profile`。
- 单次运行页数控制在几十页以内；大批量拆成多天。
- 需要稳定大规模元数据时，优先用有正式 API 的来源（Semantic Scholar、OpenAlex、Crossref），把本工具留给 Scholar 独有的检索与被引数据。

## 开发

```sh
python3 -m pytest -q     # 79 个用例，全部离线
ruff check .             # 与 CI 相同的 lint 配置
```

测试覆盖：结果页解析（含仅引用条目、PDF 侧链、被引/版本计数、`<b>` 高亮词断词、第二页起的结果总数、无结果页）、作者主页解析（头部、按行位置读取的统计表、论文行、0 被引与缺年份、"show more" 状态）、URL 与过滤参数拼装、id/URL 解析、JSONL 去重与 CSV 导出、profile 覆盖写、断点状态读写、challenge 判定（真实 headless Chromium 加载 DOM）、接管等待/超时/窗口关闭/headless 拒绝、BibTeX 链接识别（按 href 而非按标签文字）与 `<pre>` 内容提取、`.bib` 去重、导出过程中的接管、翻页与作者分批推进、结果上限截断、未知主页版式报错、接管后自动减速、HTML dump、命令行参数到请求的组装。GitHub Actions 在 Python 3.10 与 3.13 上跑同一套。

## 合规

Google Scholar 的服务条款不允许自动化抓取，抓到的数据版权归原出版方。请仅用于个人研究规模的检索，遵守目标站点的条款与 `robots.txt`，不要转售或再分发抓取结果。工具刻意保留低速节奏与人工接管，就是为了不把它变成规模化滥用手段。

## 结构

```
scholar_crawler/
  urls.py       查询/主页 URL、过滤参数、id/URL 解析
  parser.py     结果页与作者主页 HTML → 结构化记录
  challenge.py  验证页判定 + 人工接管等待
  browser.py    持久化 profile 的浏览器会话
  crawler.py    抓取循环：节奏、接管、翻页/分批、HTML dump
  storage.py    JSONL/CSV 写入、作者主页记录、BibTeX 文件、断点状态
  cli.py        命令行入口
tests/          离线测试（含 headless Chromium 判定测试）
```

MIT License。
