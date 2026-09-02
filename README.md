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

# 沿引文网络往外走一层：先跑关键词，再抓被引最多的 3 篇各自的引证文献
scholar-crawler -q "chain of thought prompting" -p 1 \
  --follow-cites 1 --follow-breadth 3 --follow-min-citations 50 -o out/graph.jsonl

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

## 汇总已抓到的结果（不发请求）

多次断断续续地抓，结果会散在好几个 JSONL 里。`scholar-digest` 只读本地文件，做合并去重、过滤、统计和导出：

```sh
# 合并多份结果，去重后写成一份，并导出 CSV
scholar-digest out/*.jsonl -o out/all.jsonl --csv out/all.csv

# 只要 2018 年以后、被引 1000 以上的
scholar-digest out/all.jsonl --min-citations 1000 --year-from 2018 -o out/hot.jsonl
```

默认打印一份概览：记录数、被引总数、已带 BibTeX 键的条数、只有引用信息的条数、年份分布、引文层级分布、出现最多的期刊/会议，以及被引最高的几条。

同一篇论文在多份文件里重复时，保留被引数更高（也就是更新）的那条，字段更全的那条优先，`extra` 里的 `bibtex_key` 不会丢，`follow_depth` 取最浅的一层。

| 参数 | 作用 |
| --- | --- |
| `-o`、`--csv` | 写出合并后的 JSONL / CSV |
| `--min-citations`、`--year-from`、`--year-to` | 过滤条件；带年份区间时会丢掉没有年份的记录 |
| `--top` | 概览里列出的高被引条数（默认 5） |
| `--group-by` | 按 `author`/`venue`/`year`/`level` 分组统计 |
| `--min-group`、`--groups` | 隐藏记录数少于 N 的组；最多列出几组（默认 10） |
| `--quiet` | 只打印写出结果，需要配合 `-o` 或 `--csv` |

## 查看与重置断点

抓了很多次之后，哪些目标真的抓完了、下次会从哪一页继续，光看 state 文件的 JSON 并不好认（键是给程序用的签名）。两个命令都不联网：

```sh
$ scholar-crawler --show-state --state out/state.json
[state] 3 targets in out/state.json (1 finished)
[state]   attention is all you need [en] — next offset 30, 2026-09-02 10:45:51 UTC
[state]   cites:2960712678066186980 [en] — done after 50 records, 2026-09-02 10:45:51 UTC
[state]   author:kukA0LcAAAAJ [en] — next offset 100, 2026-09-02 10:45:51 UTC

# 让某个目标从头再抓（签名里含该子串的都会被清掉；空串清空全部）
$ scholar-crawler --forget "attention" --state out/state.json
```

签名会被还原成可读的目标名，年份区间、语言、排序、`--review-only` 之类的过滤条件以 `[...]` 附在后面——同一个查询配不同过滤条件是不同的断点，这样一眼能分清。每条断点现在还带最后更新时间（旧的 state 文件照样能读，只是显示 `unknown time`）。

`-n/--max-results` 截断的目标不再被记成「已抓完」：那是我们自己决定停的，Scholar 那边还有结果，所以它保持可续抓。

## 先算账：`--dry-run`

`--pages`、`-n`、`--follow-cites`、`--bibtex` 的成本是相乘的，很容易一不小心开出一个跑几小时的任务。`--dry-run` 不发任何请求，先把这轮要抓什么、最多多少次页面加载、按当前节奏大概多久列清楚：

```sh
$ scholar-crawler -q "diffusion models" -q "flow matching" -p 3 \
    --follow-cites 1 --follow-breadth 4 --bibtex out/x.bib --dry-run
[plan] diffusion models -> https://scholar.google.com/scholar?hl=en&q=diffusion+models&as_vis=0&as_sdt=0%2C5
[plan] flow matching -> https://scholar.google.com/scholar?hl=en&q=flow+matching&as_vis=0&as_sdt=0%2C5
[plan] seed targets: 6 page loads, up to 60 records
[plan] citation expansion: up to 8 listings, 24 page loads, up to 240 records
[plan] bibtex export: up to 600 page loads
[plan] total: up to 630 page loads for 300 records
[plan] estimated 3.4 h at 4-11s between requests plus 63 cooldowns of 90s
[plan] nothing was requested; drop --dry-run to start
```

所有数字都是上限：列表提前抓完、引文网络无可展开时都会更少。目标写错（比如一个入口都没给）在 `--dry-run` 下同样会报错，所以它也能当参数检查用。

### 分组统计

`--group-by` 把合并后的结果按第一作者、期刊/会议、年份或引文层级分组，按被引总数排序：

```
$ scholar-digest out/all.jsonl --group-by venue --groups 4
  by venue                                 count  citations  median  years      most cited
    Advances in neural information processin     1     119743  119743  2014       Generative adversarial nets
    nature                                       1     118913  118913  2015       Deep learning
    arXiv preprint                               2      44564   22282  2017-2021  Graph attention networks
    The world wide web                           1       4408    4408  2019       Heterogeneous graph attention network
    ... and 4 more groups
```

`median`（组内被引中位数）是为了公平比较：某组只靠一篇爆款撑起来，还是整体都被引得多，看中位数才分得清。`--min-group 3` 之类可以把只有一两条的长尾折掉。

分组时会做两处归一化，否则同一个去处会被拆散：所有 arXiv 预印本归为 `arXiv preprint`（Scholar 会把 arXiv 编号写进 venue），作者主页那种 `nature 521 (7553), 436-444, 2015` 会去掉卷号页码归为 `nature`；大小写不同也算同一组（显示时保留先出现的写法）。默认概览里的「出现最多的期刊」现在也用同一套归一化。

## 真实结构回归夹具

`tests/pages/` 里放着四份**真实抓取页面**的脱敏副本（结果页、作者主页、cite 弹窗、BibTeX 导出页）。手写夹具能证明解析逻辑对，但证明不了它还贴合 Scholar 的真实结构；这几份夹具补的就是这块，而且离线跑：结果页那份会把 `--self-check` 的 10 项检查完整跑一遍。

脱敏由 `tests/sanitize.py` 完成，规则是「结构全留，凭证全去」：删掉 `<script>`/`<style>`/`<iframe>`，图片 `src` 换成 `about:blank`，签名与会话参数（`scisig`、`xsrf`、`scisdr`、`usg`…）无论在 URL 里还是嵌在 `continue=` 这类编码参数里都替换成 `REDACTED`，隐藏表单里的 xsrf 值同样替换，`Verified email at ...` 改写成 `example.edu`，重复卡片裁到几条。解析器要用的 class 名、`data-cid`、`cites=`/`cluster=` 链接、`scisf=4` 这类标记一个不动。另有一条测试专门扫这四份文件，确保里面没有 `<script>`、没有未脱敏的长 token。

Scholar 改版时刷新夹具：

```sh
scholar-crawler -q "graph attention networks" -p 1 -n 2 --bibtex out/x.bib \
    --dump-html out/dump -o out/d.jsonl
python3 -m tests.sanitize out/dump/<结果页>.html tests/pages/results.html 6
```

## 自检

怀疑 Scholar 改版、解析结果变空时，先跑一次自检（只发一次请求）：

```sh
scholar-crawler --self-check
```

它抓一页固定的宽泛查询，逐项报告标题、链接、作者行、年份、摘要、`data-cid`、被引链接、结果总数、下一页是否都还能解析出来，全通过退出码 0，任一项失败退出码 1 并提示用 `--dump-html` 保存页面。

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `-q/--query`、`--queries-file` | 关键词检索，可重复；文件一行一个 |
| `--cites`、`--cluster` | 抓某文的引证文献 / 全部版本；接受数字 id 或结果里的 `cited_by_url`、`versions_url`，可重复 |
| `--author` | 抓作者主页论文列表；接受 12 位 user id 或主页 URL，可重复；配合 `--sort-by-date` 按年份排序 |
| `-p/--pages`、`-n/--max-results` | 每个入口抓几页 / 最多抓几条（末页精确截断）。检索页每页 10 条，作者主页每页 100 篇 |
| `--follow-cites`、`--follow-breadth`、`--follow-min-citations` | 抓完种子入口后，继续抓「引用它们的文献」若干层；每层只展开被引最多的 N 条，且低于引用下限的直接跳过 |
| `--start`、`--resume` | 起始 offset；从 state 断点继续 |
| `--year-from/--year-to`、`--sort-by-date`、`--review-only` | 年份区间、按日期排序、只要综述 |
| `--no-citations`、`--no-patents` | 排除仅引用条目、排除专利 |
| `--lang`、`--host` | 界面语言 `hl`；镜像站如 `https://scholar.google.de` |
| `-o/--out`、`--csv`、`--state` | JSONL 输出、CSV 导出、断点文件 |
| `--bibtex` | 同时导出 BibTeX 到 `.bib` 文件；按引用键去重，记录里写入 `extra.bibtex_key` 便于关联 |
| `--profiles-out`、`--dump-html` | 作者主页头部记录（每位作者一行，重复抓取覆盖旧值）、抓到的原始 HTML（排查解析问题用） |
| `--profile`、`--channel`、`--locale`、`--timezone`、`--proxy` | 浏览器 profile 与环境参数 |
| `--min-delay/--max-delay`、`--cooldown-every/--cooldown-seconds` | 抓取节奏 |
| `--handoff-timeout`、`--max-handoffs`、`--backoff-factor`、`--challenge-cooldown` | 等人多久（0 = 无限等）、最多接管几次、每次接管后延迟放大倍数、连续被拦时恢复前的静默等待 |
| `--show-state`、`--forget PATTERN` | 查看断点进度；按签名子串清除断点（空串清空全部） |
| `--dry-run` | 只打印这轮的抓取计划与用时估算，不发任何请求 |
| `--self-check` | 跑一次解析自检（一个请求），逐项报告哪些字段还能正常解析 |
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

## 关于引文网络展开

`--follow-cites DEPTH` 会在种子入口跑完之后，把已抓到的记录按被引数从高到低取前 `--follow-breadth` 条，各自打开「被引用次数」列表继续抓，逐层向外。

- 请求数是乘法增长：1 个种子、深度 2、宽度 5 就是最多 31 个列表（每个列表还要按 `-p` 翻页）。启动时会先打印本轮的上限估算。
- 同一个 cites id 在整轮里只抓一次，重复的分支会被跳过；每条记录写入 `extra.follow_depth` 标明它来自第几层。
- 展开出来的列表沿用命令行上的年份、语言、排序等过滤条件，`--resume` 也照常按每个列表的签名记断点。
- 作者主页抓到的论文同样可以作为展开起点。

## 关于 BibTeX 导出

`--bibtex` 每条记录要多走两次页面加载：先打开 Scholar 的 "Cite" 弹窗，再打开弹窗里带签名的 `scholar.bib` 链接（签名参数无法自己拼出来）。因此：

- 一次抓 10 条的页面，开了 `--bibtex` 就是 21 次请求而不是 1 次，整体耗时约慢一个数量级，被验证拦的概率也随之上升。建议配合 `-n` 只对确定要用的结果导出。
- 这两次加载都走可见窗口的正常导航，因此同样受节奏控制和人工接管保护。不能改用后台 HTTP 请求：Scholar 对浏览器导航之外发起的同样请求直接返回 429。
- 作者主页的论文条目没有 Scholar 的 `data-cid`，程序会先用它的 cluster 列表把 `data-cid`查出来，所以每条是 3 次加载而不是 2 次；开头会提示一次。

## 演练人工接管

真验证码不好按需触发，所以可以先空演一遍整条接管链路（**不发任何请求**，页面是本地生成的）：

```sh
scholar-crawler --rehearse-handoff
```

流程和真遇到验证时完全一致：检测到「验证页」→ 响铃并把窗口提到最前 → 打印接管提示 → 轮询等你操作。页面上有一个按钮，按下就等于「验证已通过」，程序会确认页面恢复成正常内容并报告等待了多久，退出码 0。没人操作时会在 `--handoff-timeout` 到点后报错退出（退出码 1）；加 `--headless` 则会验证「无窗口就拒绝运行」这条路径。

## 运行摘要与自适应减速

每轮结束（正常结束、Ctrl+C 中断、出错退出都一样）会打印一行运行摘要：

```
[run] 12 requests in 3.4 min (3.5/min), 1 takeover (captcha x1), 0 navigation retries, delay now 6.4-17.6s
```

请求数含 cite 弹窗与 BibTeX 导出；接管次数按类型分列；`delay now` 是退避之后的当前节奏——如果它明显比初始值大，说明这轮被拦过，当前 IP 或节奏需要更保守。运行不到 30 秒时只报秒数，不报速率，因为那时的速率主要反映启动开销。

自适应减速分两级：每次人工接管后按 `--backoff-factor` 放大延迟；如果**中间没有一次正常加载**就又被拦（说明解完一次并没有恢复信任），则在恢复前先静默等待 `--challenge-cooldown` 秒，第三次连续被拦等两倍，以此类推。超过 `--max-handoffs` 仍然直接中止。

## 降低验证频率

- 别调小默认延迟；被封的主因是节奏，不是 User-Agent。
- 一个 profile 只做一件事，长期复用，不要每次删掉 `.scholar-profile`。
- 单次运行页数控制在几十页以内；大批量拆成多天。
- 需要稳定大规模元数据时，优先用有正式 API 的来源（Semantic Scholar、OpenAlex、Crossref），把本工具留给 Scholar 独有的检索与被引数据。

## 开发

```sh
python3 -m pytest -q     # 161 个用例，全部离线
ruff check .             # 与 CI 相同的 lint 配置
```

测试覆盖：结果页解析（含仅引用条目、PDF 侧链、被引/版本计数、`<b>` 高亮词断词、第二页起的结果总数、无结果页）、作者主页解析（头部、按行位置读取的统计表、论文行、0 被引与缺年份、"show more" 状态）、URL 与过滤参数拼装、id/URL 解析、JSONL 去重与 CSV 导出、profile 覆盖写、断点状态读写、challenge 判定（真实 headless Chromium 加载 DOM）、接管等待/超时/窗口关闭/headless 拒绝、BibTeX 链接识别（按 href 而非按标签文字）与 `<pre>` 内容提取、`.bib` 去重、导出过程中的接管、分组统计（第一作者取法、期刊名归一化、四个维度的标签、按被引排序与中位数、小组隐藏、表格对齐）、真实页面回归（结果页 10 项自检全过、字段完整性、作者统计与论文行、cite 弹窗到 BibTeX、脱敏规则与「夹具不含凭证」扫描）、断点查看与清除（签名还原、时间戳、旧格式兼容、按子串清除、被 `-n` 截断的目标仍可续抓）、抓取计划（页数被 `-n` 收窄、作者按 100 条一页、展开与 BibTeX 的乘法成本、时长格式、`--dry-run` 不落地任何文件）、运行摘要（长短两种时长格式、按类型统计接管）、连续被拦时的静默等待与关闭开关、接管演练（真实 DOM 里被识别为验证页、按钮按下后恢复、未恢复与未识别的报告、headless 拒绝）、离线汇总（合并取舍、`extra` 合并、层级取最浅、过滤组合、统计与排序、写文件、参数校验）、自检报告（健康页面、空结果页、字段缺失定位、末页判定、输出格式）、作者条目的 `data-cid` 解析回退、引文网络展开（按被引排序、宽度上限、访问去重、引用下限、逐层推进与提前收敛）、翻页与作者分批推进、结果上限截断、未知主页版式报错、接管后自动减速、HTML dump、命令行参数到请求的组装。GitHub Actions 在 Python 3.10 与 3.13 上跑同一套。

## 合规

Google Scholar 的服务条款不允许自动化抓取，抓到的数据版权归原出版方。请仅用于个人研究规模的检索，遵守目标站点的条款与 `robots.txt`，不要转售或再分发抓取结果。工具刻意保留低速节奏与人工接管，就是为了不把它变成规模化滥用手段。

## 结构

```
scholar_crawler/
  urls.py       查询/主页 URL、过滤参数、id/URL 解析
  parser.py     结果页与作者主页 HTML → 结构化记录
  challenge.py  验证页判定 + 人工接管等待
  browser.py    持久化 profile 的浏览器会话
  crawler.py    抓取循环：节奏、接管、翻页/分批、BibTeX 取用、HTML dump
  expand.py     引文网络展开：选点、上限、去重
  plan.py       抓取计划：页数/加载数/用时估算
  selfcheck.py  解析自检：逐字段体检与报告
  rehearsal.py  接管演练：本地验证页与全链路空演
  digest.py     离线汇总：合并去重、过滤、统计、导出
  storage.py    JSONL/CSV 写入、作者主页记录、BibTeX 文件、断点状态
  cli.py        命令行入口
tests/          离线测试（含 headless Chromium 判定测试）
```

MIT License。
