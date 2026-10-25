#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_transcribe_en.py — 英文语言包回归

查四件事：
  1. 英文词库与中文词库结构一致（键、场景 id、口水词类别、锚点名都相同，只换词条）；
  2. lang/en.md 的字面记号与校验器常量逐字一致，§4 示例收录进 cases_en.txt；
  3. cases_en.txt：合格样本全部通过且场景判定一致，每条反例命中预期错误码；
  4. learn.py 在英文词库上能学、能拦（按词边界防污染）。

用法：python3 tests/test_transcribe_en.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
import validate_en as V  # noqa: E402

ZH_LEX = ROOT / "lexicon.json"
EN_LEX = ROOT / "lang" / "lexicon.en.json"
EN_MD = ROOT / "lang" / "en.md"
CASES = TESTS / "cases_en.txt"
LEARN = ROOT / "scripts" / "learn.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("[FAIL] %s %s" % (name, detail))


def main():
    zh = json.loads(ZH_LEX.read_text(encoding="utf-8"))
    en = V.load_lexicon(EN_LEX)

    # 1. 结构一致
    extra = {"lang", "clause_connectives"}
    check("顶层键与中文词库一致", set(en) - extra == set(zh), str(set(en) ^ set(zh)))
    check("场景 id 一致", list(en["scenes"]) == list(zh["scenes"]))
    check("场景优先级一致", en["scene_priority"] == zh["scene_priority"])
    for sc in zh["scenes"]:
        check("场景 %s 字段一致" % sc, set(en["scenes"][sc]) == set(zh["scenes"][sc]))
        check("场景 %s 必填锚点一致" % sc, en["scenes"][sc]["required"] == zh["scenes"][sc]["required"])
    check("口水词类别一致", list(en["colloquial"]) == list(zh["colloquial"]))
    check("锚点标记名一致", list(en["anchor_patterns"]) == list(zh["anchor_patterns"]))
    check("默认/代推标注键一致", set(en["default_tags"]) == set(zh["default_tags"]) and set(en["infer_tags"]) == set(zh["infer_tags"]))
    check("学习层桶一致", set(en["learned"]) == set(zh["learned"]))
    check("兜底规则条数一致", len(en["scene_fallback"]["rules"]) == len(zh["scene_fallback"]["rules"]))
    check("可学习兜底规则一致", [r.get("learnable") for r in en["scene_fallback"]["rules"]] == [r.get("learnable") for r in zh["scene_fallback"]["rules"]])
    for rule in en["scene_fallback"]["rules"]:
        for k in ("pattern", "requires", "exclude"):
            if k in rule:
                try:
                    re.compile(rule[k])
                    ok = True
                except re.error:
                    ok = False
                check("兜底正则可编译 %s" % k, ok)
    for w, rx in en["deletion_context"].items():
        try:
            re.compile(rx)
            ok = True
        except re.error:
            ok = False
        check("deletion_context 正则可编译 %s" % w, ok)
        check("deletion_context 词在口水词表里 %s" % w, any(w in ws for ws in en["colloquial"].values()))
    for p in en["symptoms"]["patterns"]:
        try:
            re.compile(p)
            ok = True
        except re.error:
            ok = False
        check("症状正则可编译", ok, p)
    for w in en["colloquial"]["问候语"] + en["colloquial"]["客套"]:
        check("口水词不含实义名词 %s" % w, not any(re.search(r"\b%s\b" % re.escape(n), w) for n in en["content_nouns"]))

    # 2. en.md 记号
    md = EN_MD.read_text(encoding="utf-8")
    for tok in (V.NO_TRIGGER.strip(), V.INFER_LIST.strip(), V.INHERIT_TAG.strip(), V.DEBUG_TEMPLATE.strip(),
                V.GIT_SUFFIX.lstrip(", "), V.OTHER_SUFFIX.lstrip(", "), "[learn] new N / fixed M"):
        check("en.md 含记号 %r" % tok, tok in md)
    for sc in en["scenes"].values():
        check("en.md 含场景标签 %s" % sc["label"], "`%s`" % sc["label"] in md)
    for v in list(en["default_tags"].values()) + list(en["infer_tags"].values()):
        check("en.md 含标注 %r" % v.strip(), v.strip() in md)

    cases = [json.loads(ln) for ln in CASES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    ids = [c["id"] for c in cases]
    check("cases_en id 唯一", len(ids) == len(set(ids)))
    pairs = {(c["input"], c["output"]) for c in cases if c["kind"] == "positive"}
    sec = md.split("## §4", 1)[1].split("## §5", 1)[0]
    exs = re.findall(r"输入：`([^`]+)`\n\n(```\n.*?\n```\n.*?)(?=\n\n输入：|\n\n## |\Z)", sec, re.S)
    check("en.md §4 抽到 2 个示例", len(exs) == 2, str(len(exs)))
    for i, (inp, o) in enumerate(exs, 1):
        check("en.md 示例 %d 收录于 cases_en.txt" % i, (inp, o.strip("\n")) in pairs)

    # 3. 样本
    npos = nneg = 0
    for c in cases:
        if c["kind"] == "positive":
            npos += 1
            errs = V.validate(c["input"], c["output"], en, c.get("context"))
            check("合格样本 %s 通过" % c["id"], not errs, str(errs))
            want = "no-trigger" if c["scene"] == "不触发" else en["scenes"][c["scene"]]["label"]
            check("合格样本 %s 场景 %s" % (c["id"], want), V.decision(c["output"]) == want, str(V.decision(c["output"])))
        else:
            nneg += 1
            codes = [e[0] for e in V.validate(c["input"], c["output"], en, c.get("context"))]
            check("反例 %s 命中 %s" % (c["id"], c["expect_code"]), c["expect_code"] in codes, str(codes))
    covered = {c["expect_code"] for c in cases if c["kind"] == "negative"}
    check("反例覆盖 E01–E13", covered == {"E%02d" % i for i in range(1, 14)}, str(sorted(covered)))
    labels = {V.decision(c["output"]) for c in cases if c["kind"] == "positive"}
    check("合格样本覆盖六个场景与不触发", labels == {s["label"] for s in en["scenes"].values()} | {"no-trigger"}, str(labels))

    # 4. learn.py
    d = tempfile.mkdtemp()
    try:
        lex = os.path.join(d, "lexicon.en.json")
        shutil.copy(EN_LEX, lex)
        env = dict(os.environ)
        env["BAIHUA_" + "LEXICON"] = lex
        for args, want, name in [
            (["add", "colloquial", "--text", "gonna", "--category", "填充词"], 0, "gonna 不因含 go 被拒"),
            (["add", "colloquial", "--text", "gonna", "--category", "填充词"], 0, "重复学习计数"),
            (["add", "colloquial", "--text", "the database", "--category", "填充词"], 1, "含实义名词拒绝"),
            (["add", "scene-trigger", "--text", "bug", "--scene", "开发"], 1, "过泛触发词拒绝"),
            (["add", "colloquial", "--text", "please", "--category", "客套"], 2, "种子层跳过"),
            (["add", "scene-trigger", "--text", "spin up", "--scene", "部署"], 0, "学触发词"),
            (["add", "anchor-pattern", "--text", "never", "--anchor", "q:"], 1, "锚点标记冲突拒绝"),
            (["add", "diagnose-verb", "--text", "fix", ], 1, "修复动词不能当诊断动词"),
        ]:
            r = subprocess.run([sys.executable, str(LEARN)] + args, env=env, capture_output=True, text=True)
            check("learn.py en：%s" % name, r.returncode == want, "rc=%d %s" % (r.returncode, (r.stdout + r.stderr).strip()[:120]))
        data = json.loads(Path(lex).read_text(encoding="utf-8"))
        g = [e for e in data["learned"]["colloquial"] if e["text"] == "gonna"]
        check("learn.py en：gonna count=2 已生效", bool(g) and g[0]["count"] == 2)
        check("learn.py en：写回后 lang 键保留", data.get("lang") == "en")
        errs = V.validate("gonna fix the build, it fails", "```\n/debug\nz:it fails\ni:problem gone, feature works normally again (default)\nx:fix the build\ny:don't touch unrelated code or config (default, removable)\n```\n[learn] new 0 / fixed 0\nOn it.", data)
        check("生效的英文学习词条参与清洗", not errs, str(errs))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("合格样本 %d 条，反例 %d 条；断言 %d 项，失败 %d 项" % (npos, nneg, passed + failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
