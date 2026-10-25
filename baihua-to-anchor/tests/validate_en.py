#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_en.py — baihua-to-anchor 英文语言包的输出校验器

规则与中文完全相同（SKILL.md），只换字面记号与词表（lang/en.md、lang/lexicon.en.json）。
    validate(input_text, output_text, lexicon, context=None) -> [(code, msg), ...]
错误码与 test_transcribe.py 一一对应（E01–E13），返回空列表即合格。
英文与中文的唯一机制差异：片段按「词」切而不是按「字」切；匹配按词边界、不分大小写。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEXICON_EN_PATH = ROOT / "lang" / "lexicon.en.json"

SCHEMA_VERSION = 3
INPUT_ANCHORS = "zixohyjqsr"
OUTPUT_ANCHORS = ["z:", "i:", "x:", "o:", "h:", "y:", "q:", "j:"]
LINE_ANCHOR_RE = re.compile(r"^[ \t]*[zixohyjqsr][:：]", re.M)

# 字面记号（与 lang/en.md §2 逐字一致）
NO_TRIGGER = "No trigger: "
INFER_LIST = "Inferred: "
LEARN_PREFIX = "[learn]"
LEARN_LINE_RE = re.compile(r"^\[learn\] new (\d+) / fixed (\d+)(?: candidates: (.+))?$")
INHERIT_TAG = " (carried over)"
DEBUG_TEMPLATE = "locate and fix "
HANDLE_TEMPLATE = "handle "
GIT_SUFFIX = ", restore to a continuable state"
OTHER_SUFFIX = ", exact action to be confirmed"
JOIN = ", "

CLAUSE_SEP = ",.;!?\n"
NEG_RE = re.compile(r"\b(not|no|never|nor|without|cannot|\w+n't)\b", re.I)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")

PATH_RE = re.compile(r"(?<![\w./\-])(?:~|\.{1,2})?/?[A-Za-z0-9_.\-]+(?:/[\w.\-]+)+|(?<![\w/])/[A-Za-z0-9_.\-]+(?:/[\w.\-]+)*")
URL_RE = re.compile(r"https?://[^\s,;!?()\"']+[^\s,;!?()\"'.]")
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
QUOTED_RE = re.compile(r"(?<![A-Za-z])\"[^\"\n]+\"|(?<![A-Za-z])'[^'\n]+'(?![A-Za-z])")
TECH_TOKEN_RE = re.compile(
    r"(?<![\w\-])(?:--?[A-Za-z][\w\-]*(?:=\S+)?"          # --flags / -x
    r"|[A-Za-z_][\w]*(?:[.:/][\w\-]+)+"                 # a.b  utils.py  host:port
    r"|[\w\-]*\d[\w.\-]*"                                # 含数字：3000 v3.2 utf-8 8G
    r"|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*"  # camelCase / PascalCase
    r"|[A-Z][A-Z_]{1,}[A-Z0-9_]*"                       # ALLCAPS / ENV_VAR
    r"|[A-Za-z]*(?:Error|Exception)"                    # 报错类名
    r"|\w+_\w+)(?![\w\-])"                              # snake_case
)


def load_lexicon(path=LEXICON_EN_PATH):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION or type(data.get("schema_version")) is not int:
        raise ValueError("lexicon schema_version 必须为 %d" % SCHEMA_VERSION)
    if data.get("lang") != "en":
        raise ValueError("不是英文词库（lang != en）")
    return data


def _active(lexicon, bucket):
    need = int(lexicon.get("active_min_count", 2))
    return [e for e in lexicon.get("learned", {}).get(bucket, [])
            if isinstance(e, dict) and isinstance(e.get("text"), str) and isinstance(e.get("count"), int) and e["count"] >= need]


def colloquial_words(lexicon, classes=None):
    out = []
    for cls, words in lexicon["colloquial"].items():
        if classes and cls not in classes:
            continue
        out += [(w, cls) for w in words]
    for e in _active(lexicon, "colloquial"):
        if not classes or e.get("category") in classes:
            out.append((e["text"], e.get("category")))
    return out


def word_rx(word):
    return re.compile(r"(?<![A-Za-z0-9_'])" + re.escape(word) + r"(?![A-Za-z0-9_'])", re.I)


def word_spans(text, word, lexicon):
    ctx = lexicon.get("deletion_context", {}).get(word)
    if ctx:
        return [(m.start(), m.end()) for m in re.finditer(ctx, text) if m.end() > m.start()]
    return [(m.start(), m.end()) for m in word_rx(word).finditer(text)]


def protected_spans(text):
    spans = []
    for m in FENCE_RE.finditer(text):
        spans.append((m.start(), m.end(), None))
    for rx in (INLINE_CODE_RE, URL_RE, PATH_RE, QUOTED_RE, TECH_TOKEN_RE):
        for m in rx.finditer(text):
            lit = m.group(0).rstrip(".")
            if re.fullmatch(r"[%s]:" % INPUT_ANCHORS, lit):
                continue
            if any(s <= m.start() and m.end() <= e for s, e, _ in spans):
                continue
            spans.append((m.start(), m.start() + len(lit), lit))
    return spans


def fence_lines(text):
    out = []
    for m in FENCE_RE.finditer(text):
        out += [ln for ln in m.group(1).split("\n") if ln.strip()]
    return out


def norm_ws(s):
    return re.sub(r"\s+", " ", s).strip().casefold()


def parse_output(output_text):
    lines = output_text.strip("\n").split("\n")
    if not lines or not lines[0].strip():
        return {"error": ("E01", "empty output")}
    first = lines[0]
    if first.startswith(NO_TRIGGER):
        if not first[len(NO_TRIGGER):].strip():
            return {"error": ("E01", "no reason after 'No trigger:'")}
        return {"trigger": False}
    fm = re.fullmatch(r"(`{3,})\s*", first)
    if not fm:
        return {"error": ("E01", "first line is neither a fence nor 'No trigger: <reason>': %r" % first)}
    fence = fm.group(1)
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == fence)
    except StopIteration:
        return {"error": ("E12", "unclosed code block")}
    return {"trigger": True, "block": lines[1:close], "rest": lines[close + 1:]}


def anchor_entries(block):
    entries, bad, in_code = [], [], False
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
        if re.search(r"```[\w+-]*$", m.group(3)):
            in_code = True
    return entries, bad


def decision(output_text):
    p = parse_output(output_text)
    if "error" in p:
        return None
    if not p["trigger"]:
        return "no-trigger"
    return p["block"][0].strip() if p["block"] else None


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
            err("E02", "input has a line-start anchor; output must be 'No trigger: …'")
        return errs
    if not p["trigger"]:
        return errs

    labels = {c["label"]: n for n, c in lexicon["scenes"].items()}
    block = p["block"]
    if not block or block[0].strip() not in labels:
        err("E01", "first block line is not a scene label: %r" % (block[0] if block else ""))
        return errs
    scene = labels[block[0].strip()]
    conf = lexicon["scenes"][scene]

    entries, bad = anchor_entries(block)
    for ln in bad:
        err("E03", "non-anchor line in block: %r" % ln)
    order = []
    for a, v, _ in entries:
        if a not in OUTPUT_ANCHORS:
            err("E03", "illegal output anchor: %s" % a)
            continue
        if not v.strip():
            err("E03", "empty anchor: %s" % a)
        if v != v.strip(" ") and not v.endswith("```"):
            err("E03", "leading/trailing space in anchor value: %r" % v)
        order.append(OUTPUT_ANCHORS.index(a))
    if order != sorted(order):
        err("E04", "anchor order must be z→i→x→o→h→y→q→j")

    tags = lexicon["default_tags"]
    infer_tag = lexicon["infer_tags"]["排障" if scene == "排障" else "其他"]
    user_lines, infer_lines, default_lines = [], [], []
    seen_infer = False
    for a, v, cont in entries:
        full = "\n".join([v] + cont)
        if "(inferred" in v:
            if a != "x:":
                err("E08", "inference only allowed on x:, found on %s" % a)
            elif not v.endswith(infer_tag):
                err("E08", "inference tag must be %r: %r" % (infer_tag, v))
            else:
                body = v[: -len(infer_tag)]
                spec_ok = False
                if body.startswith(HANDLE_TEMPLATE) and JOIN in body:
                    head, _, tail = body[len(HANDLE_TEMPLATE):].rpartition(JOIN)
                    spec_ok = bool(head.strip()) and bool(tail) and norm_ws(tail) in norm_ws(input_text)
                if scene == "排障":
                    git_form = body.startswith(HANDLE_TEMPLATE) and body.endswith(GIT_SUFFIX)
                    if not (body.startswith(DEBUG_TEMPLATE) or git_form or spec_ok):
                        err("E08", "debug inference must be 'locate and fix <symptom>' or 'handle <state>%s'" % GIT_SUFFIX)
                else:
                    if not (body.startswith(HANDLE_TEMPLATE) and (body.endswith(OTHER_SUFFIX) or spec_ok)):
                        err("E08", "inference must be 'handle <object>%s'" % OTHER_SUFFIX)
                for pre in (DEBUG_TEMPLATE, HANDLE_TEMPLATE):
                    if body.startswith(pre):
                        if body[len(pre):len(pre) + 1] in ("", " "):
                            err("E08", "exactly one space after template word: %r" % v)
                        break
            seen_infer = True
            infer_lines.append((a, full))
            continue
        if a == "x:" and seen_infer:
            err("E04", "inferred x: line must follow the user's x: line")
        if v.endswith(tags["y:"]):
            if a != "y:":
                err("E09", "%r only on y:" % tags["y:"])
            elif v[: -len(tags["y:"])] != conf.get("default_y"):
                err("E09", "default y: must be exactly %r" % conf.get("default_y"))
            default_lines.append((a, full))
            continue
        if v.endswith(tags["i:"]):
            if a != "i:":
                err("E09", "%r only on i:" % tags["i:"])
            elif v[: -len(tags["i:"])] not in (conf.get("default_i"), conf.get("default_i_ops")):
                err("E09", "default i: must be exactly %r" % conf.get("default_i"))
            default_lines.append((a, full))
            continue
        if "(default" in v:
            err("E09", "malformed default tag: %r" % v)
        if v.endswith(INHERIT_TAG) and (a != "z:" or not context.get("session_lock")):
            err("E03", "%r only on z: inside a session lock" % INHERIT_TAG)
        user_lines.append((a, full))

    au = [a for a, _ in user_lines]
    ad = [a for a, _ in default_lines]
    if "i:" not in au and "i:" not in ad:
        err("E10", "missing i: (write default i: when user gave none)")
    if "i:" in au and "i:" in ad:
        err("E09", "user gave i:, default i: not allowed")
    if "y:" in au and "y:" in ad:
        err("E09", "user gave a red line, default y: not allowed")
    if conf.get("default_y") and "y:" not in au and "y:" not in ad:
        err("E10", "%s needs default y: when user gave no red line" % conf["label"])
    if "z:" in conf.get("required", []) and "z:" not in au:
        err("E10", "%s requires z:" % conf["label"])
    if "y:" in conf.get("required", []) and "y:" not in au + ad:
        err("E10", "%s requires y:" % conf["label"])

    rest = [ln for ln in p["rest"] if ln.strip()]
    idx, listed = 0, None
    if idx < len(rest) and rest[idx].startswith(INFER_LIST):
        listed = [s.strip() for s in rest[idx][len(INFER_LIST):].split(",") if s.strip()]
        idx += 1
    if infer_lines and listed != ["x:"]:
        err("E08", "with an inferred line, 'Inferred: x:' must follow the block")
    if not infer_lines and listed is not None:
        err("E08", "'Inferred:' written without an inferred line")
    if any(ln.startswith(INFER_LIST) for ln in rest[idx:]):
        err("E12", "'Inferred:' line misplaced")
    if idx >= len(rest) or not rest[idx].startswith(LEARN_PREFIX):
        err("E11", "missing learning line, or not right after 'Inferred:'")
    else:
        m = LEARN_LINE_RE.match(rest[idx])
        if not m:
            err("E11", "bad learning line: %r" % rest[idx])
        elif m.group(3):
            for cand in m.group(3).split(", "):
                if re.fullmatch(r"[^→]+→diagnose_verbs", cand):
                    continue
                cm = re.fullmatch(r"(.+)→(colloquial|scene_triggers|anchor_patterns)\.(.+)", cand)
                if not cm:
                    err("E11", "bad candidate: %r" % cand)
                    continue
                kind, target = cm.group(2), cm.group(3)
                ok = ((kind == "colloquial" and target in lexicon["colloquial"])
                      or (kind == "scene_triggers" and target in lexicon["scenes"])
                      or (kind == "anchor_patterns" and target in lexicon["anchor_patterns"]))
                if not ok:
                    err("E11", "candidate target does not exist: %r" % cand)
        idx += 1
        if idx >= len(rest):
            err("E12", "missing accompanying sentence")
    if sum(1 for ln in rest if ln.startswith(LEARN_PREFIX)) > 1:
        err("E11", "duplicate learning line")

    user_text = "\n".join(v for _, v in user_lines)
    all_text = "\n".join(v for _, v in user_lines + infer_lines)
    all_cmp = norm_ws(all_text)

    # E05 技术片段逐字
    for ln in fence_lines(input_text):
        if ln not in all_text:
            err("E05", "code line not kept verbatim: %r" % ln)
    spans = protected_spans(input_text)
    for s, e, lit in spans:
        if lit and lit not in all_text:
            err("E05", "technical fragment not kept verbatim: %r" % lit)

    # E06 子句覆盖（按词）
    n = len(input_text)
    masked = list(input_text)
    for s, e, _ in spans:
        for i in range(s, e):
            masked[i] = "\x00"
    dele = [False] * n
    col = [False] * n
    for w, _ in colloquial_words(lexicon):
        for s, e in word_spans(input_text, w, lexicon):
            for i in range(s, e):
                dele[i] = col[i] = True
    literal = (list(lexicon["anchor_patterns"]["o:"]) + list(lexicon["negation"]["normalize"])
               + list(lexicon["weak_verb_replace"]) + list(lexicon["time_words"]["filler_when_alone"]))
    for w in literal:
        for m in word_rx(w).finditer(input_text):
            for i in range(m.start(), m.end()):
                dele[i] = True
    protect = lexicon["negation"]["protect"]
    for w in protect:
        for m in word_rx(w).finditer(input_text):
            for i in range(m.start(), m.end()):
                dele[i] = col[i] = False
    masked_text = "".join(masked)
    connectives = set(lexicon.get("clause_connectives", []))
    runs = []  # (clause_idx, [words])
    ci, cur = 0, []
    tok_re = re.compile(r"[A-Za-z][A-Za-z']*|[%s]|[^\sA-Za-z]" % re.escape(CLAUSE_SEP))
    for m in tok_re.finditer(masked_text):
        tok = m.group(0)
        is_word = WORD_RE.fullmatch(tok) is not None and not any(dele[m.start():m.end()])
        if is_word and tok.lower() in connectives:
            is_word = False  # 拆分点上的连接词（and / but…）可随拆分省去（lang/en.md §3 第 5 条）
        if is_word:
            cur.append(tok)
            continue
        if cur:
            runs.append((ci, cur))
        cur = []
        if tok in CLAUSE_SEP:
            ci += 1
    if cur:
        runs.append((ci, cur))
    for _, ws in runs:
        r = " ".join(ws)
        if len(ws) >= 2 and norm_ws(r) not in all_cmp:
            err("E06", "user phrase missing from output: %r" % r)

    # E07 否定
    for w in protect:
        if word_rx(w).search(masked_text) and not word_rx(w).search(user_text):
            err("E07", "%r is not a negation and must be kept" % w)
    clauses = re.split("[%s]" % re.escape(CLAUSE_SEP), masked_text)
    colm = "".join("\x01" if col[i] else ch for i, ch in enumerate(masked_text))
    col_clauses = re.split("[%s]" % re.escape(CLAUSE_SEP), colm)
    user_cmp = norm_ws(user_text)
    for k, clause in enumerate(clauses):
        probe = col_clauses[k]
        for w in protect:
            probe = word_rx(w).sub(lambda mm: "\x01" * len(mm.group(0)), probe)
        has_neg = bool(NEG_RE.search(probe))
        cands = [" ".join(ws) for c, ws in runs if c == k and len(ws) >= 2]
        if has_neg:
            start = sum(len(c) + 1 for c in clauses[:k])
            lits = [lit for s, e, lit in spans if lit and start <= s < start + len(clause)]
            pieces = []
            for r in cands:  # 去掉否定词后的片段也拿来定位（否定词本身可能就是被丢的部分）
                pieces += [q.strip() for q in NEG_RE.split(r) if q and not NEG_RE.fullmatch(q.strip()) and len(q.split()) >= 2]
            pool = sorted(cands + pieces + lits, key=len, reverse=True)
            if not pool:
                continue
            hit = []
            for key in pool:
                hit = [v for _, v in user_lines if norm_ws(key) in norm_ws(v) or key in v]
                if hit:
                    break
            if hit and not any(NEG_RE.search(v) for v in hit):
                err("E07", "negation lost for clause %r" % key)
        else:
            for r in cands:
                occ = [mm.start() for mm in re.finditer(re.escape(norm_ws(r)), user_cmp)]
                if occ and all(re.search(r"\b(don't|do not|not|never|no)\s$", user_cmp[:o]) for o in occ):
                    err("E07", "negation added before %r" % r)

    # E13 口水词残留
    for a, v in user_lines:
        val = v[: -len(INHERIT_TAG)] if v.endswith(INHERIT_TAG) else v
        mval = list(val)
        for s, e, _ in protected_spans(val):
            for i in range(s, e):
                mval[i] = "\x00"
        mval = "".join(mval)
        for w, cls in colloquial_words(lexicon, ["问候语", "客套", "托付套话", "弱化动词", "委婉句式", "语气词"]):
            if word_spans(mval, w, lexicon):
                err("E13", "%s still has filler %r (%s)" % (a, w, cls))
    return errs
