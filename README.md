# Google Scholar 浏览器爬虫（验证码人工接管）

[![tests](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml/badge.svg)](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml)
[English](README.en.md) | 中文

用真实浏览器（Playwright + 本机 Chrome）执行 Google Scholar 的学术检索：翻页、解析、落盘全部自动完成；一旦出现 reCAPTCHA、`/sorry/` 拦截页、Cookie 同意页或登录墙，程序停下来把浏览器窗口交给人，人处理完成后自动继续。

程序不尝试识别、绕过或隐藏任何验证 challenge——验证只由人在可见窗口里完成。

## 三步跑起来

**一、装**（两条命令；细节见[安装](#安装)）

```sh
pip install git+https://github.com/KimRasak/google-scholar-crawler   # pipx install 同样可以
scholar-crawler --install-browser                                    # 没装 Chrome 才需要
```

**二、抓一次**（一次请求，约 5 秒，会弹出一个真实的 Chrome 窗口）

```sh
$ scholar-crawler -q "retrieval augmented generation survey" -p 1 -o out/rag.jsonl
[query] 'retrieval augmented generation survey' from offset 0
[page] offset=0 parsed=10 new=10 total=~548000
[out] 10 new records (0 duplicates skipped) -> out/rag.jsonl
[run] 1 request in 5s, 0 takeovers, 0 navigation retries, delay now 4.0-11.0s
```

**三、看拿到了什么**

`out/rag.jsonl` 每行一条记录，字段是 Scholar 结果卡片上能看到的一切（完整字段表见[输出](#输出)）：

```json
{"title":"Retrieval-augmented generation for large language models: A survey",
 "authors":"Y Gao, Y Xiong, X Gao, K Jia, J Pan","year":2023,"venue":"arXiv preprint",
 "cited_by_count":7878,"cited_by_url":"https://scholar.google.com/scholar?cites=...",
 "cluster_id":"...","link":"...","query":"retrieval augmented generation survey"}
```

同时出现 `out/state.json`（断点，下次加 `--resume` 就接着抓；忘了加也不会白跑——同一个目标再来一次时，开头那行 `[state] ... already reached offset 10` 会先告诉你）。想看看一份集合长什么样，接着跑一条不发请求的汇总：

```sh
scholar-digest out/rag.jsonl                # 规模、年份、期刊、被引最高的几篇
scholar-digest out/rag.jsonl --report out/report.md   # 写成一份可读的 Markdown
```

再往下要么按[从哪读起](#从哪读起)挑一节，要么直接 `scholar-crawler --recipes` 复制现成命令。给 AI agent 调用的话，一页读完的约定在 [AGENTS.md](AGENTS.md)。

## 被拦时会发生什么

抓着抓着 Google 弹出验证是正常的，这个工具的整个设计就是围绕这件事：**它不认、不绕、不藏**，只把窗口交给人。真实的等待过程长这样（只把 URL 换成了 `/sorry/`）：

```
[handoff] captcha: matched #gs_captcha_ccl
[handoff] URL: https://www.google.com/sorry/index?continue=...
[handoff] The browser window is yours. Solve the challenge (or accept the
[handoff] consent/sign-in page) and leave it on the Scholar result page.
[handoff] No keypress needed — the page is re-checked every 2s and crawling resumes by itself. You have 600s to act.
[handoff] Press Ctrl+C to stop instead.
[handoff] waiting 15s so far, 585s left; still showing captcha
[handoff] the page is now a sign_in: account sign-in wall
[handoff] still waiting; 60s left before the run gives up and stops with whatever it collected
[handoff] cleared after 128s — resuming automated crawl.
[pace] backing off to 6.4-17.6s between pages
```

你要做的只有一件事：在弹到最前面的那个窗口里把验证做完，然后什么都不用按。几个为「人离开了一会儿」准备的细节：

- **不需要按键**：程序每 2 秒重看一眼页面，恢复正常就继续；开头就说明还剩多少时间（`--handoff-timeout 0` 是无限等）。
- **验证类型变了会说**：验证码点完却跳出登录墙时你要做的事不一样，所以它明确报出来（`the page is now a sign_in`）。
- **放弃前再响一次铃**：超时前 60 秒重新响铃，并说明「再不处理就带着已抓到的数据停机」。
- **越被拦越慢**：接管一次之后页间延迟按 `--backoff-factor` 放大（默认 ×1.6），下次运行也会参考[接管记录](#接管记录)自动起得更慢。
- **数据不会丢**：结果逐页落盘，`--headless` 下无人可接管时会以 `challenge_unattended` 停机，已抓到的仍在文件里。

第一次运行大概率会被拦一次（profile 里还没有 Cookie）；人工过一次之后 Cookie 存在 `.scholar-profile` 里复用，后面就少多了。相关几节：[降低验证频率](#降低验证频率)、[演练人工接管](#演练人工接管)、[跨运行学会减速](#跨运行学会减速)。

## 从哪读起

| 你的情况 | 读这几节 |
| --- | --- |
| 第一次用 | [三步跑起来](#三步跑起来) → [被拦时会发生什么](#被拦时会发生什么) |
| 要抓一批数据 | [更多用法](#更多用法) → [先把命令读回来、并算清账：`--dry-run`](#先把命令读回来并算清账--dry-run) → [常用参数](#常用参数) |
| 抓完了要用数据 | [汇总已抓到的结果](#汇总已抓到的结果不发请求) → [出一份可读的综述](#出一份可读的综述--report) → [离线生成参考文献](#离线生成参考文献) |
| 老是被验证码拦 | [被拦时会发生什么](#被拦时会发生什么) → [降低验证频率](#降低验证频率) → [接管记录](#接管记录) |
| 报错停了，或结果不对 | [出错时给人话](#出错时给人话) → [自检](#自检) → `--dump-html` |
| 中途断了 | [查看与重置断点](#查看与重置断点) → `--resume` |
| 让 AI agent 来调研 | [给程序调用：`--json`](#给程序调用--json) → [AGENTS.md](AGENTS.md)（一页写完的调用约定） |
| 想改代码 | [开发](#开发) → [结构](#结构) → [工作方式](#工作方式) |

一句话版本：`scholar-crawler --recipes` 会直接给你能用的命令，遇到问题再回来查对应那节。

## 工作方式

1. 用**持久化浏览器 profile** 启动有界面的 Chrome（`--profile`，默认 `.scholar-profile`）。
2. 每次请求前随机等待（默认 4–11 秒），每 10 次请求长冷却 90 秒；计数覆盖整轮运行的全部请求（跨查询、跨作者、含 BibTeX 导出），换查询时不归零。页面加载后有滚动和随机停留，避免机器式的固定节奏。
3. 每次导航后先做 challenge 判定：命中就把窗口交给人（[被拦时会发生什么](#被拦时会发生什么)），未命中就解析当前结果页。
4. 结果**逐页追加**写入 JSONL 并立即 flush，同时把「该查询下一个未抓取的 offset」写入 state 文件，`--resume` 从那里继续。

判定依据是 URL（`/sorry/`、`consent.google.`、`accounts.google.com`）、DOM 选择器（`#gs_captcha_ccl`、`#gs_captcha_f`、`form#captcha-form`、reCAPTCHA iframe）以及无结果时的正文关键词；解析计数（被引数、版本数）从链接 href 判定，不依赖界面语言。

## 安装

`pip install git+https://github.com/KimRasak/google-scholar-crawler` 之后 `scholar-crawler --install-browser`，两条就够（见[三步跑起来](#三步跑起来)）。第一条写成 `pip` 是因为它一定在：`pipx install <同一个 URL>` 会把工具装进独立环境、更干净，但 pipx 本身也得先装，所以它是可选项而不是第一步。第二条用当前解释器去执行 Playwright 的下载，所以无论装在 pipx 的独立环境、venv 还是全局，浏览器都落在对的地方——这一步是新装用户唯一猜不到的动作，所以它是一个命令而不是一段说明。

本机已装 Chrome 的话这条可以省掉：默认 `--channel chrome` 驱动的就是系统 Chrome，那 550 MB 的 Chromium 一次也不会被下载或打开。`--doctor` 只检查这次运行真正要启动的那个浏览器，所以在这种机器上它会直接放行。

想改代码就从源码装：

```sh
git clone https://github.com/KimRasak/google-scholar-crawler.git
cd google-scholar-crawler
python3 -m pip install -e .          # 提供 scholar-crawler 命令；或 pip install -r requirements.txt
scholar-crawler --install-browser
```

需要 Python 3.10+。本机已安装 Chrome 时保持默认 `--channel chrome`，指纹更自然。装好后 `scholar-crawler ...` 与 `python3 -m scholar_crawler ...` 等价，`scholar-crawler --version` 报告装的是哪一版。

装完先跑一次环境体检，它不发任何请求：

```sh
$ scholar-crawler --doctor
[doctor] + python                 3.13.5 at /opt/miniconda3/bin/python3
[doctor] + version                0.2.0
[doctor] + playwright             1.60.0
[doctor] + bs4                    4.14.3
[doctor] + lxml                   6.1.0
[doctor] + settings files         tomllib (stdlib) reads --config files
[doctor] + browser                chrome at /Applications/Google Chrome.app/...; bundled Chromium is also available
[doctor] ! profile                .scholar-profile holds no cookies yet, so the first challenge will need a human
[doctor] + output                 out is writable
[doctor] nothing is broken; these are worth knowing:
[doctor]   profile: expect one takeover on the first run; the cleared cookies are then reused
```

没装好的样子长这样——这也是新装用户最可能看到的一屏（`--channel ''` 表示这次运行要用自带的 Chromium，而它还没下载）：

```sh
$ scholar-crawler --doctor --channel ''
[doctor] x browser                bundled Chromium not downloaded (expected at .../chromium-1234/...)
[doctor] ! profile                .scholar-profile holds no cookies yet, so the first challenge will need a human
[doctor] 1 problem must be fixed before a crawl can run:
[doctor]   browser: scholar-crawler --install-browser
[doctor] also worth knowing, but nothing to fix:
[doctor]   profile: expect one takeover on the first run; the cleared cookies are then reused
```

要修的和只需知道的分成两段：一条 `!` 出现在「必须修」下面，会被读成第二个问题，而一个新装环境看到的第一屏不该把自己说得比实际更糟。照它给的那条命令跑完 `--install-browser`（下 280 MB，占盘 550 MB），同一条 `--doctor` 就会变成退出码 0。

检查项：Python 版本是否达到 3.10、装上的版本号与源码里的版本号是否还一致、三个依赖是否装上且版本不低于 `pyproject.toml` 声明的下限、能不能读 TOML 设置文件（3.11 起是标准库的 `tomllib`，3.10 需要 `tomli`）、**这次运行真正要启动的那个浏览器**在不在、profile 里有没有以前攒下的 cookie、输出与断点文件的目录是否可写。每项失败都给出具体的修复命令（`pip install -e .`、`scholar-crawler --install-browser`、`--channel ''`……），有 `x` 就退出码 1。

三个刻意的设计：浏览器只查一条，因为一次运行只启动一个——默认 `--channel chrome` 时系统 Chrome 在就够了（自带 Chromium 没下也不算问题），反过来指定的 channel 没装则直接判失败，因为这条命令确实起不来；装上的版本号与源码不一致只是警告，但会提示重装，否则 `--json` 里那个 `version` 会一直是安装那一刻的旧号；体检本身不会创建任何目录——路径打错了不该在磁盘上留下空壳，所以它探测的是最近的已存在上级目录，并如实写明「目录还不存在，但上级可写」。

环境没问题之后再用 `--self-check` 去碰网络。

## 更多用法

不想读参数表就先看 `--recipes`：十七条可以直接复制的完整命令。**第一条就是「抓一个主题」**——列表开头摆三条体检命令，等于回答没人问过的问题；抓取之后才是各种检查，再往后大致按「越靠后越贵」排。什么都不传时报错后也会列出前三条，所以从错误信息里抄一条就能开始抓。

```sh
$ scholar-crawler --recipes
1. Collect one topic — start here
   $ scholar-crawler -q "graph attention networks" -p 3 -o out/gat.jsonl
     3 pages, 10 records each, about a minute; clear any challenge in the window it opens
2. Check that this machine can run a crawl at all
   $ scholar-crawler --doctor
     no requests; reports Python, the libraries, the browser and the directories
...
```

这些命令由测试保证仍然可用：每条都会被真实解析器解析、能构造出抓取目标，带 `--dry-run` 的那条会被真的跑一遍。参数被改名或写错时测试会先失败，而不是让你复制到一条跑不起来的命令。

```sh
# 关键词检索，抓 3 页（每页 10 条）
scholar-crawler -q "large language model agents" -p 3 -o out/agents.jsonl

# 限定年份 + 按时间排序，最多 40 条
scholar-crawler -q "retrieval augmented generation" \
  --year-from 2023 --sort-by-date -n 40 -o out/rag.jsonl

# 批量查询（文件内一行一个，# 开头为注释，见 queries.example.txt），断点续爬
# 多个目标时日志里每条会标出进度：[query] 3/12 '...' from offset 0
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
scholar-crawler --author kukA0LcAAAAJ -o out/bengio.jsonl
scholar-crawler --author "https://scholar.google.com/citations?user=kukA0LcAAAAJ&hl=en" --sort-by-date -p 2

# Scholar 高级语法直接写进 query
scholar-crawler -q 'author:"Yoshua Bengio" source:"NeurIPS"' -p 2
```

## 给程序调用：`--json`

这个工具越来越常被 AI agent 调用：它要的不是终端里的进度，而是一次调用、一个可解析的结果。`--json` 把 stdout 让给一个 JSON 对象，所有给人看的行改走 stderr，所以 `json.loads(stdout)` 永远成立：

```sh
$ scholar-crawler -q "graph attention networks" -p 1 --json 2>/dev/null
{
  "tool": "scholar-crawler",
  "version": "0.2.0",
  "ok": true,
  "exit_code": 0,
  "counts": { "records": 10, "duplicates": 0, "requests": 1, "takeovers": 0 },
  "files": { "records": "out/results.jsonl", "state": "out/state.json" },
  "records": [ { "title": "...", "cluster_id": "...", "cited_by_count": 1234, "...": "..." } ],
  "error": null
}
```

八个顶层键固定不变：`tool`、`version`、`ok`、`exit_code`、`counts`、`files`、`records`、`error`。几条约定：

- **`records` 直接给记录本身**，调用方不用再去读文件；文件照旧会写，`files` 里列出路径。
- **`--dry-run --json` 只算账不发请求**，多一个 `plan`（`page_loads`、`records_at_most`、`seconds`、`cooldowns`、`targets`），这是让 agent 先估价再决定的入口。
- **失败也是一个文档**：`error` 是 `{kind, message, next_steps}`，`kind` 取自一份封闭的词表（`challenge_unattended`、`rate_limited`、`unknown_layout`、`connection_refused`……），调用方可以直接 `switch`。词表由测试保证：代码里写出一个词表外的 `kind` 会直接抛错。
- **报告类模式与 `--json` 互斥**：`--doctor`、`--recipes`、`--self-check` 本身就是给人读的报告，配上 `--json` 只会承诺一个不存在的结果，所以直接拒绝（并且拒绝本身也是一个 `unsupported_mode` 文档）。

`scholar-digest --json` 同理，另加 `overview`（记录数、被引总数、年份、期刊、被引最高）和 `--since` 的 `delta`（新增、不在了、被引变动、净增），也就是「这轮和上次比变了什么」——不用重抓。

最重要的一条给 agent 的约定是**验证码只能交给人**：`--headless` 下遇到验证会以 `challenge_unattended` 结束，正确的反应不是重试更狠，而是把这件事交给人处理一次。完整的调用约定写在 [AGENTS.md](AGENTS.md)（一页），它是给程序读的，人读 README。

## 汇总已抓到的结果（不发请求）

多次断断续续地抓，结果会散在好几个 JSONL 里。`scholar-digest` 只读本地文件，做合并去重、过滤、统计和导出：

```sh
# 合并多份结果，去重后写成一份，并导出 CSV
scholar-digest out/*.jsonl -o out/all.jsonl --csv out/all.csv

# 只要 2018 年以后、被引 1000 以上的
scholar-digest out/all.jsonl --min-citations 1000 --year-from 2018 -o out/hot.jsonl
```

不带写文件参数时只打印一份概览：

```sh
$ scholar-digest out/all.jsonl
[in] 38 records from 1 file(s), 3 duplicates merged, 0 filtered out
  records          38
  citations        104392 total
  bibtex keys      7
  citation-only    2
  unknown year     1
  years            2024:3, 2023:9, 2022:7, 2021:6, 2020:5, 2019:4, 2018:3
  graph levels     L0:20, L1:18
  venues              6  Advances in neural information processing systems
                      4  ICLR
                      3  arXiv preprint
  most cited        41135  2018  Graph attention networks
                     8204  2019  Heterogeneous graph attention network
                     3205  2019  Kgat: Knowledge graph attention network for recommendation
```

`graph levels` 只在集合里真的有 `--follow-cites` 抓来的记录时出现（`L0` 是直接搜到的，`L1` 是它们的被引），`citation-only` 是 Scholar 上只有引用信息、没有页面的那些。

同一篇论文在多份文件里重复时，保留被引数更高（也就是更新）的那条，字段更全的那条优先，`extra` 里的 `bibtex_key` 不会丢，`follow_depth` 取最浅的一层。

`--help` 按「选什么记录 → 在终端里看 → 写成文件」三组排列，每组开头一句话说明什么时候用它。

**读哪些文件**

| 参数 | 作用 |
| --- | --- |
| `FILE ...` | 直接列出要读的 JSONL |
| `--collection DIR` | 把目录当作一个文献库：读其中所有 `.jsonl`，自动排除这一轮要写的文件 |
| `--since FILE` | 和上一次的合并结果比：新增了什么、哪些不在了、被引数怎么动的 |

**选什么记录**（下面所有报告与文件都只覆盖这批）

| 参数 | 作用 |
| --- | --- |
| `--min-citations`、`--year-from`、`--year-to` | 过滤条件；带年份区间时会丢掉没有年份的记录 |

**在终端里看**（不写任何文件）

| 参数 | 作用 |
| --- | --- |
| `--top` | 每个终端列表列出几条：高被引、最旧、集合内被引（默认 5，`0` 表示只要统计不要列表） |
| `--group-by`、`--groups` | 按 `author`/`venue`/`year`/`level` 分组；最多列出几组（默认 10） |
| `--audit` | 体检字段：可疑值与缺失率，分 error/warn 两档 |
| `--network` | 报告记录里已有的引文网络：谁引用了谁、连通分量、孤立记录 |
| `--stale [天数]` | 报告数据有多旧，并按「最可能变了」排序 |

**写成文件**（交给别的工具：表格、LaTeX）

| 参数 | 作用 |
| --- | --- |
| `-o`、`--csv` | 写出合并后的 JSONL / CSV（表格导出只在这里） |
| `--bibtex` | 离线拼出参考文献文件（不发请求） |
| `--report`、`--report-title`、`--report-top` | 输出一份可读的 Markdown 综述；正文列多少条（默认 15） |
| `--refresh-list`、`--refresh-limit` | 写出该重抓的 cluster id 清单 |
| `--quiet` | 只打印写出结果，需要配合任一写文件的参数 |

`--top` 只管终端里的列表，`--report-top` 只管 Markdown 综述——把终端列表调短，不会连带把写出的综述也削短。

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

其他细节：已经在抓取时导出过的记录会沿用同一个 key（两份文件指同一篇论文时名字一致）；`Veličković` 这类会正确转写成 `velickovic`（`ł`、`ø`、`ß`、`æ` 也都处理）；key 撞车时追加 `a`、`b`；venue 里出现 Proceedings/Conference/Workshop 的记为 `@inproceedings` 并用 `booktitle`，没有 venue 的记为 `@misc`；题名用双花括号包住，避免某些样式把大小写压平；`&`、`%`、`$`、`#`、`_` 会转义，`^` 与 `~` 也会——前者在正文模式下直接让 LaTeX 报错，后者会悄悄变成一个不换行空格。没有题名的记录会被跳过并计数。

### 出一份可读的综述：`--report`

JSONL 和 CSV 是给程序看的，终端汇总会滚走。一次文献检索最后真正要交出去的东西是文字，所以 `--report` 把合并后的记录写成一份 Markdown 概览，可以直接贴进综述初稿：

```sh
scholar-digest out/*.jsonl --report out/report.md --report-title "图注意力网络：初步梳理"
```

包含：一眼看完的规模（记录数、总被引、年份跨度、期刊数、第一作者数）、高被引清单（标题带原始链接）、按期刊/会议与按第一作者的两张分组表（记录数、总被引、中位数、年份跨度、代表作）、逐年分布的文本柱状图（复制粘贴不会坏）、这些记录分别来自哪个查询，最后是一节「这份报告有多可信」——直接复用 `--audit` 的检查结果，把缺失率和可疑字段摊开写。

真实片段（20 条记录，`--report-top 3` 只是为了在这里贴得短一些）：

```markdown
# 图神经网络综述：初步梳理

Built from 20 records collected with [google-scholar-crawler](...). Every number below comes
from what Scholar showed when the records were collected; nothing was re-fetched.

## At a glance

- **20 records**, 38,514 citations in total
- published **2020–2026**
- **17 venues**, **20 first authors**

## Most cited works (top 3)

| Citations | Year | Work | Venue |
| --- | --- | --- | --- |
| 17,842 | 2020 | [A comprehensive survey on graph neural networks](...) | … on neural networks … |
| 10,569 | 2020 | [Graph neural networks: A review of methods and applications](...) | AI open |
| 2,576 | 2022 | [Graph neural networks in recommender systems: a survey](...) | ACM computing surveys |

## When it was published

2022  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 5
2023  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 5
2024  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 4

## How much of this to trust

| warn | venue truncated | 12 (60%) | Scholar elided the venue, ... |
| warn | authors truncated | 12 (60%) | Scholar elided the author list, ... |
```

报告开头写明「所有数字都来自抓取当时 Scholar 显示的内容，生成报告没有重新请求」，避免读者把它当成实时数据。

标题里的 Markdown 记号会被转义：`*SEM 2021`、`C*-algebras`、`[Re] ...`、`word2vec_extended` 这些是真实存在的题名，不转义的话渲染器会把它们变成斜体、代码块或断掉的链接——报告里就出现了一个从没被抓到过的题名。链接的目标网址用尖括号包住（`[题名](<网址>)`），因为 Scholar 的网址里带括号和逗号，不包会让链接提前结束。

### 看清集合内部谁引用谁：`--network`

`--cites X` 这种列表的含义是「这一页上的每条记录都引用了 X」，而每条记录自己的 `cited_by_url` 里就带着它自己的那个 X。所以引文关系其实早就存在 JSONL 里了——不用多发一个请求，之前几轮抓的老数据也照样能算。

```sh
$ scholar-digest out/graph.jsonl --network
  38 records and 0 uncollected works, 28 edges
  10 component(s), largest 11 works; 7 record(s) neither cite nor are cited here
  most cited from inside this collection:
      10 here     41,135 on Scholar  Graph attention networks
       9 here      4,408 on Scholar  Heterogeneous graph attention network
```

「in this collection」是关键：`10 here` 指这 38 条里有 10 条引用了它，`41,135 on Scholar` 是全网被引数。前者才说明它在**你这个主题范围内**有多中心。

一个如实说明的边界：用 `--cites <id>` 直接起抓时，被引的那篇本身并不在集合里，它会以 `uncollected work <id>` 计入——否则「一条边都没有」会看起来像坏了。一篇论文可能出现在多个 `--cites` 列表里，所以统计边时用的是**合并前**的全部观测，节点用合并、过滤后的集合，边不会因为去重而丢。

关键词检索出来的集合没有引文边，这时它会直接说清楚，而不是画一张空图。

### 把一个目录当作文献库：`--collection` 与 `--since`

抓上几周之后，一个课题的产出就是 `out/` 下的一堆文件，靠人记住「哪个是哪次抓的、上次合并的结果是哪个」。`--collection` 让目录本身成为单位，`--since` 回答「和上次比变了什么」：

```sh
$ scholar-digest --collection out --since out/merged.jsonl -o out/merged.jsonl
[in] 11 records from 2 file(s), 3 duplicates merged, 0 filtered out
  ...
  6 works since out/merged.jsonl -> 8 now: 2 new, 0 no longer here, 2 with a new citation count
  citations gained across the works in both: +41
  biggest movers:
    +    40  now      140  Work 0
    +     1  now      111  Work 1
  new:
    Work 6
    Work 7
[out] 8 records -> out/merged.jsonl
```

这里有一个容易中招的坑，`--collection` 专门为它而存在：`scholar-digest out/*.jsonl -o out/merged.jsonl` 跑第二遍时，`out/*.jsonl` 已经**包含上次写出的 merged.jsonl**。去重让它看起来没事，但「几个文件、多少重复」的统计从此没有意义，而一个只读回自己上次结果的库看起来永远是完整的。`--collection` 会把这一轮要写的文件（`-o` 与 `--since`）从输入里排除，上面那行 `11 records from 2 file(s)` 就是证据——目录里有三个 `.jsonl`，只读了两个。

`--since` 的比较口径和抓取时的去重完全一致（同一个 `record_key`），所以被引数、期刊、摘要变了仍算同一篇。三类结果各自的含义：

- **new**：这次输入里有、上次合并里没有的。
- **with a new citation count**：两边都有但数字变了，按变化幅度绝对值排序。**降**也会如实报（`-32`）：Scholar 自己会下修被引数。注意合并规则是「同一篇在多份输入里出现时保留被引数更高的那条」，所以只有当前输入确实报了更小的数字时才会看到降。
- **no longer here**：上次有、这次没有。这**不是** Scholar 删了论文，而是那条记录所在的文件被移走了，或者当前的过滤条件（`--min-citations`/`--year-from`）把它排除了——输出里就直接这么写着，免得被误读成数据丢失。

什么都没变时只有一行：`nothing changed since out/merged.jsonl: the same 20 works, same counts`。

和上一节的重抓闭环拼起来，维护一个库就是三条命令，不需要人脑记账：

```sh
scholar-digest --collection out --stale 60 --refresh-list out/refresh.txt   # 离线：该重抓哪些
scholar-crawler --clusters-file out/refresh.txt -p 1 -o out/refresh-1.jsonl # 每条一次加载
scholar-digest --collection out --since out/merged.jsonl -o out/merged.jsonl --min-citations 1
```

目录里可以混着别的文件：只有 `.jsonl` 会被读，子目录不递归。命令行上再补几个文件也行，它们接在目录之后。

### 维护一个持续更新的文献库：`--stale` 与 `--refresh-list`

每条记录都带 `fetched_at`（抓取时刻，UTC），所以「这批数据有多旧」是离线就能算的。被引数会一直涨，抓过三个月的记录里那个数字已经不能引用了。

```sh
$ scholar-digest out/*.jsonl --stale 60 --refresh-list out/refresh.txt --refresh-limit 5
  20 records, collected between 475 and 0 days ago
  17 older than 60 days (85% of the set)
  17 of those can be re-listed by id, one page load each; 0 would need their query re-run
    375d      3,205 citations  --cluster 16121581283781234537 Kgat: Knowledge graph attention network…
    475d        203 citations  --cluster 13239932653767095002 Crystal graph attention networks…
[out] 5 id(s) to re-list -> out/refresh.txt (of 17 records older than 60 days)
```

排序不是单纯按年龄：被引 3 次的论文放一年数字也不会动，被引四万次的放两个月就差了几百。所以权重是「年龄 × log(被引数)」——把数字真的变了的排在前面。这只是给人排个序，不假装能预测新的被引数。

刚抓完一轮时，所有记录的 `fetched_at` 是同一刻，年龄区分不了任何东西，这时排序退化成「被引数从高到低」。报告会自己说出这件事（`all the same age, so this order is by citation count, not by what moved`），免得那句「把变了的排在前面」被当成年龄参与了排序。

`--refresh-list` 写出的文件就是 `scholar-crawler --clusters-file` 读的格式，一进一出闭环：

```sh
scholar-digest out/*.jsonl --stale 60 --refresh-list out/refresh.txt   # 离线，选出该重抓的
scholar-crawler --clusters-file out/refresh.txt -p 1 -o out/new.jsonl  # 每条一次页面加载
scholar-digest out/*.jsonl out/new.jsonl --min-citations 1 -o out/library.jsonl
```

第三条为什么要 `--min-citations 1`：`--cluster` 列的是「这篇的所有版本」，除了正主之外还会带回同一篇的镜像、预印本等版本行——它们没有 `data-cid`、也没有被引数。实测 5 次重抓带回 37 条，其中 32 条是这种版本行；`--min-citations 1` 正好把它们滤掉，剩下 20 条正主。

合并时也做了一处修正：以前只保留「更富」的那条记录，现在胜者缺的字段会从另一条补上。重抓回来的记录被引数更新、但版本列表里没有摘要，如果整条替换就会把已经抓到的摘要丢掉。

### 体检已抓到的数据：`--audit`

Scholar 的结果卡片只有一行灰字承载「作者 - 期刊, 年份 - 站点」，解析靠位置切分：常见卡片没问题，剩下的会静默出错——venue 实际上是页码范围、year 来自期刊名里的数字、作者列表被 Scholar 自己截断。下游不会察觉，`--group-by year` 照样按错的年份分组。

`--audit` 只读本地文件，把「已经抓到的数据有多不可信」量出来：

```
$ scholar-digest out/g.jsonl --audit
  audit of 20 records: 2 checks tripped (0 errors, 2 warnings)
    warn  venue_truncated               12  60.0%  Scholar elided the venue, so a bibliography would cite '… on neural networks …'
        e.g. … on neural networks … | A comprehensive survey on graph neural networks
        e.g. … Computing Surveys … | Computing graph neural networks: A survey from algo…
    warn  authors_truncated             12  60.0%  Scholar elided the author list, so BibTeX gets 'and others'
        e.g. Z Wu, S Pan, F Chen, G Long… | A comprehensive survey on graph neural networks
        e.g. J Zhou, G Cui, S Hu, Z Zhang, C Yang, Z Liu, L Wang… | Graph neural networks: A review…
```

数据干净时它只有一行，不必费神读：

```
$ scholar-digest out/clean.jsonl --audit
  audit of 10 records: nothing implausible found
```

分两档：`error` 是值本身错了（年份不在合理区间、年份在原始灰字里根本没出现过、venue 是卷期页码、venue 里还留着年份、有被引数却没有被引链接、计数为负、标题缺失），`warn` 是缺失或有损（缺 venue/year/作者、作者被截断、**venue 被 Scholar 省略**、venue 是裸域名、标题带 `[PDF]` 标签、没有 card id）。作者主页抓来的记录不算「没有 card id」——Scholar 的主页行本来就不带 `data-cid`，导出时用记录里的 profile id 多花一次页面加载即可解决，把它记成缺陷会让一份作者集合看起来 100% 有问题、而且指向一个并不存在的修法。每项给出条数、占比、以及两个真实例子——不是给个总分，而是让你自己判断这批数据能不能用。

上面那份 60% 是真实数字：Scholar 的结果页会把长刊名两头都省掉（`… on neural networks …`），检索页上拿不到完整刊名，导出的 BibTeX 里 `journal` 就会照抄这个省略形式。被省略的刊名在统计与分组里**保留那个省略号**（`IEEE Transactions on Knowledge and Data …`）：去掉它就等于报出一个并不存在的期刊名——真实的那个是 `… and Data Engineering`。这不是解析 bug，也没法在本地补全，所以 `--audit` 的责任是把它数清楚、指给你——要正规引用就照着这份清单去手工补，或者用 `--bibtex` 从 Scholar 的 Cite 弹窗拿完整条目（每条多两次页面加载）。

这个功能第一次跑就抓到一个真实缺陷：作者主页解析出的 venue 保留了年份（`Advances in neural information processing systems 27, 2014`），而检索页解析是剥掉年份的。分组统计侥幸没受影响（`normalize_venue` 会切掉卷期尾巴），但 JSONL/CSV 里两种来源的字段不一致，导出的 BibTeX 里 `journal` 也重复带上了年份。现在两个解析路径共用同一个剥离函数。

### 抓取时的静默体检

事后跑 `--audit` 能发现数据变质，但那时页面已经抓完了。所以每次运行会对**本轮新写入的**记录顺带做同样的检查（逐条累加计数，不占内存），平时一句话都不说，只在某项 error 级检查同时满足「≥3 条」且「≥20%」时才在运行摘要后面出声：

```
[out] 40 new records (0 duplicates skipped) -> out/results.jsonl
[run] 5 requests in 1m, 0 takeovers, 0 navigation retries, delay now 4.0-11.0s
[audit] 1 field(s) parsed badly for a large share of this run's records — Scholar's layout may have changed
[audit]   venue_looks_like_pages: 16 of 40 records (40%) — venue is a volume, issue or page range, so venue grouping is wrong
[audit]       e.g. 521 (7553), 436-444 | Deep learning
[audit] run --self-check to test the parser, or scholar-digest --audit for the details
```

阈值是为了不喊狼来了：单条奇怪的记录（Scholar 上确实有）不触发，只有「一个字段在这一轮里大面积解析失败」才触发——那通常意味着版式变了。缺失类的 `warn`（Scholar 自己就没给 venue、自己截断了作者）永远不会触发，因为那不是解析错误。

### 分组统计

`--group-by` 把合并后的结果按第一作者、期刊/会议、年份或引文层级分组，按被引总数排序：

```
$ scholar-digest out/all.jsonl --group-by venue --groups 4
  by venue                                 count  citations  median  years      most cited
    Advances in neural information processi…    1     119743  119743  2014       Generative adversarial nets
    nature                                       1     118913  118913  2015       Deep learning
    arXiv preprint                               2      44564   22282  2017-2021  Graph attention networks
    The world wide web                           1       4408    4408  2019       Heterogeneous graph attention network
    ... and 4 more groups
```

`median`（组内被引中位数）是为了公平比较：某组只靠一篇爆款撑起来，还是整体都被引得多，看中位数才分得清。

分组时会做两处归一化，否则同一个去处会被拆散：所有 arXiv 预印本归为 `arXiv preprint`（Scholar 会把 arXiv 编号写进 venue），作者主页那种 `nature 521 (7553), 436-444, 2015` 会去掉卷号页码归为 `nature`；大小写不同也算同一组（显示时保留先出现的写法）。默认概览里的「出现最多的期刊」现在也用同一套归一化。

## 接管记录

人工接管是这套工具里最少见、最关键、也最不可复现的一步：它发生时你正忙着解验证码，终端里滚过去的信息事后就找不回来了。所以每次接管都会追加一条记录（默认 `out/challenges.jsonl`），`--show-state` 会一并读出来：

```sh
$ scholar-crawler --show-state
[state] 3 targets in out/state.json (1 finished)
[state]   attention is all you need [en] — next offset 30, 2026-09-02 10:45:51 UTC
[handoff] 2 takeovers in out/challenges.jsonl (captcha x2)
[handoff]   2026-09-02T12:26:23+00:00  captcha -> unattended, waited 6s (on request 11, loading 20)
[handoff]     matched form#captcha-form at about:blank
```

一条记录有 10 个字段：时间 `at`、类型 `kind`（`captcha`/`rate_limit`/`consent`）、检测器命中的是什么 `reason`、被拦在哪个 URL（`url`，已脱敏）、这轮的第几次请求被拦 `request_index`（算上被拦这一次）、是否连续被拦 `consecutive`（第几次）、等了人多久 `waited`、被拦时正在取哪个目标 `target`、等待期间页面依次变成过哪些验证类型 `saw`（`became sign_in`）、以及结局 `outcome`——`resolved`（人解完了，继续抓）、`unattended`（`--headless` 拒绝或等待超时）、`budget`（用满 `--max-handoffs` 停机）、`interrupted`（Ctrl+C）、`rehearsed`（演练）。

有了这些，事后能回答真正要紧的问题：是抓到第几次请求被拦的、是不是解完一次又立刻被拦（说明当前节奏还是太快）、还是根本没人在电脑前。

**URL 会脱敏后再写入**：`/sorry/` 这类验证页上的 `q` 是验证令牌而不是查询词，所以整条只留 `hl`；普通检索页则保留 `q`、`start`、`cites`、`cluster`、`user` 等描述请求的参数，`scisig` 之类签名参数一律写成 `REDACTED`。所以这个文件可以放心留存和分享。

`--rehearse-handoff` 演练时也会写一条，顺带证明这个日志路径是可写的——不必等真被拦时才发现写不进去。演练走完记 `rehearsed`，但没人理它超时了就记 `unattended`，用 Ctrl+C 结束（文档里推荐的提前结束方式）则记 `interrupted`。所以「是不是演练」看的是 `target` 是否为 `rehearsal`，而不是看结局；`--show-state` 会在这类记录后标上 `(drill)`。否则演练完走开一次，之后每次运行都会被拖慢，还会声称「这个 profile 在第 0 次请求就被拦」。

`--json` 里，只有真发生过接管的那次运行才会在 `files` 里多出一个 `challenges` 指向这个文件：接管是唯一一件程序无法从文档里重建的事（它发生在浏览器窗口里、由人处理），所以文档得指出证据在哪；没被拦过就不列，免得让调用方去读一个不存在的文件。

### 本轮内的减速

自适应减速分两级：每次人工接管后按 `--backoff-factor` 放大延迟；如果**中间没有一次正常加载**就又被拦（说明解完一次并没有恢复信任），则在恢复前先静默等待 `--challenge-cooldown` 秒，第三次连续被拦等两倍，以此类推。超过 `--max-handoffs` 仍然直接中止。

### 跨运行学会减速

被拦一次的经验不该只留在那一轮里。默认每次启动会读一眼接管记录，把上次的教训折进这轮的起始节奏：

```
$ scholar-crawler -q "graph attention networks" -p 5
[pace] 3 previous blocks (captcha x2, rate_limit x1); typically at request 14; 1 arrived back to back; last 2026-09-02T12:37:20+00:00; starting at 6.8-18.7s (x1.7)
```

规则刻意保守，而且**只会放慢，不会加快**——历史能证明某个节奏太快，但没有任何历史能证明更快的节奏是安全的：

- 只被拦过 1 次：不算规律，只提示，不改节奏。
- 被拦 ≥2 次：×1.3；≥5 次：×1.6。
- 出现过「解完一次立刻又被拦」：再 +0.2（说明解验证码并没有恢复信任）。
- 通常在第 30 次请求以内就被拦：再 +0.2（说明问题是节奏，而不是抓得多）。
- 各项加起来最多 ×2.0，这不是一道额外的封顶，而是这几项之和；一条测试穷举所有组合来保证这句话成立。演练记录不算证据，不管它是怎么结束的。

你自己传了 `--min-delay`/`--max-delay` 时，它绝不会覆盖你的选择——只打印历史，并说明「按你传的值跑」。`--no-learn-from-history` 完全关掉这个行为。`--dry-run` 里的用时估算也会用学到的节奏，所以能先看到「这轮会慢多少」。

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

## 把常用参数写进文件：`--config`

同一个课题断断续续抓几周，每次都要重敲 `--min-delay --max-delay --profile --follow-cites --year-from` 这一长串，敲错一个延迟数字就是一次多余的请求。把这些写进一个 TOML 文件，用 `--config` 带上：

```sh
cp scholar.toml.example scholar.toml   # 编辑它
scholar-crawler --config scholar.toml
```

优先级只有一条规则，且不可协商：**命令行 > 文件 > 内置默认值**。所以「同样的设置，换个查询」就是：

```sh
scholar-crawler --config scholar.toml -q "另一个题目" --pages 1
```

`--dry-run` 会说明每个值的来源，「为什么这次延迟是 8 秒」有据可查：

```
[explain] settings file scholar.toml: 5 value(s) in effect
[explain]   cooldown_every, max_delay, out, profile, query
[explain]   min_delay came from the command line instead, which wins over the file
[explain]   pages came from the command line instead, which wins over the file
```

不带 `--dry-run` 的正常运行只打一行 `[config] 5 setting(s) from scholar.toml, 2 overridden by flags`。

写法约定：

- **键名就是长参数去掉前面的横线**，`min-delay` 和 `min_delay` 都认，`"--min-delay"` 也认。
- **`[pacing]` 这类表只是给人分段用的**，程序把表里的键当作写在文件顶层完全一样。所以你可以按自己的习惯组织文件，不必记住哪个参数属于哪一组。
- **可重复的参数写成数组**（`query = ["a", "b"]`）。命令行上再给 `-q` 是**替换**整个列表，不是追加——这正是「同样的设置，换个查询」想要的行为。
- **模式类参数不许写进文件**：`--doctor`、`--self-check`、`--rehearse-handoff`、`--show-state`、`--forget`、`--dry-run`、`--recipes`、`--config` 决定这条命令**做什么**，不是它**怎么做**。文件里出现它们会直接报错——一个设置文件不该在你不知情时把抓取变成别的动作。

任何不对的地方都在发出第一个请求前报错，而且指名道姓：

```
error: scholar.toml: unknown setting 'min_dely'; did you mean 'min_delay'?
error: scholar.toml: 'pages' wants a number, not a string
error: scholar.toml: 'query' wants a list of values
error: scholar.toml: 'headless' wants true or false
error: scholar.toml: 'doctor' decides what the command does, so it stays on the command line
error: scholar.toml: [pacing.deeper] nests too deep; settings are one level
error: scholar.toml: 'min-delay' is set twice
```

Python 3.11 起 `tomllib` 是标准库；3.10 需要 `tomli`（`pyproject.toml` 已按 `python_version < "3.11"` 声明），`--doctor` 会顺便报一行 `settings files`，不必等到真用 `--config` 时才发现读不了。

## 先把命令读回来、并算清账：`--dry-run`

参数有五十多个，写错的组合通常不会报错，只是安静地做了另一件事；而 `--pages`、`-n`、`--follow-cites`、`--bibtex` 的成本是相乘的，很容易一不小心开出一个跑几小时的任务。`--dry-run` 不发任何请求，把这条命令翻译成人话——抓什么、翻多少页、按什么节奏、遇到验证怎么办、会动哪些文件——指出互相抵消或名不副实的参数，最后给出账单：

```sh
$ scholar-crawler -q "graph attention networks" -p 3 --bibtex out/refs.bib --dry-run
[explain] crawling 1 listing(s)
[explain]   target: graph attention networks
[explain] up to 3 page(s) per listing, 10 records a page
[explain] waiting 4–11s between page loads
[explain] pausing 90s every 10 loads, and giving up on a page after 45s
[explain] on a challenge: the window is brought to you, waiting up to 600s for you to clear it, up to 5 time(s) this run
[explain] after each takeover the delays widen by x1.6
[explain] creating records: out/results.jsonl
[explain] creating bibtex: out/refs.bib
[explain] creating resume state: out/state.json
[explain] creating takeover log: out/challenges.jsonl
[plan] graph attention networks -> https://scholar.google.com/scholar?hl=en&q=graph+attention+networks&as_vis=0&as_sdt=0%2C5
[plan] seed targets: 3 page loads, up to 30 records
[plan] bibtex export: up to 60 page loads
[plan] total: up to 63 page loads for 30 records
[plan] estimated 20 min at 4-11s between requests plus 6 cooldowns of 90s
[plan] nothing was requested; drop --dry-run to start
```

会被点出来的组合（`warn` 是「这个参数不做你以为的事」，`note` 是「后果值得知道」）：

- `--headless` 无人可交，第一次验证就会带着已抓到的数据结束；
- `--year-from` 晚于 `--year-to`，Scholar 什么都不会返回；
- `--pages 0`、`--max-handoffs 0` 这类等于「不干活」的值；
- 延迟比默认的 4–11 秒更短、`--cooldown-every 0` 去掉长暂停；
- `--no-learn-from-history`，且接管记录里确实有历史（没有历史就不提）；
- `--resume` 但断点里没有这些目标 → 其实是从头开始；`--resume` 与 `--start` 同时给 → 断点赢（「断点里有而没写 `--resume`」不必等 `--dry-run`，任何一次运行开始前都会说）；
- 两个输出参数指向同一个文件；
- `--bibtex` 配 `--author` 每条要三次页面加载；`--dump-html` 会把含会话信息的页面写到磁盘；`--proxy` 的机房 IP 更容易被拦；`--host` 不是默认站点。

`[explain]` 说的是「这条命令是不是你想写的」，`[plan]` 说的是「它要花多少」——这两个问题原来是两个旗标（`--explain` 和 `--dry-run`），但没人只想知道其中一个：都不发请求、都需要目标、都是「该不该开始这次运行」的一半答案，所以现在是一个模式。

再看一个成本会失控的例子：

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

## 自检

先 `--doctor` 确认本机装好了（见[安装](#安装)），再用自检碰网络。怀疑 Scholar 改版、解析结果变空时，先跑一次自检（只发一次请求）：

```sh
$ scholar-crawler --self-check
[check] fetching one page for 'machine learning'
[check] results_parsed   ok    10 records on the page
[check] titles           ok    10/10 have a title
[check] links            ok    10/10 non-citation records have a link
[check] bylines          ok    10/10 have an author line
[check] years            ok    10/10 have a year
[check] snippets         ok    10/10 have a snippet
[check] card_ids         ok    10/10 carry Scholar's data-cid (needed for BibTeX)
[check] citation_counts  ok    10/10 link their citing works
[check] total_estimate   ok    result count read as 6010000
[check] pagination       ok    next-page link found
[check] all 10 checks passed
```

它抓一页固定的宽泛查询，逐项报告标题、链接、作者行、年份、摘要、`data-cid`、被引链接、结果总数、下一页是否都还能解析出来。全通过退出码 0；任一项变成 `x` 就退出码 1，并提示用 `--dump-html` 把页面存下来对比。上面这份输出是真跑出来的，Scholar 改版时最先变的通常是 `card_ids` 或 `citation_counts`。

## 常用参数

`--help` 开头只列三种运行形态（检索、按 id 抓、离线模式），末尾用四行说清「四个不抓数据的模式各回答什么问题」，而不是把五十多个旗标铺满一屏；完整列表在 `--help` 的分组里，下面这张表是同一批参数按用途的中文对照。

| 参数 | 说明 |
| --- | --- |
| `-q/--query`、`--queries-file` | 关键词检索，可重复；文件一行一个 |
| `--cluster`、`--clusters-file` | 按 cluster id 列某篇的所有版本；文件一行一个 |
| `--cites`、`--cluster` | 抓某文的引证文献 / 全部版本；接受数字 id 或结果里的 `cited_by_url`、`versions_url`，可重复 |
| `--author` | 抓作者主页论文列表；接受 12 位 user id 或主页 URL，可重复；配合 `--sort-by-date` 按年份排序 |
| `-p/--pages`、`-n/--max-results` | 每个入口抓几页 / 最多抓几条（末页精确截断）。检索页每页 10 条，作者主页每页 100 篇 |
| `--follow-cites`、`--follow-breadth`、`--follow-min-citations` | 抓完种子入口后，继续抓「引用它们的文献」若干层；每层只展开被引最多的 N 条，且低于引用下限的直接跳过 |
| `--start`、`--resume` | 起始 offset；从 state 断点继续 |
| `--year-from/--year-to`、`--sort-by-date`、`--review-only` | 年份区间、按日期排序、只要综述 |
| `--no-citations`、`--no-patents` | 排除仅引用条目、排除专利 |
| `--lang`、`--host` | 界面语言 `hl`（浏览器的 `Accept-Language` 跟着它，不另设旗标）；镜像站如 `https://scholar.google.de` |
| `-o/--out`、`--state` | JSONL 输出、断点文件（CSV 交给 `scholar-digest --csv`，抓取本身不导表） |
| `--challenge-log` | 接管记录文件（默认 `out/challenges.jsonl`，URL 已脱敏） |
| `--bibtex` | 同时导出 BibTeX 到 `.bib` 文件；按引用键去重，记录里写入 `extra.bibtex_key` 便于关联 |
| `--dump-html` | 抓到的原始 HTML（排查解析问题用） |
| `--profile`、`--channel`、`--timezone`、`--proxy` | 浏览器 profile 与环境参数 |
| `--min-delay/--max-delay`、`--cooldown-every/--cooldown-seconds` | 抓取节奏（默认 4-11s；不传时会按接管记录自动放慢） |
| `--no-learn-from-history` | 不读接管记录，按默认节奏起跑 |
| `--handoff-timeout`、`--max-handoffs`、`--backoff-factor`、`--challenge-cooldown` | 等人多久（0 = 无限等）、最多接管几次、每次接管后延迟放大倍数、连续被拦时恢复前的静默等待 |
| `--recipes` | 打印可直接复制的完整命令（不发请求） |
| `--config FILE` | 从 TOML 设置文件读参数；命令行给的值优先 |
| `--show-state`、`--forget PATTERN` | 查看断点进度与最近的接管记录；按签名子串清除断点（空串清空全部） |
| `--dry-run` | 把这条命令读回成人话、指出互相抵消的参数、并给出抓取计划与用时估算，不发任何请求 |
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

作者主页的论文写进同一个 JSONL（`extra.citation_id` 记录 Scholar 的 citation id），主页头部写进 `-o` 旁边的 `<名字>.profiles.jsonl`（`-o out/bengio.jsonl` → `out/bengio.profiles.jsonl`，每位作者一行，重复抓取覆盖旧值）——它没有自己的旗标：抓作者主页本来就要解析这段头部，而跟着 `-o` 命名可以避免两次不同的运行悄悄共用一个档案文件：

```json
{"user_id":"kukA0LcAAAAJ","name":"Yoshua Bengio",
 "affiliation":"Professor of computer science, University of Montreal, Mila, IVADO, CIFAR",
 "organization":"University of Montreal","homepage":"https://yoshuabengio.org/",
 "verified_email":"Verified email at umontreal.ca",
 "interests":["Machine learning","deep learning","artificial intelligence"],
 "cited_by_total":1149112,"cited_by_recent":764217,"h_index":259,"h_index_recent":208,
 "i10_index":1106,"i10_index_recent":947,"fetched_at":"..."}
```

## 沿引文网络往外抓：`--follow-cites`

`--follow-cites DEPTH` 会在种子入口跑完之后，把已抓到的记录按被引数从高到低取前 `--follow-breadth` 条，各自打开「被引用次数」列表继续抓，逐层向外。

- 请求数是乘法增长：1 个种子、深度 2、宽度 5 就是最多 31 个列表（每个列表还要按 `-p` 翻页）。启动时会先打印本轮的上限估算。
- 同一个 cites id 在整轮里只抓一次，重复的分支会被跳过；每条记录写入 `extra.follow_depth` 标明它来自第几层。
- 展开出来的列表沿用命令行上的年份、语言、排序等过滤条件，`--resume` 也照常按每个列表的签名记断点。
- 作者主页抓到的论文同样可以作为展开起点。

## 抓取时导出 BibTeX：`--bibtex`

`--bibtex` 每条记录要多走两次页面加载：先打开 Scholar 的 "Cite" 弹窗，再打开弹窗里带签名的 `scholar.bib` 链接（签名参数无法自己拼出来）。因此：

- 一次抓 10 条的页面，开了 `--bibtex` 就是 21 次请求而不是 1 次，整体耗时约慢一个数量级，被验证拦的概率也随之上升。建议配合 `-n` 只对确定要用的结果导出。
- 这两次加载都走可见窗口的正常导航，因此同样受节奏控制和人工接管保护。不能改用后台 HTTP 请求：Scholar 对浏览器导航之外发起的同样请求直接返回 429。
- 作者主页的论文条目没有 Scholar 的 `data-cid`，程序会先用它的 cluster 列表把 `data-cid`查出来，所以每条是 3 次加载而不是 2 次；开头会提示一次。
- 反过来，`scholar-digest --bibtex` 不发请求，用的是已抓到的字段，所以刊名会照抄结果页上的省略形式（`journal = {… on neural networks …}`）。要正式引用就先跑 `--audit` 看 `venue_truncated` 有多少条，再决定是手工补还是花两次加载去拿 Scholar 的原始条目。

## 演练人工接管

真验证码不好按需触发，所以可以先空演一遍整条接管链路（**不发任何请求**，页面是本地生成的）：

```sh
scholar-crawler --rehearse-handoff
```

流程和真遇到验证时完全一致：检测到「验证页」→ 响铃并把窗口提到最前 → 打印接管提示 → 轮询等你操作。页面上有一个按钮，按下就等于「验证已通过」，程序会确认页面恢复成正常内容并报告等待了多久，退出码 0。没人操作时会在 `--handoff-timeout` 到点后报错退出（退出码 1）；加 `--headless` 则会验证「无窗口就拒绝运行」这条路径。

## 运行摘要

每轮结束（正常结束、Ctrl+C 中断、出错退出都一样）会打印一行运行摘要：

```
[run] 12 requests in 3.4 min (3.5/min), 1 takeover (captcha x1), 0 navigation retries, delay now 6.4-17.6s
```

请求数含 cite 弹窗与 BibTeX 导出；接管次数按类型分列；`delay now` 是退避之后的当前节奏——如果它明显比初始值大，说明这轮被拦过，当前 IP 或节奏需要更保守。运行不到 30 秒时只报秒数，不报速率，因为那时的速率主要反映启动开销。

## 出错时给人话

程序停下来的时候，最没用的信息是把 Playwright 的调用日志原样倒出来。每种失败都会翻译成「发生了什么 + 下一步做什么」，必要时附上原始错误：

```
$ scholar-crawler -q "graph attention networks"

[stop] the host refused the connection, so nothing was crawled (https://scholar.google.com/scholar?...)
[stop] try: open the same address in a normal browser: if that fails too, the network is blocking it
[stop] try: check --host if you pointed it somewhere other than scholar.google.com
[stop] try: check whether a VPN, firewall or corporate proxy is in the way
[stop] underlying error: Page.goto: net::ERR_CONNECTION_REFUSED at https://scholar.google.com/...
```

而且不再白等：连接被拒、DNS 解析不了、证书被拒、代理拒绝这类「重试一百次也一样」的失败立刻停下（以前会重试三次、白等 15 秒），只有超时、连接被掐断、断网这类可能是暂时的才重试。

区分开的情况：连接被拒 / DNS 解析不了 / 本机断网 / 代理拒绝 / 连接被中途掐断（自动化流量常见的下场，建议放慢重试）/ 证书被拒（通常是有东西在中间解 TLS，或本机时钟不对）/ 加载超时（`--nav-timeout` 可调）/ 浏览器窗口被提前关掉 / Scholar 返回 429 或 503（这是拒绝服务，不是 bug：停一段时间再 `--resume`）/ 其他 4xx-5xx / 页面能打开但不含任何 Scholar 标记。原始错误只保留第一行，调用日志不再糊满屏幕；猜错了也还能看到它。

一个重要的行为修正：以前「页面打开了但一个 Scholar 标记都没有」会被当成「这个查询没有结果」——于是一个门户认证页、一个陌生版式，看起来都像是搜了个没人写过的题目，程序还会继续往下翻页。现在这种页面直接停下来报 `--self-check` 与 dump 路径。真正的零结果页仍然是零结果：Scholar 自己的「did not match any articles」提示就是内容，照常解析成 0 条。

## 降低验证频率

- 别调小默认延迟；被封的主因是节奏，不是 User-Agent。
- 一个 profile 只做一件事，长期复用，不要每次删掉 `.scholar-profile`。
- 单次运行页数控制在几十页以内；大批量拆成多天。
- 需要稳定大规模元数据时，优先用有正式 API 的来源（Semantic Scholar、OpenAlex、Crossref），把本工具留给 Scholar 独有的检索与被引数据。

## 开发

```sh
python3 -m pytest -q     # 511 个用例，全部离线
ruff check .             # 与 CI 相同的 lint 配置
```

测试全部离线（不发任何网络请求）。CI 在 3.10 与 3.13 上跑同样两条；另外在一个全新的 venv 里从这个 git URL 装一遍再跑，验证的是「新装用户拿到的依赖版本」——最近一次是 playwright 1.62.0、bs4 4.15.0、lxml 6.1.3，比开发机上的都新，整套用例全过。按覆盖面分组，细节直接读 `tests/`：

- **解析**：结果卡片与作者主页的每个字段，加上四份真实页面夹具（`tests/pages/`），保证解析既对又贴合 Scholar 的真实结构；那两份真实页面解析出的 9 条记录还要喂给 `--audit`、概览、`--network` 与 `--stale`——报告的责任是评判真实数据，所以它们必须先在真实数据上站得住（0 个 error，警告只针对 Scholar 自己做的事）。`--refresh-list` 写出的文件还要真的被 `scholar-crawler --clusters-file … --dry-run` 读回去，把「一进一出闭环」这句话跑成测试
- **抓取循环**：翻页、作者分批、节奏与冷却、连续被拦后的静默等待、HTML dump、运行摘要
- **人工接管**：真实 headless Chromium 上的验证判定、等待与超时、窗口被关、headless 拒绝、接管记录与跨运行减速
- **全链路**：真实浏览器打本地假 Scholar（`tests/fakescholar.py`）——翻页上限、遇验证接管后不丢数据、`--resume` 续抓、作者主页落盘、headless 拒绝时已抓数据仍在盘上、一次完全由设置文件描述的运行
- **失败诊断**：九类网络错误各自归类、只重试可能是暂时的、认不出的错误保留原文并仍给下一步
- **给人的输出**：`--doctor`、`--dry-run`、`--recipes`、`--audit`、`--report` 各自说的话，以及计划数字与真实请求数逐一对齐
- **给程序的输出**：JSON 文档的固定键、失败词表、stdout/stderr 分工，以及 AGENTS.md 与词表逐词一致
- **离线工具**：合并去重、过滤、统计、分组、书目生成、判旧与重抓清单、文献库差异
- **配置与界面**：设置文件的等价与报错、两条命令的参数表（分组、说明、默认值）、两份 README 的链接与模块清单
- **文档里的命令**：两份 README 与 AGENTS.md 里的每一条 `scholar-crawler`/`scholar-digest` 命令，凡是不发请求的都在临时目录里真跑一遍（读取的文件由测试自己造）；剩下的必须在测试里登记「为什么离线跑不了」（发请求抓取、`--self-check` 一次请求、`--install-browser` 要下载、`--rehearse-handoff` 要人）。新加一条命令逃不掉这两条中的任何一条
- **文档里贴出的输出**：`--recipes` 与 `--dry-run` 的输出只由命令行决定（不看机器、不看数据），所以文档里贴的每一行都逐字、按顺序比对；`--doctor` 与 `scholar-digest` 概览这类值会变的报告，比对的是标签列（`browser`、`profile`、`citation-only`、`graph levels`……），改名或删掉一项就会让文档变红；此外文档里出现的每个 `[标签]` 都必须是工具真的会打印的通道

### 检查守卫是否真在守

一条永远不会失败的测试，和一条不可能失败的测试，从外面看是一样的。`tests/mutate.py` 保存了一批**故意写错**的改动，每条指定「改哪个文件的哪一行、改成什么、哪些测试必须因此失败」：

```sh
python3 -m tests.mutate          # 62 条，约 4 分钟；跑完自动把文件改回去
python3 -m tests.mutate --all    # 连那条会让测试真的等超时的一起跑（慢十分钟）
python3 -m tests.mutate offset   # 只跑标签里含 offset 的
```

它会修改源文件再改回来，所以别在有未保存改动的树上跑。有测试没抓住的项会在最后列出来，退出码 1。改完必须清掉 `__pycache__`：把 `0.2` 换成 `0.6` 这类改动字节数不变，而恢复往往发生在同一秒内，Python 会认为旧的 `.pyc` 仍然有效，于是下一次跑读到的是错的字节码——好测试看着像坏的，坏测试看着像好的。

这批清单是几轮审计攒出来的，一共抓出八处真实漏洞：`--min-citations` 的边界是偶然正确的、判旧清单的长度没人管、「崩溃不丢数据」全靠一个没人检查的 `flush()`、`argparse.SUPPRESS` 能骗过「每个旗标都有说明」、结果页选择器没人保证还认得 Scholar、`as_sdt=0` 是 `as_sdt=0,5` 的子串所以专利开关反了也没人管、终端响铃那一行本身从没被执行过、以及体检告警的比例阈值只有数量阈值在起作用。

交付物则拿本项目之外的解析器验过一遍——`bibtexparser` 读 `refs.bib`、`markdown-it-py` 渲染 `report.md`、标准库 `csv` 回读 `rows.csv`，用的是真抓下来的 10 条 `C*-algebras` 记录：条目全部解析、key 不重复、10 个题名在渲染后一字不差、CSV 往返一致。这两个包**不是**本项目的依赖，只是审计时临时装的；跑一次的命令是 `pip install bibtexparser markdown-it-py`。这样验出的两处问题（题名里的 Markdown 记号、BibTeX 里的 `^` 与 `~`）现在都有离线测试守着。

两条方法论也是踩出来的，现在写进了工具本身：破坏点必须在文件里**只出现一次**（否则可能改到 docstring 里，得出「测试无效」的假结论），且破坏必须真的落到文件上——文本对不上时 `audit()` 直接报错，而不是安静地跑一遍全绿的测试。前一条由 `check_table()` 强制，并由一条测试在 CI 里跑（审计本身会改源文件，不能进测试套件）。

### 真实结构回归夹具

`tests/pages/` 里放着四份**真实抓取页面**的脱敏副本（结果页、作者主页、cite 弹窗、BibTeX 导出页）。手写夹具能证明解析逻辑对，但证明不了它还贴合 Scholar 的真实结构；这几份夹具补的就是这块，而且离线跑：结果页那份会把 `--self-check` 的 10 项检查完整跑一遍。

脱敏由 `tests/sanitize.py` 完成，规则是「结构全留，凭证全去」：删掉 `<script>`/`<style>`/`<iframe>`，图片 `src` 换成 `about:blank`，签名与会话参数（`scisig`、`xsrf`、`scisdr`、`usg`…）无论在 URL 里还是嵌在 `continue=` 这类编码参数里都替换成 `REDACTED`，隐藏表单里的 xsrf 值同样替换，`Verified email at ...` 改写成 `example.edu`，重复卡片裁到几条。解析器要用的 class 名、`data-cid`、`cites=`/`cluster=` 链接、`scisf=4` 这类标记一个不动。另有一条测试专门扫这四份文件，确保里面没有 `<script>`、没有未脱敏的长 token。

Scholar 改版时刷新夹具：

```sh
scholar-crawler -q "graph attention networks" -p 1 -n 2 --bibtex out/x.bib \
    --dump-html out/dump -o out/d.jsonl
python3 -m tests.sanitize out/dump/<结果页>.html tests/pages/results.html 6
```

### 全链路测试：本地假 Scholar

单元测试把 HTML 字符串喂给解析器，`--self-check` 需要真网络，两者都没覆盖最要紧的那条路：**真实浏览器**导航真实 URL、撞上验证页、接管后恢复、把文件写对。`tests/fakescholar.py` 用 `http.server` 在本地回环起一个假 Scholar，只答 `/scholar` 和 `/citations` 两条路径，可以指定某个 offset「第一次被请求时返回验证页」；测试里的替身「人」处理方式和真人一样——把页面重新加载一次，验证消失，抓取继续。

于是这些以前只能在真 Scholar 上验证一次的行为，现在每次 CI 都跑：翻页到页数上限（不多请求一页）、20 条记录都在盘上、断点写到 40、遇验证 → 接管 → 同一 offset 重取 → 一条不丢、接管记录里 `resolved` 与 URL 脱敏、`--resume` 从 20 接着抓到 40、作者主页与档案落盘、干净数据不触发体检报警，以及 headless 下拒绝接管时**已抓到的 10 条仍在盘上**、退出码为 1、断点停在 10；Ctrl+C 打在两页之间时退出码 130、`interrupted`、已抓到的 10 条仍在盘上、断点停在 10；一批查询里第二条撞上读不懂的页面时，第一条的 20 条与它自己的断点都还在（失败那条的断点仍是 0，`--resume` 会重试它），日志里两条目标分别标着 `1/2`、`2/2`；还有一条完全由设置文件描述的运行——查询、页数、host、profile、输出路径全从 TOML 读出，命令行只有 `--config`——抓到同样的 10 条。

## 合规

Google Scholar 的服务条款不允许自动化抓取，抓到的数据版权归原出版方。请仅用于个人研究规模的检索，遵守目标站点的条款与 `robots.txt`，不要转售或再分发抓取结果。工具刻意保留低速节奏与人工接管，就是为了不把它变成规模化滥用手段。

## 结构

```
scholar_crawler/
  models.py     记录与请求的数据结构（检索请求、结果、作者主页、页结果）
  urls.py       查询/主页 URL、过滤参数、id/URL 解析
  parser.py     结果页与作者主页 HTML → 结构化记录
  challenge.py  验证页判定 + 人工接管等待
  diagnose.py   失败诊断：把网络与页面故障翻译成下一步动作
  browser.py    持久化 profile 的浏览器会话
  crawler.py    抓取循环：节奏、接管、翻页/分批、BibTeX 取用、HTML dump
  run.py        一次运行的执行：开浏览器、目标抓取、图展开、输出文件开关与汇报
  modes.py      替代抓取的五种模式：环境体检、自检、演练、查看断点、清除断点
  doctor.py     环境体检：依赖版本、浏览器、目录权限
  expand.py     引文网络展开：选点、上限、去重
  explain.py    把命令读回成人话，并指出互相抵消的参数
  plan.py       抓取计划：页数/加载数/用时估算
  selfcheck.py  解析自检：逐字段体检与报告
  rehearsal.py  接管演练：本地验证页与全链路空演
  history.py    接管记录 → 起始节奏建议
  recipes.py    可直接复制的完整命令
  collection.py 把目录当作一个文献库：输入发现、与上次合并的差异
  digest.py     离线汇总：合并去重、过滤、命令行
  analysis.py   离线分析：概览统计与分组
  refresh.py    离线判旧：该重抓哪些记录
  graph.py      离线引文网络：从已抓数据还原边、报告谁被引最多
  report.py     离线综述：可读的 Markdown 报告
  audit.py      离线体检：字段可疑值与缺失率
  bibsynth.py   离线书目：由已存字段拼出 BibTeX
  storage.py    JSONL 写入、作者主页记录、BibTeX 文件、断点状态
  config.py     TOML 设置文件：读取校验、与命令行的优先级、来源追溯
  machine.py    给程序看的一份 JSON 文档：字段固定、失败词表、stdout 只放它
  text.py       终端列宽内的截断：截了就标出来
  cli.py        命令行入口：参数定义、模式分发
  __main__.py   让 python3 -m scholar_crawler 等价于 scholar-crawler
tests/          离线测试（含 headless Chromium 判定测试）与 mutate.py 守卫审计
scholar.toml.example   设置文件样例，直接 cp 成 scholar.toml 用
queries.example.txt    批量查询样例
```

MIT License。
