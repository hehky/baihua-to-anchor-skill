#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
learn.py — baihua-to-anchor 3.1.0 词库学习工具

子命令：
  add colloquial     --text T --category 类别 [--source S]
  add scene-trigger  --text T --scene 场景     [--source S]
  add anchor-pattern --text T --anchor 锚点   [--source S]   锚点限 i: o: h: y: q: j:
  add diagnose-verb  --text T                [--source S]   诊断动词（瞄一眼 / 排查一下…类"先看看情况"的动作）
  correct --wrong A --right B [--anchor x:] [--source S]      纠错只作用于代推行（x:）
  reject  --text T [--reason R] [--source S]                 判错拉黑
  remove  --kind {colloquial,scene-trigger,anchor-pattern,diagnose-verb,correction,rejected} --text T [--anchor A]
  list    [colloquial|scene-trigger|anchor-pattern|diagnose-verb|correction|rejected]
  active
  prune   [--min-count N]                                      N >= 1，默认 2

返回码：0 成功；1 拒绝或出错（原因在 stderr）；2 已在种子层、跳过（不算学到）。
环境变量 BAIHUA_LEXICON：指定词库路径（测试用临时副本）；缺省为脚本上级目录的 lexicon.json。
写操作持排他锁（lexicon.json.lock）完成读-改-写；读操作持共享锁，锁文件建不了时无锁只读。
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "3.1.0"
SCHEMA_VERSION = 3
DEFAULT_LEXICON = Path(__file__).resolve().parent.parent / "lexicon.json"
LEXICON_PATH = Path(os.environ.get("BAIHUA_LEXICON") or DEFAULT_LEXICON).resolve()
LOCK_PATH = LEXICON_PATH.with_name(LEXICON_PATH.name + ".lock")
ADD_KINDS = ("colloquial", "scene-trigger", "anchor-pattern", "diagnose-verb")
ALL_KINDS = ADD_KINDS + ("correction", "rejected")
BUCKET = {"colloquial": "colloquial", "scene-trigger": "scene_triggers", "anchor-pattern": "anchor_patterns",
          "diagnose-verb": "diagnose_verbs", "correction": "corrections", "rejected": "rejected"}
PRUNABLE = ("colloquial", "scene_triggers", "anchor_patterns", "diagnose_verbs")
ATTR_FIELD = {"colloquial": "category", "scene-trigger": "scene", "anchor-pattern": "anchor", "diagnose-verb": None}
REQUIRED_FIELDS = {
    "colloquial": ("text", "count", "category"),
    "scene_triggers": ("text", "count", "scene"),
    "anchor_patterns": ("text", "count", "anchor"),
    "diagnose_verbs": ("text", "count"),
    "corrections": ("wrong", "right", "anchor", "count"),
    "rejected": ("text",),
}
LEARNABLE_ANCHORS = ("i:", "o:", "h:", "y:", "q:", "j:")
CORRECTION_ANCHORS = ("x:",)
MAX_TEXT_LEN = 50
MAX_PER_BUCKET = 200
DEFAULT_ACTIVE_MIN_COUNT = 2
NEG_RE = re.compile(r"禁止|[不别没无未非勿]")
TZ = timezone(timedelta(hours=8))
EXIT_SKIP = 2


class Skip(Exception):
    """已在种子层，跳过。"""


def fail(msg):
    sys.stderr.write("错误: " + msg + "\n")
    sys.exit(1)


def warn(msg):
    sys.stderr.write("警告: " + msg + "\n")


def now_str():
    return datetime.now(TZ).isoformat(timespec="seconds")


def norm(text):
    """NFKC 归一并去首尾空白；比对时再 casefold。"""
    return unicodedata.normalize("NFKC", text or "").strip()


def key(text):
    return norm(text).casefold()


def has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def inside(small, big):
    """small 是否作为整体出现在 big 里：含汉字按子串；纯拉丁文字按词边界（gonna 不含 go）。"""
    if not small:
        return False
    if has_cjk(small) or has_cjk(big):
        return small in big
    return re.search(r"(?<![0-9a-z_])" + re.escape(small) + r"(?![0-9a-z_])", big) is not None


def check_text(text, what="词条文本"):
    if not text:
        fail("%s为空" % what)
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text):
        fail("%s含换行或控制字符" % what)
    if len(text) > MAX_TEXT_LEN:
        fail("%s过长（>%d 字符），疑似任务内容而非语言模式" % (what, MAX_TEXT_LEN))


# ---------------------------------------------------------------- 读写
def load_lexicon():
    try:
        with open(LEXICON_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        fail("词库不存在 %s" % LEXICON_PATH)
    except json.JSONDecodeError as exc:
        fail("词库 JSON 损坏：%s" % exc)
    if not isinstance(data, dict):
        fail("词库顶层必须是对象")
    ver = data.get("schema_version")
    if type(ver) is not int or ver != SCHEMA_VERSION:
        fail("词库 schema_version=%r，本脚本要求 %d，请先迁移词库" % (ver, SCHEMA_VERSION))
    learned = data.get("learned")
    if not isinstance(learned, dict):
        fail("词库 learned 区缺失或不是对象")
    for b in REQUIRED_FIELDS:
        learned.setdefault(b, [])
        if not isinstance(learned[b], list):
            fail("词库 learned.%s 必须是数组" % b)
    for k in ("scenes", "colloquial", "anchor_patterns", "symptoms"):
        if not isinstance(data.get(k), dict):
            fail("词库缺少 %s 区" % k)
    return data


def valid(entry, bucket):
    if not isinstance(entry, dict):
        return False
    for f in REQUIRED_FIELDS[bucket]:
        if f not in entry:
            return False
    if "count" in REQUIRED_FIELDS[bucket]:
        c = entry.get("count")
        if type(c) is not int or c < 1:
            return False
    return all(isinstance(entry[f], str) for f in REQUIRED_FIELDS[bucket] if f != "count")


def entries(lexicon, bucket, quiet=True):
    out = []
    for i, e in enumerate(lexicon["learned"].get(bucket, [])):
        if valid(e, bucket):
            out.append(e)
        elif not quiet:
            warn("learned.%s 第 %d 条缺字段或字段类型不对，已跳过：%r" % (bucket, i + 1, e))
    return out


@contextlib.contextmanager
def lock(exclusive):
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        if exclusive:
            fail("无法创建锁文件 %s，词库目录不可写" % LOCK_PATH)
        warn("无法创建锁文件，按无锁只读处理")
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_lexicon(data):
    """原子写：临时文件 + fsync + rename，保留原文件权限。调用方已持排他锁。"""
    data["updated_at"] = now_str()
    try:
        mode = os.stat(LEXICON_PATH).st_mode & 0o7777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=str(LEXICON_PATH.parent), prefix=".lexicon-", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, LEXICON_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


@contextlib.contextmanager
def editing():
    """排他锁内读-改-写；块内 sys.exit/Skip 时不写回。"""
    with lock(True):
        data = load_lexicon()
        yield data
        write_lexicon(data)


def reading():
    with lock(False):
        return load_lexicon()


# ---------------------------------------------------------------- 种子层与信息词
def plain(words):
    """去掉正则模式与 … 占位，只留字面词。"""
    out = set()
    for w in words:
        if not isinstance(w, str) or not w:
            continue
        for part in w.split("…"):
            if part and not re.search(r"[\\\[\]()*+?|{}^$]", part):
                out.add(key(part))
    return out


def seed_triggers(lexicon, scene):
    if scene == "排障":
        return plain(lexicon["symptoms"].get("words", []))
    return plain(lexicon["scenes"].get(scene, {}).get("triggers", []))


def seed_colloquial(lexicon):
    s = set()
    for words in lexicon["colloquial"].values():
        s |= plain(words)
    return s


def seed_anchor(lexicon, anchor):
    return plain(lexicon["anchor_patterns"].get(anchor, []))


def informative(lexicon):
    """有信息量的词：口水词不许等于、包含或被包含于其中任何一个。"""
    s = plain(lexicon["symptoms"].get("words", []))
    for conf in lexicon["scenes"].values():
        s |= plain(conf.get("triggers", []))
    for v in lexicon.get("scene_exclusions", {}).values():
        s |= plain(v)
    for v in lexicon["anchor_patterns"].values():
        s |= plain(v)
    fr = lexicon.get("feature_name_rule", {})
    s |= plain(fr.get("repair_verbs", []) + fr.get("diagnose_verbs", []))
    s |= plain(lexicon.get("bad_smells", []) + lexicon.get("imperatives", []) + lexicon.get("content_nouns", []))
    for b in ("scene_triggers", "anchor_patterns", "diagnose_verbs"):
        s |= {key(e["text"]) for e in entries(lexicon, b)}  # 候选与生效都算
    return s


def find_learned(lexicon, text):
    """[(bucket, entry)]：学习层里同文本的条目。"""
    k = key(text)
    out = []
    for b in PRUNABLE:
        for e in entries(lexicon, b):
            if key(e["text"]) == k:
                out.append((b, e))
    return out


def rejected_reason(lexicon, text):
    k = key(text)
    for e in entries(lexicon, "rejected"):
        if key(e["text"]) == k:
            return e.get("reason", "未注明")
    return None


def guard_add(lexicon, kind, text, attr):
    """返回 None 放行；抛 Skip 跳过；返回字符串为拒绝原因。"""
    k = key(text)
    why = rejected_reason(lexicon, text)
    if why is not None:
        return "「%s」已被判错拉黑（reason: %s），确需恢复先 remove --kind rejected" % (text, why)
    for b, e in find_learned(lexicon, text):
        if b != BUCKET[kind]:
            return "「%s」已学为 %s，不能再学成 %s（语义冲突）；确认学错先 remove" % (text, b, kind)
    coll = seed_colloquial(lexicon)
    if kind == "colloquial":
        if len(text) < 2 and attr != "语气词":
            return "单字口水词只允许「语气词」类"
        if k in coll:
            raise Skip("「%s」已在种子层 colloquial" % text)
        info = informative(lexicon)
        for w in info:
            if not w:
                continue
            if k == w:
                return "「%s」是有信息量的词，学成口水词会把事实删掉" % text
            if inside(w, k):
                return "「%s」包含信息词「%s」，学成口水词会把事实删掉" % (text, w)
            if inside(k, w):
                return "「%s」是信息词「%s」的一部分，按字面删除会破坏事实" % (text, w)
        return None
    if k in coll:
        return "「%s」已是口水词，不能再作为 %s 学习（语义冲突）" % (text, kind)
    if kind == "scene-trigger":
        if len(text) < 2:
            return "触发词不许单字"
        if k in plain(lexicon.get("generic_words", [])):
            return "「%s」过泛，不能当触发词" % text
        for v in lexicon.get("scene_exclusions", {}).values():
            if k in plain(v):
                return "「%s」是排除词，不能当触发词" % text
        same = seed_triggers(lexicon, attr)
        if k in same:
            raise Skip("「%s」已在种子层 %s 触发词" % (text, attr))
        for w in same:
            if w and inside(w, k):
                raise Skip("「%s」已含 %s 的种子触发词「%s」，视为重复" % (text, attr, w))
        for sc in lexicon["scenes"]:
            if sc != attr and k in seed_triggers(lexicon, sc):
                return "「%s」已是 %s 的种子触发词，不能再归 %s" % (text, sc, attr)
        return None
    if kind == "diagnose-verb":
        if len(text) < 2:
            return "诊断动词不许单字"
        fr = lexicon.get("feature_name_rule", {})
        if k in plain(fr.get("diagnose_verbs", [])):
            raise Skip("「%s」已在种子层 diagnose_verbs" % text)
        if k in plain(fr.get("repair_verbs", [])):
            return "「%s」是修复动词，不能当诊断动词（语义冲突）" % text
        if k in plain(lexicon.get("generic_words", [])):
            return "「%s」过泛，不能当诊断动词" % text
        for sc in lexicon["scenes"]:
            if k in seed_triggers(lexicon, sc):
                return "「%s」是 %s 的种子触发词，不能当诊断动词" % (text, sc)
        for a in lexicon["anchor_patterns"]:
            if k in seed_anchor(lexicon, a):
                return "「%s」是 anchor_patterns.%s 的标记，不能当诊断动词" % (text, a)
        return None
    # anchor-pattern
    if len(text) < 2:
        return "锚点标记不许单字"
    if k in seed_anchor(lexicon, attr):
        raise Skip("「%s」已在种子层 anchor_patterns.%s" % (text, attr))
    for a in lexicon["anchor_patterns"]:
        if a != attr and k in seed_anchor(lexicon, a):
            return "「%s」已是 anchor_patterns.%s 的标记，不能再归 %s" % (text, a, attr)
    for sc in lexicon["scenes"]:
        if k in seed_triggers(lexicon, sc):
            return "「%s」是 %s 的种子触发词，不能当锚点标记" % (text, sc)
    return None


# ---------------------------------------------------------------- 子命令
def need_of(lexicon):
    return int(lexicon.get("active_min_count", DEFAULT_ACTIVE_MIN_COUNT))


def cmd_add(args):
    text = norm(args.text)
    check_text(text)
    attr_field = ATTR_FIELD[args.kind]
    attr = getattr(args, attr_field) if attr_field else None
    if attr_field and not attr:
        fail("add %s 必须给 --%s" % (args.kind, attr_field))
    try:
        with editing() as lex:
            if args.kind == "colloquial" and attr not in lex["colloquial"]:
                fail("未知口水词类别「%s」，现有: %s" % (attr, list(lex["colloquial"])))
            if args.kind == "scene-trigger" and attr not in lex["scenes"]:
                fail("未知场景「%s」，现有: %s" % (attr, list(lex["scenes"])))
            if args.kind == "anchor-pattern" and attr not in LEARNABLE_ANCHORS:
                fail("锚点只能是 %s" % "、".join(LEARNABLE_ANCHORS))
            reason = guard_add(lex, args.kind, text, attr)
            if reason:
                fail(reason)
            bucket_name = BUCKET[args.kind]
            bucket = lex["learned"][bucket_name]
            for e in entries(lex, bucket_name):
                if key(e["text"]) == key(text):
                    if attr_field and e.get(attr_field) != attr:
                        fail("「%s」已学为 %s=%s，与本次 %s 冲突；确认学错先 remove 再学"
                             % (text, attr_field, e.get(attr_field), attr))
                    e["count"] += 1
                    e["last_seen_at"] = now_str()
                    state = "生效" if e["count"] >= need_of(lex) else "候选"
                    print("已存在，计数 +1: [%s] %s (count=%d，%s)" % (bucket_name, e["text"], e["count"], state))
                    return
            if len(bucket) >= MAX_PER_BUCKET:
                need = need_of(lex)
                cands = [e for e in entries(lex, bucket_name) if e["count"] < need]
                if not cands:
                    fail("learned.%s 已满 %d 条且全部生效，拒绝新增；请先 prune" % (bucket_name, MAX_PER_BUCKET))
                victim = min(cands, key=lambda e: (e["count"], e.get("last_seen_at") or e.get("added_at") or ""))
                bucket.remove(victim)
                print("已满 %d 条，淘汰最旧的候选: %s" % (MAX_PER_BUCKET, victim["text"]))
            entry = {"text": text, "count": 1, "added_at": now_str(), "source": args.source or "未注明"}
            if attr_field:
                entry[attr_field] = attr
            bucket.append(entry)
            print("已学习: [%s] %s (count=1，未达生效门槛 %d，当前为候选)" % (bucket_name, text, need_of(lex)))
    except Skip as s:
        print("跳过: %s" % s)
        sys.exit(EXIT_SKIP)


def cmd_correct(args):
    wrong, right = norm(args.wrong), norm(args.right)
    check_text(wrong, "--wrong ")
    check_text(right, "--right ")
    if len(wrong) < 2:
        fail("--wrong 至少 2 个字，单字纠错会误伤")
    if key(wrong) == key(right):
        fail("纠错前后内容相同，无需记录")
    if args.anchor not in CORRECTION_ANCHORS:
        fail("纠错只作用于代推行，--anchor 只能是 x:")
    if len(NEG_RE.findall(wrong)) != len(NEG_RE.findall(right)):
        fail("纠错前后否定词个数不同，不能用纠错翻转否定")
    with editing() as lex:
        for t in (wrong, right):
            why = rejected_reason(lex, t)
            if why is not None:
                fail("「%s」已被判错拉黑（reason: %s），不能用于纠错" % (t, why))
        for e in entries(lex, "corrections"):
            if key(e["wrong"]) == key(wrong) and e["anchor"] == args.anchor:
                e["right"] = right
                e["count"] += 1
                e["last_seen_at"] = now_str()
                print("已更新纠错规则: [%s] %s → %s (count=%d)" % (args.anchor, wrong, right, e["count"]))
                return
        lex["learned"]["corrections"].append({"wrong": wrong, "right": right, "anchor": args.anchor, "count": 1,
                                              "added_at": now_str(), "source": args.source or "未注明"})
        print("已学习纠错规则: [%s] %s → %s (count=1)" % (args.anchor, wrong, right))


def cmd_reject(args):
    text = norm(args.text)
    check_text(text)
    with editing() as lex:
        learned = lex["learned"]
        removed = 0
        for b in PRUNABLE:
            kept = [e for e in learned[b] if not (valid(e, b) and key(e["text"]) == key(text))]
            removed += len(learned[b]) - len(kept)
            learned[b] = kept
        kept = [e for e in learned["corrections"]
                if not (valid(e, "corrections") and key(text) in (key(e["wrong"]), key(e["right"])))]
        removed += len(learned["corrections"]) - len(kept)
        learned["corrections"] = kept
        for e in entries(lex, "rejected"):
            if key(e["text"]) == key(text):
                e["reason"] = args.reason or e.get("reason", "未注明")
                e["last_rejected_at"] = now_str()
                print("已在拉黑区: %s（reason: %s）" % (text, e["reason"]))
                return
        learned["rejected"].append({"text": text, "reason": args.reason or "未注明", "added_at": now_str(),
                                    "source": args.source or "未注明"})
        print("已拉黑: %s（同步删除学习层相关条目 %d 条），此后 add 一律拒绝" % (text, removed))


def cmd_remove(args):
    text = norm(args.text)
    check_text(text)
    bucket_name = BUCKET[args.kind]
    with editing() as lex:
        bucket = lex["learned"][bucket_name]
        for i, e in enumerate(bucket):
            if not valid(e, bucket_name):
                continue
            field = "wrong" if bucket_name == "corrections" else "text"
            if key(e[field]) != key(text):
                continue
            if args.anchor and e.get("anchor") != args.anchor:
                continue
            del bucket[i]
            print("已删除: [%s] %s" % (bucket_name, text))
            return
        fail("[%s] 中未找到「%s」" % (bucket_name, text))


def describe(e, bucket):
    if bucket == "corrections":
        return "%s → %s  [%s]" % (e["wrong"], e["right"], e["anchor"])
    if bucket == "rejected":
        return "%s  reason=%s" % (e["text"], e.get("reason", "未注明"))
    if bucket == "diagnose_verbs":
        return "%s  诊断动词" % e["text"]
    return "%s  %s" % (e["text"], e.get("category") or e.get("scene") or e.get("anchor"))


def cmd_list(args):
    lex = reading()
    need = need_of(lex)
    buckets = [BUCKET[args.kind]] if args.kind else list(REQUIRED_FIELDS)
    for b in buckets:
        items = entries(lex, b, quiet=False)
        print("[%s] 共 %d 条（生效门槛 count>=%d）" % (b, len(items), need))
        for e in sorted(items, key=lambda x: -x.get("count", 0)):
            if b == "corrections":
                tag = "[纠错]"
            elif b == "rejected":
                tag = "[拉黑]"
            else:
                tag = "[生效]" if e["count"] >= need else "[候选]"
            cnt = "" if b == "rejected" else "  count=%d" % e["count"]
            print("  %s %s%s  %s" % (tag, describe(e, b), cnt, e.get("source", "")))


def cmd_active(args):
    lex = reading()
    need = need_of(lex)
    print("生效门槛: count >= %d（低于门槛只是候选，不参与转写）" % need)
    total = 0
    for b in PRUNABLE:
        items = entries(lex, b, quiet=False)
        act = [e for e in items if e["count"] >= need]
        cand = [e for e in items if e["count"] < need]
        total += len(act)
        print("[%s] 生效 %d 条，候选 %d 条" % (b, len(act), len(cand)))
        for e in sorted(act, key=lambda x: -x["count"]):
            print("  [生效] %s  count=%d" % (describe(e, b), e["count"]))
        for e in sorted(cand, key=lambda x: -x["count"]):
            print("  [候选] %s  count=%d" % (describe(e, b), e["count"]))
    print("[corrections] %d 条（无门槛，只作用于代推行）" % len(entries(lex, "corrections")))
    print("[rejected] %d 条（永久拒学）" % len(entries(lex, "rejected")))
    print("共生效 %d 条学习层词条" % total)


def cmd_prune(args):
    if args.min_count < 1:
        fail("--min-count 必须 >= 1（当前 %d），否则会一次清空全部词条" % args.min_count)
    with editing() as lex:
        total = 0
        for b in PRUNABLE:
            bucket = lex["learned"][b]
            kept = [e for e in bucket if not valid(e, b) or e["count"] >= args.min_count]
            removed = len(bucket) - len(kept)
            lex["learned"][b] = kept
            total += removed
            print("[%s] 淘汰 count<%d 的 %d 条，保留 %d 条" % (b, args.min_count, removed, len(kept)))
        print("[corrections] 保留 %d 条（不参与淘汰）" % len(lex["learned"]["corrections"]))
        print("[rejected] 保留 %d 条（不参与淘汰）" % len(lex["learned"]["rejected"]))
        print("清理完成，共淘汰 %d 条" % total)


def main():
    ap = argparse.ArgumentParser(description="baihua-to-anchor %s 词库学习工具" % VERSION)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="新增学习词条（纠错请用 correct）")
    p.add_argument("kind", choices=ADD_KINDS)
    p.add_argument("--text", required=True)
    p.add_argument("--category")
    p.add_argument("--scene")
    p.add_argument("--anchor")
    p.add_argument("--source")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("correct", help="纠错规则：下次代推时用 right 替代 wrong")
    p.add_argument("--wrong", required=True)
    p.add_argument("--right", required=True)
    p.add_argument("--anchor", default="x:")
    p.add_argument("--source")
    p.set_defaults(func=cmd_correct)

    p = sub.add_parser("reject", help="判错拉黑")
    p.add_argument("--text", required=True)
    p.add_argument("--reason")
    p.add_argument("--source")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("remove", help="删除学习层条目（correction 按 wrong 匹配）")
    p.add_argument("--kind", required=True, choices=ALL_KINDS)
    p.add_argument("--text", required=True)
    p.add_argument("--anchor")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("list", help="列出学习层（可限定类型）")
    p.add_argument("kind", nargs="?", choices=ALL_KINDS)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("active", help="只看生效与候选")
    p.set_defaults(func=cmd_active)

    p = sub.add_parser("prune", help="淘汰低频候选")
    p.add_argument("--min-count", type=int, default=DEFAULT_ACTIVE_MIN_COUNT)
    p.set_defaults(func=cmd_prune)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
