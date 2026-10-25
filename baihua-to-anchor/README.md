# 白话转锚点（baihua-to-anchor）3.0.0

用户用白话（行首没有锚点）提工程任务时，模型按 `SKILL.md` 把白话转写成《符号指令解析规范 v5.2》的锚点格式（`/场景` `z:` `i:` `x:` `o:` `h:` `y:` `q:` `j:`），删掉口水词、保留全部技术事实、单独标出代推项，再按转写结果执行。

## 文件

- `SKILL.md`：唯一规则，由模型执行。总原则（词库是种子示例 + 判定提示，判定以语义为准）、术语表、场景决策链、"系统已坏"判定、会话锁、子句归属、锚点细则、五种允许的改写、默认项与代推、输出与执行、22 组示例、自查清单、学习、词库键说明、种子一览、变更记录。
- `lexicon.json`：词库（`schema_version=3`），只提供词表数据；每个键的含义见 `SKILL.md` §14。学习层在 `learned` 区。
- `scripts/learn.py`：词库学习工具（add 口水词 / 触发词 / 锚点标记 / 诊断动词、correct / reject / remove / list / active / prune），带文件锁、版本校验、防污染与坏数据容错。
- `tests/test_transcribe.py`：输出校验器 `validate(input_text, output_text, lexicon, context)` 加回归。
- `tests/cases.txt`：合格样本（输入 + 合格输出，含 `SKILL.md` 全部示例、40 句盲测句、盲测 v3 的 30 句与盲测 v3b 的 20 句的正确产物、审计对抗输入）与故意错误的反例。
- `tests/test_learn.py`：`learn.py` 回归，跑在临时词库副本上。

## 安装

把整个目录复制到所用工具的 skill 库目录，例如：

```bash
cp -r baihua-to-anchor <skill库目录>/
```

## 使用

无需显式调用。用白话说一个工程任务即可，例如"npm run dev 报 port 3000 already in use，帮我搞一下"。模型先输出转写块、代推项和学习结算行，再按随附话确认或直接执行。

词库维护（在 skill 目录下执行）：

```bash
python3 scripts/learn.py add colloquial --text "劳驾" --category 客套
python3 scripts/learn.py add scene-trigger --text "炸了" --scene 排障
python3 scripts/learn.py list
python3 scripts/learn.py active
python3 scripts/learn.py prune --min-count 2
```

返回码：0 成功；1 拒绝或出错；2 已在种子层、跳过。写操作会在词库旁生成锁文件 `lexicon.json.lock`，可以保留。

## 测试

```bash
cd <skill目录>
python3 tests/test_transcribe.py
python3 tests/test_learn.py
```

- `test_transcribe.py`：断言 639 项。合格样本 245 条、反例 51 条。合格样本必须全部通过校验，反例必须全部判红（证明校验器不是恒真）；另外检查 `SKILL.md` 示例逐字收录且通过校验、词库每个字面词条都写在 `SKILL.md` 里、版本号与样本数一致、文件为 LF、脚本可执行。
- `test_learn.py`：断言 114 项。覆盖 add（含诊断动词桶）/ correct / reject / remove（含 correction 与 rejected）/ list 过滤 / active / prune、种子层与学习层防污染、满额淘汰、NFKC 去重、坏数据容错、文件权限、并发锁，以及 `SKILL.md` §13.4 与本文件里的命令可以照抄运行。

校验器只查硬性不变量（场景标签、锚点合法与行序、技术片段逐字保留、子句不丢、否定不丢不反转、默认项与代推标注、必填锚点、结算行格式、口水词残留、行首锚点输入不触发）。场景选得对不对、归属是否最优，要靠人或盲测对照合格样本判断。

## 变更

3.0.0 的变更见 `SKILL.md` §17。
