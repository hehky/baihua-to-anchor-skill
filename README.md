# baihua-to-anchor · Plain Words to Anchors (v3.1.0)

[English](#english) · [简体中文](#简体中文)

> Describe an engineering task in everyday words. The model turns it into a clean, line-by-line instruction block: filler removed, technical details kept character for character, and anything it had to guess clearly marked for your approval before it acts.

<a id="english"></a>

## What it is

Requests written to an AI coding assistant are often messy: filler words, jumbled order, key conditions buried mid-sentence. Models then miss constraints, rewrite what you said, or quietly decide things for you.

baihua-to-anchor is a Skill. You keep talking normally; the model first transcribes your request into **anchor format** (one intent per line, each line starting with a one-letter anchor), then works from that transcription.

- **Your words stay yours**: only filler from the lexicon is removed; nothing else is reworded.
- **Zero loss on technical fragments**: code, paths, URLs, commands, error messages and numbers are kept verbatim.
- **Negations never dropped or flipped**: "don't restart" and "never touch the database" always survive.
- **Transparent inference**: every step or default the model added on its own is labeled and waits for your confirmation.

> **Languages: Chinese and English.** One shared rule set; the input's language picks a language pack (`lang/`). No translation step: anchor letters and internal scene IDs never change, your original words, code, paths and commands stay verbatim, and only labels, templates and the follow-up sentence are localized. English scene tags: `/debug` `/deploy` `/config` `/refactor` `/script` `/dev`.

## When it triggers

- Automatically, when you describe an engineering task (debug, deploy, config, refactor, script, dev) in plain words **without** a line-start anchor.
- If a line already starts with an anchor (`x:`, `y:` …), you wrote the structure yourself, so it is not transcribed.
- After the first transcription a **session lock** keeps follow-up messages in the same task; a closing message ("done", "thanks") releases it.

## How it works

1. Load `lexicon.json` (version-checked).
2. Pick the scene from what you asked it to *do*: `/排障` debug · `/部署` deploy · `/配置` config · `/重构` refactor · `/脚本` script · `/开发` dev.
3. Split into clauses; each clause goes to exactly one anchor (named technologies are also copied to `h:`).
4. Clean: only five kinds of edits are allowed (e.g. deleting lexicon filler).
5. Add defaults and inferences, each labeled.
6. Run the self-check list.
7. Output the anchor block + inferred items + learning line + one follow-up sentence, then execute.

## Output anchors

| Anchor | Meaning |
|---|---|
| `z:` | Symptom, error text, current state, code smell |
| `i:` | Target end state (scene default added and labeled if you gave none) |
| `x:` | Action to take (inferred and labeled only when you gave none) |
| `o:` | Deliverable you explicitly asked for |
| `h:` | Named language, version, OS, framework, database, tool |
| `y:` | Red lines and limits, never dropped |
| `q:` | Standalone emphasis |
| `j:` | How the reply should be presented |

Fixed order: `/scene → z → i → x → o → h → y → q → j`. Empty anchors are omitted.

**Example (Chinese input)** — input: `npm run dev 报 port 3000 already in use，帮我搞一下` ("npm run dev says port 3000 already in use, sort it out")

```
/排障
z:npm run dev 报 port 3000 already in use
i:问题消除，功能恢复正常（默认）
x:定位并修复 npm run dev 报 port 3000 already in use（代推：由症状代推）
y:不改动无关的代码和配置（默认，可删）
```

The error text is kept verbatim, the filler "帮我搞一下" is gone, and the inferred action is labeled `代推` (inferred) for you to confirm.

## Hard rules vs. judgment

- **Hard rules (applied literally)**: line-start anchor detection, negation preserved, technical fragments verbatim, output format and order, learning-layer anti-pollution.
- **Semantics first**: scene, "is the system actually broken", and risk are judged by meaning; the lexicon is seed examples and a tie-breaker.
- **When unsure, be conservative**: don't delete, don't infer, ask one question. Defaults inferable from the project (language, existing framework) don't interrupt you.

## Files

```
baihua-to-anchor/
├── SKILL.md              The single rule set (17 sections: principles, glossary, scene chain, anchor rules, 22 examples, self-check, learning…)
├── lexicon.json          Lexicon (schema_version=3): seed layer + learning layer
├── lang/en.md            English language pack: selection rules, output markers, word-level handling
├── lang/lexicon.en.json  English lexicon (schema_version=3, same structure and scene IDs)
├── scripts/learn.py      Lexicon tool: add / correct / reject / remove / list / active / prune (whole-word matching for English)
├── README.md             In-package notes
└── tests/
    ├── test_transcribe.py  Output validator + regression (639 assertions)
    ├── test_learn.py       learn.py regression (114 assertions)
    ├── cases.txt           245 passing samples + 51 deliberately broken counter-examples
    ├── validate_en.py      English output validator
    ├── test_transcribe_en.py  English pack regression (244 assertions)
    └── cases_en.txt        42 English passing samples + 25 counter-examples
```

## Install & run

- Any AI coding tool that supports Skill directories: copy the whole folder into your skills directory.
  ```bash
  cp -r baihua-to-anchor <your-skills-dir>/
  ```
- Learning tool and tests need Python 3, no third-party dependencies.
  ```bash
  cd baihua-to-anchor
  python3 tests/test_transcribe.py   # 639 assertions
  python3 tests/test_learn.py        # 114 assertions
  python3 tests/test_transcribe_en.py # 244 assertions
  python3 scripts/learn.py add colloquial --text "劳驾" --category 客套
  python3 scripts/learn.py active
  ```

## Limits

- Engineering tasks only; chit-chat and non-technical questions don't trigger.
- The validator checks hard invariants only (labels, anchor legality and order, verbatim fragments, negation, required anchors…). Whether the scene or clause assignment is the best choice still needs human review or blind tests against the samples.
- A learned entry takes effect only after it is seen twice; a single sighting is just a candidate.
- Risky operations (production, push, merge, deletes) always ask first.

## FAQ

**I already write anchors. Will it rewrite mine?** No. A line-start anchor means it follows what you wrote.

**Will it "polish" my error message?** No. Error text, commands, paths and numbers are technical fragments, kept verbatim and covered by dedicated assertions.

**It added a step I don't want.** Every inferred item is labeled and the follow-up sentence asks you to confirm; just say no. Corrections can be written back to the lexicon.

**The lexicon is missing a word.** Use `scripts/learn.py`; the learning layer is isolated from the seeds and guarded against pollution.

Changelog: `SKILL.md` §17.

---

<a id="简体中文"></a>

## 简体中文

**白话转锚点 v3.1.0**：用白话提工程任务，模型先把它转写成《符号指令解析规范 v5.2》锚点格式（一行一个意图），再按转写结果执行。

- **不改原话**：只删词库里的口水词，其余一字不动。
- **技术片段零损失**：代码、路径、URL、命令、报错原文、数字逐字保留。
- **否定不丢不反转**："别重启""不要动数据库"一定保留原意。
- **代推透明**：模型自己补的步骤或默认项全部单独标注，等你确认。

**触发**：白话工程任务（排障、部署、配置、重构、脚本、开发）且行首没有锚点时自动触发；行首已有锚点不转写；首次转写后进入会话锁，说"好了/谢谢"解锁。

**多语言**：支持中文、英文；规则一份，按输入语言选语言包（`lang/`），不走翻译，锚点字母与场景 ID 不变，原话与技术片段原样保留。

**安装与测试**：把 `baihua-to-anchor/` 复制到 skill 库目录；`python3 tests/test_transcribe.py`（639 项）、`python3 tests/test_learn.py`（114 项）、`python3 tests/test_transcribe_en.py`（244 项），只需 Python 3。

**能力边界**：只处理工程任务；校验器只查硬性不变量；学习词条出现 2 次才生效；风险操作先征询。完整规则见 `SKILL.md`，变更记录见 §17。

---

v3.1.0 · Release package SHA-256: `877fcd8a9edd76b9795b4d6f5e8e37ab3a3a2cd635fe4d0dad64e2c52ee323f9`
