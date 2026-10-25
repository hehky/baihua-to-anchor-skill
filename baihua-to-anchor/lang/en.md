# 英文语言包（lang: en）

> 规则只有一份：`SKILL.md` §0–§13 原样适用于英文输入。本文件只规定**换什么**：词库换成 `lang/lexicon.en.json`，字面记号换成下表的英文写法，按词处理代替按字处理。不翻译、不调用翻译接口。

## §1 选语言包（转写前第一步）

1. 去掉技术片段（代码、路径、URL、命令、报错原文、技术名）后，看用户**白话部分**用的语言：有汉字白话 → 中文包（`lexicon.json`）；全是英文白话 → 英文包（`lang/lexicon.en.json`）。
2. 混说按主语言：中文句子夹英文技术词 → 中文包；英文句子夹个别中文词 → 英文包，中文词按原话保留，不删不译。
3. 一轮只用一个包；会话锁内换语言，按新消息的语言重新选包，场景照常继承。
4. 输出用输入的语言：锚点字母不变，锚点内容保留用户原话，标注、代推模板、结算行、随附话都用本包的写法。

## §2 字面记号对照（逐字使用，与 `tests/validate_en.py` 一致）

| 中文包 | 英文包 |
|---|---|
| `不触发：<理由>` | `No trigger: <reason>` |
| `/排障` `/部署` `/配置` `/重构` `/脚本` `/开发` | `/debug` `/deploy` `/config` `/refactor` `/script` `/dev`（场景内部 id 不变，仍是 排障…开发） |
| `（默认）` | ` (default)`（前有一个空格） |
| `（默认，可删）` | ` (default, removable)` |
| `（代推：由症状代推）` | ` (inferred: from symptom)` |
| `（代推：由 i: 代推）` | ` (inferred: from i:)` |
| `（沿用上一轮）` | ` (carried over)` |
| `定位并修复<症状>` | `locate and fix <symptom>` |
| `处理<现状>，恢复到可继续的状态` | `handle <state>, restore to a continuable state` |
| `处理<对象>，具体动作待确认` | `handle <object>, exact action to be confirmed` |
| `处理<现状>，<规格原话>` | `handle <state>, <spec as said>` |
| `代推项：x:` | `Inferred: x:` |
| `[学习] 新增词条 N / 纠错 M 候选：a→b、c→d` | `[learn] new N / fixed M candidates: a→b, c→d` |
| 同锚点多子句用「，」连接；`h:` 多项用「、」 | 都用 `, ` |
| 上一轮症状无对象时写「上一轮任务的问题」 | `the previous task's problem` |

模板词（`locate and fix` / `handle`）后**固定一个半角空格**（英文没有"汉字开头不加空格"这一条）。

## §3 按词处理（英文与中文唯一的机制差异）

1. **匹配**：词表里的词按词边界、不分大小写匹配（`go` 不命中 `gonna`，`data` 不命中 `database`）；`deletion_context` 有正则的照正则。
2. **只删完整词**：口水词删掉后，留下的孤立标点（句首逗号、连续两个逗号）一并去掉；不改大小写，不改其余措辞，缩写原样（`don't` 不展开成 `do not`）。
3. **否定**（硬规则②不变）：否定词是 `not / no / never / nor / without / cannot / …n't`。只在 `y:` 里归一：`negation.normalize`（absolutely don't / do not / never ever…）后接动词时归一成 `don't`；`negation.keep`（can't / must not / never / no more than / without…）原样保留；`negation.protect`（not only / no-op / notification…）不是否定。
4. **技术片段**（硬规则③不变）：除代码、路径、URL 外，英文里还按形态认：带数字的词（`3000` `v3.2` `utf-8`）、带 `. : /` 的词（`utils.py` `host:8080`）、驼峰与全大写（`UserController` `SENTRY_DSN`）、`--flag`、引号里的原文、`…Error/…Exception`。逐字保留。
5. **子句**：按 `, . ; ! ?` 和换行切；一个子句里有动作又有红线或两个动作（`fix it but do not raise the limit`）时按语义拆，拆分点上的连接词（词库 `clause_connectives`：and / but / while / whereas）随拆分省去，这是英文唯一多出的删除；不拆时连接词原样保留。

## §4 示例（输出部分逐字可作合格样本，收录于 `tests/cases_en.txt`）

输入：`hi, my project won't start, npm run dev throws port 3000 already in use, can you fix it? don't break my other config, thanks`

```
/debug
z:my project won't start, npm run dev throws port 3000 already in use
i:problem gone, feature works normally again (default)
x:fix it
y:don't break my other config
```
[learn] new 0 / fixed 0
Want me to find the process holding port 3000 and free it?

输入：`could you write a Python script that sorts the images in downloads into subfolders by date, never overwrite duplicates, I need it today`

```
/script
i:script runs and completes the target processing (default)
x:write a Python script that sorts the images in downloads into subfolders by date
h:Python
y:never overwrite duplicates
q:I need it today
```
[learn] new 0 / fixed 0
Small task, writing the script now.

## §5 学习层

`learn.py` 不分语言，用环境变量指定词库：`BAIHUA_LEXICON=<skill目录>/lang/lexicon.en.json python3 scripts/learn.py add colloquial --text "gonna" --category 填充词`。类别、场景、锚点名用内部 id（中文），词条是英文。英文按词边界查防污染（`gonna` 不会因含 `go` 被拒）。

## §6 新增一种语言

复制 `lang/lexicon.en.json` 改成 `lexicon.<code>.json`（键与内部 id 不动，只换词条），复制本文件写字面记号对照，再配一套合格样本与反例。规则、优先级、锚点字母一律不改。
