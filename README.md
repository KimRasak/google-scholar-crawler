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
| `--bibtex` | 离线拼出参考文献文件（不发请求） |
| `--min-citations`、`--year-from`、`--year-to` | 过滤条件；带年份区间时会丢掉没有年份的记录 |
| `--top` | 概览里列出的高被引条数（默认 5） |
| `--group-by` | 按 `author`/`venue`/`year`/`level` 分组统计 |
| `--min-group`、`--groups` | 隐藏记录数少于 N 的组；最多列出几组（默认 10） |
| `--quiet` | 只打印写出结果，需要配合 `-o`、`--csv` 或 `--bibtex` |

## 接管记录

人工接管是这套工具里最少见、最关键、也最不可复现的一步：它发生时你正忙着解验证码，终端里滚过去的信息事后就找不回来了。所以每次接管都会追加一条记录（默认 `out/challenges.jsonl`），`--show-state` 会一并读出来：

```sh
$ scholar-crawler --show-state
[state] 3 targets in out/state.json (1 finished)
[state]   attention is all you need [en] — next offset 30, 2026-09-02 10:45:51 UTC
[handoff] 2 takeovers in out/challenges.jsonl (captcha x2)
[handoff]   2026-09-02T12:26:23+00:00  captcha -> unattended, waited 6s (after 11 requests, loading 20)
[handoff]     matched form#captcha-form at about:blank
```

一条记录包含：时间、类型（`captcha`/`rate_limit`/`consent`）、检测器命中的是什么、被拦在哪个 URL、这轮此前已发了多少次请求、是否连续被拦（第几次）、等了人多久、以及结局——`resolved`（人解完了，继续抓）、`unattended`（`--headless` 拒绝或等待超时）、`budget`（用满 `--max-handoffs` 停机）、`interrupted`（Ctrl+C）、`rehearsed`（演练）。

有了这些，事后能回答真正要紧的问题：是抓到第几次请求被拦的、是不是解完一次又立刻被拦（说明当前节奏还是太快）、还是根本没人在电脑前。

**URL 会脱敏后再写入**：`/sorry/` 这类验证页上的 `q` 是验证令牌而不是查询词，所以整条只留 `hl`；普通检索页则保留 `q`、`start`、`cites`、`cluster`、`user` 等描述请求的参数，`scisig` 之类签名参数一律写成 `REDACTED`。所以这个文件可以放心留存和分享。

`--rehearse-handoff` 演练时也会写一条（`outcome=rehearsed`），顺带证明这个日志路径是可写的——不必等真被拦时才发现写不进去。

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

### 离线生成参考文献

抓取时加 `--bibtex` 会向 Scholar 多要两次页面（每条记录），是整轮里最贵的部分。但结果页上的题名、作者、期刊、年份、链接其实都已经存下来了，所以事后可以离线拼出可用的条目：

```sh
scholar-digest out/all.jsonl --min-citations 500 --bibtex out/refs.bib
[out] 42 entries -> out/refs.bib (7 keys from the crawl, 35 generated, 12 truncated author lists)
```

这是重建，不是 Scholar 的官方导出，差别值得知道：

```bibtex
% Scholar 自己导出的（抓取时 --bibtex，每条 2 次请求）
@article{velivckovic2017graph,
  title={Graph attention networks},
  author={Veli{\v{c}}kovi{\'c}, Petar and Cucurull, Guillem and Casanova, Arantxa and ...},
  journal={arXiv preprint arXiv:1710.10903},
  year={2017}
}

% 离线拼出来的（0 次请求）
@article{velickovic2017graph,
  title = {{Graph attention networks}},
  author = {P Veličković and G Cucurull and A Casanova and others},
  journal = {arXiv preprint},
  year = {2017},
  url = {https://arxiv.org/abs/1710.10903},
  note = {cited by 41135 on Google Scholar},
}
```

也就是说：作者名只有 Scholar 显示的缩写、被它截断的作者列表补 `and others`、arXiv 编号可能缺失；换来的是零请求，外加原始链接与被引数。要精确条目就在抓取时导；要一份能直接用的书目、又不想再等几小时，就离线生成。

其他细节：已经在抓取时导出过的记录会沿用同一个 key（两份文件指同一篇论文时名字一致）；`Veličković` 这类会正确转写成 `velickovic`（`ł`、`ø`、`ß`、`æ` 也都处理）；key 撞车时追加 `a`、`b`；venue 里出现 Proceedings/Conference/Workshop 的记为 `@inproceedings` 并用 `booktitle`，没有 venue 的记为 `@misc`；题名用双花括号包住，避免某些样式把大小写压平；`&`、`%`、`_` 等会转义。没有题名的记录会被跳过并计数。

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
| `--challenge-log` | 接管记录文件（默认 `out/challenges.jsonl`，URL 已脱敏） |
| `--bibtex` | 同时导出 BibTeX 到 `.bib` 文件；按引用键去重，记录里写入 `extra.bibtex_key` 便于关联 |
| `--profiles-out`、`--dump-html` | 作者主页头部记录（每位作者一行，重复抓取覆盖旧值）、抓到的原始 HTML（排查解析问题用） |
| `--profile`、`--channel`、`--locale`、`--timezone`、`--proxy` | 浏览器 profile 与环境参数 |
| `--min-delay/--max-delay`、`--cooldown-every/--cooldown-seconds` | 抓取节奏 |
| `--handoff-timeout`、`--max-handoffs`、`--backoff-factor`、`--challenge-cooldown` | 等人多久（0 = 无限等）、最多接管几次、每次接管后延迟放大倍数、连续被拦时恢复前的静默等待 |
| `--show-state`、`--forget PATTERN` | 查看断点进度与最近的接管记录；按签名子串清除断点（空串清空全部） |
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
python3 -m pytest -q     # 184 个用例，全部离线
ruff check .             # 与 CI 相同的 lint 配置
```

测试全部离线（不发任何网络请求），按模块分组：

- **解析**：结果卡片（仅引用条目、PDF 侧链、被引/版本计数、词中加粗、第二页的结果计数、零结果页）、作者主页（头部各行、按行位置读统计表、论文行、缺年份与零被引、「显示更多」状态）、真实页面夹具（结果页 10 项自检全过、字段完整性、脱敏规则、夹具不含凭证）
- **URL 与过滤**：查询/主页地址拼装、过滤参数、id 与 URL 解析、cite 弹窗地址
- **抓取循环**：翻页与作者分批、节奏与冷却、连续被拦的静默等待与关闭开关、运行摘要的长短两种格式、HTML dump、导出过程中被拦
- **接管记录**：URL 脱敏（验证页令牌与检索参数区别对待）、单行摘要格式、追加与读回（跳过坏行）、抓取中被拦/接管额度用尽/headless 拒绝三种结局各自落账、演练也落账、`--show-state` 读回
- **人工接管**：真实 headless Chromium DOM 上的验证页判定、等待超时/窗口被关/headless 拒绝、接管演练全链路（识别→清除→恢复）
- **引文网络**：按被引排序选点、宽度上限、访问去重、被引下限、层级推进与提前收敛
- **输出与断点**：JSONL 去重、CSV 导出、作者档案 upsert、`.bib` 去重、断点查看与清除（签名还原、时间戳、旧格式）、被 `-n` 截断的目标仍可续抓
- **离线工具**：汇总的合并取舍/过滤/统计、分组统计与 venue 归一化、书目生成（转写与姓氏、截断作者列表、key 复用与撞车、条目类型、转义与双花括号）
- **计划与实际一致**：把 `--dry-run` 算出的加载次数与真实抓取循环在同一组参数下实际发出的次数逐一比对（含 `-n` 截断的三种情况）
- **命令行**：参数校验、`--dry-run` 的计划数字且不落地文件、`--self-check`、BibTeX 链接发现（按 href 而非文案）与 `<pre>` 提取、`--quiet` 的组合校验

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
  run.py        一次运行的执行：目标抓取、图展开、输出文件开关与汇报
  expand.py     引文网络展开：选点、上限、去重
  plan.py       抓取计划：页数/加载数/用时估算
  selfcheck.py  解析自检：逐字段体检与报告
  rehearsal.py  接管演练：本地验证页与全链路空演
  digest.py     离线汇总：合并去重、过滤、命令行
  analysis.py   离线分析：概览统计与分组
  bibsynth.py   离线书目：由已存字段拼出 BibTeX
  storage.py    JSONL/CSV 写入、作者主页记录、BibTeX 文件、断点状态
  cli.py        命令行入口
tests/          离线测试（含 headless Chromium 判定测试）
```

MIT License。
