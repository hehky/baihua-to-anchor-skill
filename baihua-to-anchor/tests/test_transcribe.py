#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_transcribe.py — baihua-to-anchor 3.0.0 输出校验器 + 回归

定位：SKILL.md 由模型执行，本文件不再实现转写。它提供
    validate(input_text, output_text, lexicon, context=None) -> [(code, msg), ...]
只检查 SKILL.md 里可机检的硬性不变量；返回空列表即合格。它不判断场景选得对不对、
子句归属是否最优——那些要靠人或盲测对照 tests/cases.txt 的合格样本。

错误码：
  E01 首行不是合法场景标签，也不是「不触发：理由」
  E02 输入有行首锚点，输出却做了转写
  E03 锚点行非法（非输出锚点、空值、块内出现非锚点行、非法标注）
  E04 锚点行序不符合 SKILL.md §7
  E05 技术片段（代码块/行内代码/URL/路径/ASCII 片段/数字）没有逐字保留
  E06 原话的实质子句在输出里找不到
  E07 否定丢失、反转，或「别的/别人/区别」类词被误改
  E08 代推标注与「代推项」行不一致，或代推出现在 x: 以外、标注措辞不对、模板后的空格不对
  E09 默认项错误（值不是词库原文、标注挂错锚点、与用户原话并存）
  E10 必填锚点缺失（i: 必有；/排障 要 z:；/配置 /重构 要 y:）
  E11 学习结算行缺失或格式错误
  E12 输出结构错误（代码块未闭合、随附话缺失、顺序错）
  E13 用户原话锚点里残留口水词

用法：python3 tests/test_transcribe.py
"""
import copy
import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
LEXICON_PATH = ROOT / "lexicon.json"
CASES_PATH = TESTS / "cases.txt"
SKILL_PATH = ROOT / "SKILL.md"
README_PATH = ROOT / "README.md"
LEARN_PATH = ROOT / "scripts" / "learn.py"

SCHEMA_VERSION = 3
INPUT_ANCHORS = "zixohyjqsr"
OUTPUT_ANCHORS = ["z:", "i:", "x:", "o:", "h:", "y:", "q:", "j:"]
LINE_ANCHOR_RE = re.compile(r"^[ \t]*[zixohyjqsr][:：]", re.M)
CJK = "\u4e00-\u9fff"
CJK_RE = re.compile("[%s]" % CJK)
CLAUSE_SEP = "，。；！？\n"
NEG_CHARS = "不别没无未非勿禁"
INHERIT_TAG = "（沿用上一轮）"
GIT_INFER_SUFFIX = "，恢复到可继续的状态"  # git 工作流卡住时的排障代推写法（SKILL.md §9）
LEARN_LINE_RE = re.compile(r"^\[学习\] 新增词条 (\d+) / 纠错 (\d+)(?: 候选：(.+))?$")


# ---------------------------------------------------------------- 词库
def load_lexicon(path=LEXICON_PATH):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION or type(data.get("schema_version")) is not int:
        raise ValueError("lexicon schema_version 必须为 %d" % SCHEMA_VERSION)
    return data


def _active(lexicon, bucket, **match):
    need = int(lexicon.get("active_min_count", 2))
    out = []
    for e in lexicon.get("learned", {}).get(bucket, []):
        if not isinstance(e, dict) or not isinstance(e.get("text"), str):
            continue
        if not isinstance(e.get("count"), int) or e["count"] < need:
            continue
        if all(e.get(k) == v for k, v in match.items()):
            out.append(e)
    return out


def colloquial_words(lexicon, classes=None):
    """[(word, class)]：种子 + 生效的学习层口水词。"""
    out = []
    for cls, words in lexicon["colloquial"].items():
        if classes and cls not in classes:
            continue
        out += [(w, cls) for w in words]
    for e in _active(lexicon, "colloquial"):
        if not classes or e.get("category") in classes:
            out.append((e["text"], e.get("category")))
    return out


def word_spans(text, word, lexicon):
    """口水词在 text 中可删的位置（遵守 deletion_context）。"""
    ctx = lexicon.get("deletion_context", {}).get(word)
    if ctx:
        return [(m.start(), m.end()) for m in re.finditer(ctx, text) if m.end() > m.start()]
    return [(m.start(), m.end()) for m in re.finditer(re.escape(word), text)]


# ---------------------------------------------------------------- 技术片段
PATH_RE = re.compile(
    r"(?<![\w./\-])(?:~|\.{1,2})?/?[A-Za-z0-9_.\-]+(?:/[\w.\-%s]+)+|(?<![\w/])/[A-Za-z0-9_.\-]+(?:/[\w.\-%s]+)*" % (CJK, CJK)
)
URL_RE = re.compile(r"https?://[^\s，。；！？、（）「」“”]+")
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
ASCII_RUN_RE = re.compile(r"[^%s\u3000-\u303f\uff00-\uffef\n]+" % CJK)


def protected_spans(text, lexicon):
    """返回 [(start, end, literal)]：输入里必须逐字保留的片段。"""
    spans = []
    for m in FENCE_RE.finditer(text):
        spans.append((m.start(), m.end(), None))  # 代码块逐行另查
    for rx in (INLINE_CODE_RE, URL_RE, PATH_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), m.group(0)))
    deletable = set()
    for w, _ in colloquial_words(lexicon, ["问候语"]):
        for s, e in word_spans(text, w, lexicon):
            deletable.update(range(s, e))
    for m in ASCII_RUN_RE.finditer(text):
        raw = m.group(0)
        lead = len(raw) - len(raw.lstrip(" \t,;"))
        val = raw.strip(" \t,;")
        if not re.search(r"[A-Za-z0-9]", val):
            continue
        s = m.start() + lead
        e = s + len(val)
        if all(i in deletable for i in range(s, e)):
            continue  # 消息开头的 hi / hello
        if re.fullmatch(r"[%s]:" % INPUT_ANCHORS, val):
            continue  # 行内锚点符号，§8 改写⑤
        spans.append((s, e, val))
    return spans


def fence_lines(text):
    out = []
    for m in FENCE_RE.finditer(text):
        out += [ln for ln in m.group(1).split("\n") if ln.strip()]
    return out


# ---------------------------------------------------------------- 输出解析
def parse_output(output_text):
    """解析输出；返回 dict 或 {'error': (code,msg)}。"""
    lines = output_text.strip("\n").split("\n")
    if not lines or not lines[0].strip():
        return {"error": ("E01", "输出为空")}
    first = lines[0]
    if first.startswith("不触发："):
        if not first[len("不触发："):].strip():
            return {"error": ("E01", "「不触发：」后没有理由")}
        return {"trigger": False, "reason": first[len("不触发："):]}
    fm = re.fullmatch(r"(`{3,})\s*", first)
    if not fm:
        return {"error": ("E01", "首行既不是代码块开头也不是「不触发：理由」: %r" % first)}
    fence = fm.group(1)
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == fence)
    except StopIteration:
        return {"error": ("E12", "代码块未闭合")}
    block = lines[1:close]
    rest = [ln for ln in lines[close + 1:]]
    return {"trigger": True, "block": block, "rest": rest}


def anchor_entries(block):
    """[(anchor, value, [continuation lines])]，外加非法行列表。"""
    entries, bad = [], []
    in_code = False
    for ln in block[1:]:
        if in_code:
            entries[-1][2].append(ln)
            if ln.strip().startswith("```"):
                in_code = False
            continue
        m = re.match(r"^([a-z])(:)(.*)$", ln)
        if not m:
            bad.append(ln)
            continue
        entries.append([m.group(1) + ":", m.group(3), []])
        if m.group(3).rstrip().endswith("```") or re.search(r"```[\w+-]*$", m.group(3)):
            in_code = True
    return entries, bad


def decision(output_text):
    p = parse_output(output_text)
    if "error" in p:
        return None
    if not p["trigger"]:
        return "不触发"
    return p["block"][0].strip().lstrip("/") if p["block"] else None


# ---------------------------------------------------------------- validate
def validate(input_text, output_text, lexicon, context=None):
    context = context or {}
    errs = []

    def err(code, msg):
        errs.append((code, msg))

    p = parse_output(output_text)
    if "error" in p:
        return [p["error"]]
    if LINE_ANCHOR_RE.search(input_text):
        if p["trigger"]:
            err("E02", "输入含行首锚点，必须输出「不触发：…」")
        return errs
    if not p["trigger"]:
        return errs

    labels = {conf["label"]: name for name, conf in lexicon["scenes"].items()}
    block = p["block"]
    if not block or block[0].strip() not in labels:
        err("E01", "代码块首行不是合法场景标签: %r" % (block[0] if block else ""))
        return errs
    scene = labels[block[0].strip()]
    conf = lexicon["scenes"][scene]

    entries, bad = anchor_entries(block)
    for ln in bad:
        err("E03", "代码块里出现非锚点行: %r" % ln)
    order = []
    for a, v, cont in entries:
        if a not in OUTPUT_ANCHORS:
            err("E03", "非法输出锚点: %s" % a)
            continue
        if not v.strip():
            err("E03", "空锚点: %s" % a)
        order.append(OUTPUT_ANCHORS.index(a))
    if order != sorted(order):
        err("E04", "锚点行序不符合 z→i→x→o→h→y→q→j")
    seen_x_infer = False
    for a, v, _ in entries:
        if a == "x:":
            if "（代推：" in v:
                seen_x_infer = True
            elif seen_x_infer:
                err("E04", "代推 x: 行必须在用户原话 x: 行之后")

    tags = lexicon["default_tags"]
    infer_tag = lexicon["infer_tags"]["排障" if scene == "排障" else "其他"]
    user_lines, infer_lines, default_lines = [], [], []
    for a, v, cont in entries:
        full = "\n".join([v] + cont)
        if "（代推" in v:
            m = re.search(r"（代推：[^）]*）$", v)
            if a != "x:":
                err("E08", "代推只能出现在 x:，却出现在 %s" % a)
            elif not m:
                err("E08", "代推标注不在行尾或格式错误: %r" % v)
            elif m.group(0) != infer_tag:
                err("E08", "代推标注应为「%s」，实际「%s」" % (infer_tag, m.group(0)))
            elif not lexicon["learned"].get("corrections"):
                body = v[: m.start()]
                git_form = body.startswith("处理") and body.endswith(GIT_INFER_SUFFIX)
                # 托付套话删掉后只剩解法规格：处理<现状>，<规格原话>（SKILL.md §6 第 8 条、§9）
                head, _, tail = body[2:].rpartition("，")
                spec_form = body.startswith("处理") and bool(head.strip()) and bool(tail) and tail in input_text
                if scene == "排障" and not (body.startswith("定位并修复") or git_form or spec_form):
                    err("E08", "排障代推应写「定位并修复<症状原话片段>」或「处理<卡住的现状>%s」" % GIT_INFER_SUFFIX)
                if scene != "排障" and not (body.startswith("处理") and (body.endswith("，具体动作待确认") or spec_form)):
                    err("E08", "非排障代推应写「处理<对象>，具体动作待确认」")
                # 模板后的空格：片段以 ASCII 开头加一个半角空格，以汉字开头不加（SKILL.md §9）
                for pre in ("定位并修复", "处理"):
                    if body.startswith(pre):
                        frag = body[len(pre):]
                        if frag.startswith(" "):
                            if frag[1:2] in ("", " ") or CJK_RE.match(frag[1:2]):
                                err("E08", "代推片段以汉字开头时「%s」后不加空格: %r" % (pre, v))
                        elif frag[:1] and frag[:1].isascii():
                            err("E08", "代推片段以 ASCII 开头时「%s」后要加一个空格: %r" % (pre, v))
                        break
            infer_lines.append((a, full))
            continue
        if v.endswith(tags["y:"]):
            if a != "y:":
                err("E09", "「%s」只能挂在 y:" % tags["y:"])
            elif v[: -len(tags["y:"])] != conf.get("default_y"):
                err("E09", "默认 y: 必须逐字为「%s」" % conf.get("default_y"))
            default_lines.append((a, full))
            continue
        if v.endswith(tags["i:"]):
            if a != "i:":
                err("E09", "「%s」只能挂在 i:" % tags["i:"])
            elif v[: -len(tags["i:"])] not in (conf.get("default_i"), conf.get("default_i_ops")):
                err("E09", "默认 i: 必须逐字为「%s」" % conf.get("default_i"))
            default_lines.append((a, full))
            continue
        if "（默认" in v:
            err("E09", "默认标注写法错误: %r" % v)
        if v.endswith(INHERIT_TAG):
            if a != "z:" or not context.get("session_lock"):
                err("E03", "「%s」只能在会话锁内挂在 z:" % INHERIT_TAG)
        user_lines.append((a, full))

    anchors_user = [a for a, _ in user_lines]
    anchors_default = [a for a, _ in default_lines]
    if "i:" not in anchors_user and "i:" not in anchors_default:
        err("E10", "缺 i:（用户没说就写默认 i:）")
    if "i:" in anchors_user and "i:" in anchors_default:
        err("E09", "用户已给 i:，不能再写默认 i:")
    if "y:" in anchors_user and "y:" in anchors_default:
        err("E09", "用户已给红线，不能再写默认 y:")
    if conf.get("default_y") and "y:" not in anchors_user and "y:" not in anchors_default:
        err("E10", "%s 用户没给红线时必须写默认 y:" % conf["label"])
    if "z:" in conf.get("required", []) and "z:" not in anchors_user:
        err("E10", "/排障 必须有 z:")
    if "y:" in conf.get("required", []) and "y:" not in anchors_user + anchors_default:
        err("E10", "%s 必须有 y:" % conf["label"])

    # ---- 块外结构：代推项 → [学习] → 随附话
    rest = [ln for ln in p["rest"] if ln.strip()]
    idx = 0
    listed = None
    if idx < len(rest) and rest[idx].startswith("代推项："):
        listed = [s.strip() for s in rest[idx][len("代推项："):].split("、") if s.strip()]
        idx += 1
    if infer_lines and listed != ["x:"]:
        err("E08", "有代推行时代码块下方必须写「代推项：x:」")
    if not infer_lines and listed is not None:
        err("E08", "没有代推行却写了「代推项」")
    if any(ln.startswith("代推项：") for ln in rest[idx:]):
        err("E12", "「代推项」行位置错误（应紧跟代码块）")
    if idx >= len(rest) or not rest[idx].startswith("[学习]"):
        err("E11", "缺学习结算行，或它不在代推项之后")
    else:
        m = LEARN_LINE_RE.match(rest[idx])
        if not m:
            err("E11", "学习结算行格式错误: %r" % rest[idx])
        elif m.group(3):
            for cand in m.group(3).split("、"):
                if re.fullmatch(r"[^→]+→diagnose_verbs", cand):
                    continue  # 诊断动词桶没有子类别
                cm = re.fullmatch(r"(.+)→(colloquial|scene_triggers|anchor_patterns)\.(.+)", cand)
                if not cm:
                    err("E11", "候选格式错误: %r" % cand)
                    continue
                kind, target = cm.group(2), cm.group(3)
                ok = (
                    (kind == "colloquial" and target in lexicon["colloquial"])
                    or (kind == "scene_triggers" and target in lexicon["scenes"])
                    or (kind == "anchor_patterns" and target in lexicon["anchor_patterns"])
                )
                if not ok:
                    err("E11", "候选去向不存在: %r" % cand)
        idx += 1
        if idx >= len(rest):
            err("E12", "缺随附话")
    if sum(1 for ln in rest if ln.startswith("[学习]")) > 1:
        err("E11", "学习结算行重复")

    user_text = "\n".join(v for _, v in user_lines)
    all_text = "\n".join(v for _, v in user_lines + infer_lines)

    # ---- E05 技术片段
    for ln in fence_lines(input_text):
        if ln not in all_text:
            err("E05", "代码块行未逐字保留: %r" % ln)
    spans = protected_spans(input_text, lexicon)
    for s, e, lit in spans:
        if lit and lit not in all_text:
            err("E05", "技术片段未逐字保留在锚点里: %r" % lit)

    # ---- E06 子句覆盖
    masked = list(input_text)
    for s, e, _ in spans:
        for i in range(s, e):
            masked[i] = "\x00"
    dele = [False] * len(input_text)
    col_dele = [False] * len(input_text)
    removable = [w for w, _ in colloquial_words(lexicon)]
    for w in removable:
        for s, e in word_spans(input_text, w, lexicon):
            for i in range(s, e):
                dele[i] = True
                col_dele[i] = True
    literal_removable = (
        list(lexicon["anchor_patterns"]["o:"])
        + list(lexicon["negation"]["normalize"])
        + list(lexicon["weak_verb_replace"])
        + list(lexicon["time_words"]["filler_when_alone"])
    )
    for w in literal_removable:
        for m in re.finditer(re.escape(w), input_text):
            for i in range(m.start(), m.end()):
                dele[i] = True
    protect_words = lexicon["negation"]["protect"]
    for w in protect_words:  # 保护词不参与删除
        for m in re.finditer(re.escape(w), input_text):
            for i in range(m.start(), m.end()):
                dele[i] = False
    runs = []  # (clause_index, run)
    clause_idx, cur = 0, ""
    for i, ch in enumerate(masked):
        if ch in CLAUSE_SEP:
            if cur:
                runs.append((clause_idx, cur))
            cur = ""
            clause_idx += 1
            continue
        if ch != "\x00" and CJK_RE.match(ch) and not dele[i]:
            cur += ch
        else:
            if cur:
                runs.append((clause_idx, cur))
            cur = ""
    if cur:
        runs.append((clause_idx, cur))
    for _, r in runs:
        if len(r) >= 2 and r not in all_text:
            err("E06", "原话片段在输出里找不到: %r" % r)

    # ---- E07 否定
    masked_text = "".join(masked)
    for w in protect_words:
        for m in re.finditer(re.escape(w), masked_text):
            if w not in user_text:
                err("E07", "「%s」不是否定，必须原样保留" % w)
    neg_words = sorted(lexicon["negation"]["normalize"] + lexicon["negation"]["keep"], key=len, reverse=True)
    clauses = re.split("[%s]" % CLAUSE_SEP, masked_text)
    col_masked = "".join("\x01" if col_dele[i] else ch for i, ch in enumerate(masked_text))
    col_clauses = re.split("[%s]" % CLAUSE_SEP, col_masked)
    for ci, clause in enumerate(clauses):
        probe = col_clauses[ci]  # 口水词（能不能帮我…）里的「不能」不是否定
        for w in protect_words:
            probe = probe.replace(w, "\x01" * len(w))
        has_neg = any(w in probe for w in neg_words)
        has_neg_char = any(c in probe for c in NEG_CHARS)
        cands = [r for k, r in runs if k == ci and len(r) >= 2]
        if has_neg:
            # 用该子句最长的原话片段（或 ASCII 片段）定位输出行
            start = sum(len(c) + 1 for c in clauses[:ci])
            ascii_c = [lit for s, e, lit in spans if lit and start <= s < start + len(clause)]
            pool = sorted(cands + ascii_c, key=len, reverse=True)
            if not pool:
                continue
            key = pool[0]
            hit = [v for _, v in user_lines if key in v]
            if hit and not any(any(c in v for c in NEG_CHARS) for v in hit):
                err("E07", "含否定的子句（%r）在输出里丢了否定" % key)
        elif not has_neg_char:
            for r in cands:
                occ = [m.start() for m in re.finditer(re.escape(r), user_text)]
                if occ and all(o > 0 and user_text[o - 1] in "不别没" for o in occ):
                    err("E07", "原话没有否定，输出却在「%s」前加了否定" % r)

    # ---- E13 口水词残留（只查问候、客套、托付、弱化动词、委婉句式残片、语气词）
    for a, v in user_lines:
        val = v[: -len(INHERIT_TAG)] if v.endswith(INHERIT_TAG) else v
        mval = list(val)
        for rx in (FENCE_RE, INLINE_CODE_RE, URL_RE, PATH_RE, ASCII_RUN_RE):
            for m in rx.finditer(val):
                if rx is ASCII_RUN_RE and not re.search(r"[A-Za-z]", m.group(0)):
                    continue
                for i in range(m.start(), m.end()):
                    mval[i] = "\x00"
        mval = "".join(mval)
        for w, cls in colloquial_words(lexicon, ["问候语", "客套", "托付套话", "弱化动词", "委婉句式", "语气词"]):
            if word_spans(mval, w, lexicon):
                err("E13", "%s 里残留口水词「%s」（%s）" % (a, w, cls))
    return errs


# ---------------------------------------------------------------- SKILL.md 示例抽取
def extract_examples(skill_text):
    out = []
    for m in re.finditer(r"~~~text\n(.*?)\n~~~", skill_text, re.S):
        body = m.group(1)
        if "【输入】" not in body or "【输出】" not in body:
            continue
        ctx = {}
        cm = re.search(r"【上下文】会话锁定中，上一轮场景 /(\S+)", body)
        if cm:
            ctx = {"session_lock": True, "prev_scene": cm.group(1)}
        inp = re.search(r"【输入】(.*)", body).group(1)
        outp = body.split("【输出】\n", 1)[1]
        out.append({"input": inp, "output": outp, "context": ctx})
    return out


def lexicon_strings(lexicon):
    """词库里所有应在 SKILL.md 出现的字面词（正则模式除外）。"""
    out = []
    for name, conf in lexicon["scenes"].items():
        out += conf["triggers"] + [conf["default_i"]] + ([conf["default_y"]] if conf.get("default_y") else [])
        out += [conf["default_i_ops"]] if conf.get("default_i_ops") else []
    for v in lexicon["scene_exclusions"].values():
        out += v
    out += lexicon["symptoms"]["words"]
    fr = lexicon["feature_name_rule"]
    out += fr["repair_verbs"] + fr["diagnose_verbs"] + fr["state_markers"] + fr["conditional_markers"]
    out += lexicon["bad_smells"] + lexicon["knowledge_question"] + lexicon["opt_out"]
    out += lexicon["session"]["end_phrases"] + lexicon["session"].get("append_markers", []) + lexicon["imperatives"] + lexicon["risk_actions"]
    for v in lexicon["colloquial"].values():
        out += v
    out += list(lexicon["weak_verb_replace"]) + list(lexicon["weak_verb_replace"].values())
    out += lexicon["time_words"]["step_markers"]
    neg = lexicon["negation"]
    out += neg["normalize"] + neg["keep"] + neg["protect"]
    for a, v in lexicon["anchor_patterns"].items():
        out += v
    out += lexicon["content_nouns"] + lexicon["generic_words"]
    out += list(lexicon["default_tags"].values()) + list(lexicon["infer_tags"].values())
    return out


# ---------------------------------------------------------------- 回归
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or not detail else "  -> " + detail))


def main():
    print("【测试定位】输出校验器只查硬性不变量；场景与归属是否最优由合格样本与盲测对照判断。")
    real = load_lexicon()
    lex = copy.deepcopy(real)
    for k in lex["learned"]:
        lex["learned"][k] = []  # 校验基线与真实学习层无关

    cases = [json.loads(l) for l in CASES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = [c["id"] for c in cases]
    check("cases.txt 的 id 唯一", len(ids) == len(set(ids)))
    pos = [c for c in cases if c["kind"] == "positive"]
    neg = [c for c in cases if c["kind"] == "negative"]

    for c in pos:
        e = validate(c["input"], c["output"], lex, c.get("context"))
        check("[%s] 合格样本通过校验" % c["id"], not e, "; ".join("%s %s" % x for x in e))
        check("[%s] 场景/不触发与样本标注一致" % c["id"], decision(c["output"]) == c["scene"],
              "%s != %s" % (decision(c["output"]), c["scene"]))
    for c in neg:
        e = validate(c["input"], c["output"], lex, c.get("context"))
        codes = {x[0] for x in e}
        check("[%s] 反例被判红（%s）" % (c["id"], c["expect_code"]), c["expect_code"] in codes,
              "实际: %s" % ("; ".join("%s %s" % x for x in e) or "无错误"))

    skill = SKILL_PATH.read_text(encoding="utf-8")
    examples = extract_examples(skill)
    check("SKILL.md 示例可抽取（>=15 个）", len(examples) >= 15, str(len(examples)))
    pairs = {(c["input"], c["output"].strip("\n")) for c in pos}
    for i, ex in enumerate(examples, 1):
        e = validate(ex["input"], ex["output"], lex, ex["context"])
        check("SKILL.md 示例块 %d 通过校验" % i, not e, "; ".join("%s %s" % x for x in e))
        check("SKILL.md 示例块 %d 收录于 cases.txt" % i, (ex["input"], ex["output"].strip("\n")) in pairs)

    # ---- 文档与词库一致
    missing = [w for w in lexicon_strings(real) if w not in skill]
    check("词库每个字面词条都写在 SKILL.md 里", not missing, str(missing[:10]))
    for key in real:
        check("词库顶层键 %s 在 SKILL.md §14 有说明" % key, "| `%s` |" % key in skill)
    check("口水词恰为七类且 SKILL.md 写的是七类", len(real["colloquial"]) == 7 and "七类" in skill and "7 类" not in skill)
    check("scene_priority 与 SKILL.md 文字一致", " > ".join(real["scene_priority"]) in skill)
    check("学习层为空的真实词库 schema_version=3", real["schema_version"] == 3)

    ver = re.search(r"^version: (\S+)$", skill, re.M).group(1)
    readme = README_PATH.read_text(encoding="utf-8")
    learn_src = LEARN_PATH.read_text(encoding="utf-8")
    check("SKILL.md 版本为 3.0.0", ver == "3.0.0")
    check("README 标题版本与 SKILL.md 一致", readme.splitlines()[0].endswith(ver))
    check("learn.py VERSION 与 SKILL.md 一致", 'VERSION = "%s"' % ver in learn_src)
    desc = re.search(r"^description: (.+)$", skill, re.M).group(1)
    check("front matter description 是一句话（单行、<=120 字）", len(desc) <= 120 and "|" not in desc)

    for path in [SKILL_PATH, README_PATH, LEXICON_PATH, CASES_PATH, LEARN_PATH, Path(__file__), TESTS / "test_learn.py"]:
        check("%s 为 LF 换行" % path.name, b"\r" not in path.read_bytes())
    for path in [LEARN_PATH, Path(__file__), TESTS / "test_learn.py"]:
        check("%s 可执行" % path.name, bool(path.stat().st_mode & stat.S_IXUSR))

    n_pos, n_neg = len(pos), len(neg)
    check("SKILL.md 写的样本数与实际一致", "合格样本 %d 条、反例 %d 条" % (n_pos, n_neg) in skill)
    check("README 写的样本数与实际一致", "合格样本 %d 条、反例 %d 条" % (n_pos, n_neg) in readme)
    total = len(results) + 2
    check("SKILL.md 写的断言数与实际一致", "断言 %d 项（`test_transcribe.py`）" % total in skill,
          "应为 %d" % total)
    check("README 写的断言数与实际一致", "test_transcribe.py`：断言 %d 项" % total in readme)

    failed = [n for n, ok in results if not ok]
    print("---")
    print("合格样本 %d 条，反例 %d 条；断言 %d 项，失败 %d 项" % (n_pos, n_neg, len(results), len(failed)))
    for n in failed:
        print("  FAIL " + n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
