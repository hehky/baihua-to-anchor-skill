#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_learn.py — baihua-to-anchor 3.0.0 learn.py 回归

全部用例跑在临时词库副本上（BAIHUA_LEXICON），副本的学习层先清空，
所以真实 lexicon.json 学到多少东西都不影响本测试；测试结束校验真实词库未被改动。
用法：python3 tests/test_learn.py
"""
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEARN = ROOT / "scripts" / "learn.py"
REAL = ROOT / "lexicon.json"
SKILL = ROOT / "SKILL.md"
README = ROOT / "README.md"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or not detail else "  -> " + detail))


REAL_BYTES = REAL.read_bytes()
REAL_MTIME = REAL.stat().st_mtime_ns
TMP = Path(tempfile.mkdtemp(prefix="baihua-learn-"))


def fresh(name="lexicon.json", mutate=None):
    """生成学习层为空的临时词库副本，返回路径与环境。"""
    data = json.loads(REAL_BYTES.decode("utf-8"))
    for k in data["learned"]:
        data["learned"][k] = []
    if mutate:
        mutate(data)
    path = TMP / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, dict(os.environ, BAIHUA_LEXICON=str(path))


LEX, ENV = fresh()


def run(*argv, env=None):
    return subprocess.run([sys.executable, str(LEARN), *argv], capture_output=True, text=True, env=env or ENV)


def data(path=None):
    return json.loads((path or LEX).read_text(encoding="utf-8"))


def count(bucket, text, path=None):
    for e in data(path)["learned"][bucket]:
        if e.get("text") == text:
            return e["count"]
    return 0


try:
    # ---- 基本 add / 计数 / 生效门槛
    r = run("add", "colloquial", "--text", "劳驾", "--category", "客套", "--source", "测试")
    check("add colloquial 成功", r.returncode == 0, r.stderr)
    check("新词条 count=1 为候选", count("colloquial", "劳驾") == 1 and "候选" in r.stdout)
    r = run("add", "colloquial", "--text", "劳驾", "--category", "客套")
    check("重复 add 计数 +1 且提示生效", count("colloquial", "劳驾") == 2 and "生效" in r.stdout)
    r = run("add", "colloquial", "--text", "劳驾", "--category", "填充词")
    check("同词不同类别被拒（返回码 1）", r.returncode == 1 and "冲突" in r.stderr)

    # ---- 种子层：同类跳过返回码 2，别类冲突返回码 1
    r = run("add", "colloquial", "--text", "你好", "--category", "问候语")
    check("同类种子词跳过，返回码 2", r.returncode == 2 and "跳过" in r.stdout)
    r = run("add", "scene-trigger", "--text", "打不开", "--scene", "排障")
    check("排障种子症状词跳过，返回码 2", r.returncode == 2)
    r = run("add", "scene-trigger", "--text", "崩溃了", "--scene", "排障")
    check("已含同场景种子词的触发词视为重复，返回码 2", r.returncode == 2)
    r = run("add", "scene-trigger", "--text", "麻烦", "--scene", "排障")
    check("种子口水词不能学成触发词", r.returncode == 1)
    r = run("add", "anchor-pattern", "--text", "崩溃", "--anchor", "q:")
    check("种子触发词不能学成锚点标记（跨层去重）", r.returncode == 1)
    r = run("add", "anchor-pattern", "--text", "务必", "--anchor", "y:")
    check("别的锚点的种子标记不能改归属", r.returncode == 1)

    # ---- 口水词双向防污染
    for t, why in [("报错", "等于信息词"), ("报错啦", "包含信息词"), ("帮忙部署", "包含触发词"),
                   ("文件", "实义名词"), ("错", "信息词子串/单字"), ("报错率", "排除词")]:
        r = run("add", "colloquial", "--text", t, "--category", "填充词")
        check("口水词防污染拒绝「%s」（%s）" % (t, why), r.returncode == 1)
    check("被拒的词未写入学习层", all(count("colloquial", t) == 0 for t in ["报错", "报错啦", "帮忙部署", "文件"]))
    r = run("add", "colloquial", "--text", "呗", "--category", "语气词")
    check("单字语气词允许学习", r.returncode == 0, r.stderr)
    r = run("add", "colloquial", "--text", "嗐", "--category", "问候语")
    check("单字非语气词被拒", r.returncode == 1)
    r = run("add", "scene-trigger", "--text", "跑飞了", "--scene", "排障")
    r2 = run("add", "colloquial", "--text", "跑飞了", "--category", "填充词")
    check("学习层的候选触发词也不能学成口水词", r.returncode == 0 and r2.returncode == 1)

    # ---- 触发词防污染
    for t in ["报", "错误", "周报", "超时"]:
        r = run("add", "scene-trigger", "--text", t, "--scene", "排障")
        check("触发词拒绝单字/泛词/排除词「%s」" % t, r.returncode == 1)
    r = run("add", "scene-trigger", "--text", "崩溃", "--scene", "部署")
    check("别的场景的种子触发词不能改归属", r.returncode == 1)
    r = run("add", "scene-trigger", "--text", "炸了", "--scene", "测试")
    check("未知场景被拒", r.returncode == 1)
    r = run("add", "anchor-pattern", "--text", "十万火急", "--anchor", "r:")
    check("锚点限 i: o: h: y: q: j:（r: 被拒）", r.returncode == 1)
    r = run("add", "anchor-pattern", "--text", "十万火急", "--anchor", "q:")
    check("合法锚点标记可学", r.returncode == 0, r.stderr)

    # ---- 诊断动词桶
    r = run("add", "diagnose-verb", "--text", "瞄一眼", "--source", "测试")
    check("add diagnose-verb 成功且为候选", r.returncode == 0 and count("diagnose_verbs", "瞄一眼") == 1 and "候选" in r.stdout, r.stderr)
    r = run("add", "diagnose-verb", "--text", "瞄一眼")
    check("diagnose-verb 重复 add 计数 +1 生效", count("diagnose_verbs", "瞄一眼") == 2 and "生效" in r.stdout)
    r = run("add", "diagnose-verb", "--text", "瞅瞅")
    check("种子诊断动词跳过，返回码 2", r.returncode == 2)
    for t, why in [("瞄", "单字"), ("修复", "修复动词"), ("帮我", "口水词"), ("部署", "触发词"), ("不对", "泛词"), ("务必", "锚点标记")]:
        r = run("add", "diagnose-verb", "--text", t)
        check("诊断动词拒绝「%s」（%s）" % (t, why), r.returncode == 1)
    r = run("add", "colloquial", "--text", "瞄一眼", "--category", "填充词")
    check("学习层诊断动词不能学成口水词", r.returncode == 1)
    r = run("add", "scene-trigger", "--text", "瞄一眼", "--scene", "排障")
    check("学习层诊断动词不能改学成触发词", r.returncode == 1 and "冲突" in r.stderr)
    r = run("list", "diagnose-verb")
    check("list diagnose-verb 过滤", r.returncode == 0 and "[生效] 瞄一眼  诊断动词" in r.stdout and "[colloquial]" not in r.stdout)
    r = run("remove", "--kind", "diagnose-verb", "--text", "瞄一眼")
    check("remove --kind diagnose-verb", r.returncode == 0 and count("diagnose_verbs", "瞄一眼") == 0, r.stderr)

    def no_dv(d):
        del d["learned"]["diagnose_verbs"]
    p9, env9 = fresh("nodv.json", no_dv)
    r = run("add", "diagnose-verb", "--text", "瞄一眼", env=env9)
    check("旧词库缺 diagnose_verbs 桶时自动补齐", r.returncode == 0 and count("diagnose_verbs", "瞄一眼", p9) == 1, r.stderr)

    # ---- 文本规范化
    r = run("add", "colloquial", "--text", "a\nb", "--category", "填充词")
    check("含换行被拒", r.returncode == 1)
    r = run("add", "colloquial", "--text", "x" * 51, "--category", "填充词")
    check("超过 50 字被拒", r.returncode == 1)
    r = run("add", "anchor-pattern", "--text", "Ｂｕｎ", "--anchor", "h:")
    r2 = run("add", "anchor-pattern", "--text", "bun", "--anchor", "h:")
    check("NFKC + 不分大小写去重（Ｂｕｎ 与 bun 计为同一条）",
          r.returncode == 0 and r2.returncode == 0 and count("anchor_patterns", "Bun") == 2, r.stderr + r2.stderr)
    r = run("reject", "--text", "y" * 51)
    check("reject 也有长度上限", r.returncode == 1)

    # ---- add correction 已禁止
    r = run("add", "correction", "--text", "foo")
    check("add correction 被禁止（参数校验失败）", r.returncode != 0)
    check("禁止后没写出坏纠错条目", data()["learned"]["corrections"] == [])

    # ---- correct / remove correction
    r = run("correct", "--wrong", "定位并修复", "--right", "先复现再修复")
    check("correct 写入成功", r.returncode == 0, r.stderr)
    r = run("correct", "--wrong", "定位并修复", "--right", "先复现后修复")
    corr = data()["learned"]["corrections"]
    check("correct 同 wrong 走更新路径", len(corr) == 1 and corr[0]["right"] == "先复现后修复" and corr[0]["count"] == 2)
    r = run("correct", "--wrong", "不", "--right", "要")
    check("correct 拒绝单字 wrong", r.returncode == 1)
    r = run("correct", "--wrong", "不动数据库", "--right", "动数据库")
    check("correct 拒绝翻转否定", r.returncode == 1 and "否定" in r.stderr)
    r = run("correct", "--wrong", "定位并修复", "--right", "先复现", "--anchor", "y:")
    check("correct 只允许 x:", r.returncode == 1)
    r = run("list", "correction")
    check("list correction 可用", r.returncode == 0 and "[纠错]" in r.stdout)
    r = run("remove", "--kind", "correction", "--text", "定位并修复")
    check("remove correction 按 wrong 删除", r.returncode == 0 and data()["learned"]["corrections"] == [], r.stderr)
    r = run("remove", "--kind", "correction", "--text", "定位并修复")
    check("remove 不存在的纠错报错", r.returncode == 1)

    # ---- reject / rejected
    r = run("reject", "--text", "跑飞了", "--reason", "测试")
    check("reject 删除学习层条目并拉黑", r.returncode == 0 and count("scene_triggers", "跑飞了") == 0
          and any(e["text"] == "跑飞了" for e in data()["learned"]["rejected"]))
    r = run("add", "scene-trigger", "--text", "跑飞了", "--scene", "排障")
    check("拉黑后 add 被拒", r.returncode == 1 and "拉黑" in r.stderr)
    r = run("correct", "--wrong", "跑飞了", "--right", "跑偏了")
    check("拉黑词不能用于纠错", r.returncode == 1)
    r = run("list", "rejected")
    check("list rejected 过滤", r.returncode == 0 and "[拉黑] 跑飞了" in r.stdout and "[colloquial]" not in r.stdout)
    r = run("remove", "--kind", "rejected", "--text", "跑飞了")
    check("remove --kind rejected 恢复", r.returncode == 0)
    r = run("add", "scene-trigger", "--text", "跑飞了", "--scene", "排障")
    check("恢复后可再学", r.returncode == 0)

    # ---- active / list 区分生效与候选
    r = run("active")
    check("active 标出生效项", r.returncode == 0 and "[生效] 劳驾" in r.stdout)
    check("active 标出候选项", "[候选] 跑飞了" in r.stdout and "[生效] 跑飞了" not in r.stdout)

    # ---- prune
    r = run("prune", "--min-count", "0")
    check("prune --min-count 0 拒绝", r.returncode == 1 and ">= 1" in r.stderr)
    r = run("correct", "--wrong", "定位并修复", "--right", "先复现再修复")
    r = run("prune")
    check("prune 输出措辞清楚", "淘汰 count<2 的" in r.stdout)
    check("prune 淘汰候选、保留生效", count("scene_triggers", "跑飞了") == 0 and count("colloquial", "劳驾") == 2)
    check("prune 不动纠错区", len(data()["learned"]["corrections"]) == 1)

    # ---- 满额淘汰：只淘汰候选，不淘汰本次新词
    def full_mixed(d):
        d["learned"]["colloquial"] = [{"text": "填%03d" % i, "category": "填充词", "count": 1 if i < 3 else 5,
                                       "added_at": "2026-10-0%dT00:00:00+08:00" % (i % 9 + 1)} for i in range(200)]
    p2, env2 = fresh("full1.json", full_mixed)
    r = run("add", "colloquial", "--text", "新口水", "--category", "填充词", env=env2)
    texts = [e["text"] for e in data(p2)["learned"]["colloquial"]]
    check("满额时新词留下", "新口水" in texts and len(texts) == 200, r.stdout + r.stderr)
    check("满额时淘汰的是最旧的候选", "填000" not in texts and "填001" in texts and "填100" in texts)

    def full_active(d):
        d["learned"]["colloquial"] = [{"text": "满%03d" % i, "category": "填充词", "count": 3} for i in range(200)]
    p3, env3 = fresh("full2.json", full_active)
    r = run("add", "colloquial", "--text", "新口水", "--category", "填充词", env=env3)
    check("满额且全部生效时拒绝新增并提示 prune", r.returncode == 1 and "prune" in r.stderr)

    # ---- 坏数据容错
    def broken(d):
        d["learned"]["colloquial"] = [{"text": "缺计数", "category": "填充词"}, {"category": "填充词", "count": 2},
                                      {"text": "好条目", "category": "填充词", "count": 2}]
        d["learned"]["corrections"] = [{"text": "foo", "count": 1}]
    p4, env4 = fresh("broken.json", broken)
    r = run("list", env=env4)
    check("缺字段条目：list 不崩溃并告警", r.returncode == 0 and "警告" in r.stderr and "好条目" in r.stdout)
    r = run("add", "colloquial", "--text", "好条目", "--category", "填充词", env=env4)
    check("缺字段条目：add 不崩溃", r.returncode == 0, r.stderr)
    check("缺字段条目：写回时原样保留", len(data(p4)["learned"]["colloquial"]) == 3)

    for name, mut, what in [
        ("bad1.json", lambda d: d.update(schema_version=2), "schema_version=2"),
        ("bad2.json", lambda d: d.update(schema_version=3.0), "schema_version=3.0（float）"),
        ("bad3.json", lambda d: d.update(learned="oops"), "learned 不是对象"),
        ("bad4.json", lambda d: d["learned"].update(colloquial={}), "桶不是数组"),
    ]:
        p5, env5 = fresh(name, mut)
        r = run("list", env=env5)
        check("坏结构可读报错：%s" % what, r.returncode == 1 and "Traceback" not in r.stderr and "错误" in r.stderr)

    # ---- 文件权限与锁
    p6, env6 = fresh("perm.json")
    os.chmod(p6, 0o644)
    run("add", "colloquial", "--text", "劳驾", "--category", "客套", env=env6)
    check("写入后保留原文件权限 0644", stat.S_IMODE(os.stat(p6).st_mode) == 0o644)
    os.chmod(p6, 0o640)
    run("add", "colloquial", "--text", "劳驾", "--category", "客套", env=env6)
    check("写入后保留原文件权限 0640", stat.S_IMODE(os.stat(p6).st_mode) == 0o640)
    check("没有残留临时文件", not list(TMP.glob(".lexicon-*.tmp")))

    ro_dir = TMP / "ro"
    ro_dir.mkdir()
    p7 = ro_dir / "lexicon.json"
    shutil.copy(LEX, p7)
    os.chmod(ro_dir, 0o555)
    env7 = dict(os.environ, BAIHUA_LEXICON=str(p7))
    r = run("list", env=env7)
    can_write = os.access(ro_dir, os.W_OK)  # root 下目录仍可写，此时只校验不崩溃
    check("只读目录下 list 不崩溃（无锁只读）", r.returncode == 0 and "Traceback" not in r.stderr)
    r = run("add", "colloquial", "--text", "劳驾", "--category", "客套", env=env7)
    check("只读目录下写命令给出可读错误", can_write or (r.returncode == 1 and "Traceback" not in r.stderr))
    os.chmod(ro_dir, 0o755)

    procs = [subprocess.Popen([sys.executable, str(LEARN), "add", "colloquial", "--text", "并发词",
                               "--category", "填充词"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
             for _ in range(6)]
    procs += [subprocess.Popen([sys.executable, str(LEARN), "add", "colloquial", "--text", "并发%s" % c,
                                "--category", "填充词"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
              for c in "ABCD"]
    for p in procs:
        p.wait()
    check("并发：同词 6 次计数不丢", count("colloquial", "并发词") == 6)
    check("并发：4 个不同词全部落库", all(count("colloquial", "并发" + c) == 1 for c in "ABCD"))
    check("updated_at 为 ISO 8601", re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$", data()["updated_at"]))

    # ---- SKILL.md §13.4 的命令按顺序都能跑通
    p8, env8 = fresh("skillcmds.json")
    skill = SKILL.read_text(encoding="utf-8")
    block = re.search(r"### 13\.4.*?```bash\n(.*?)```", skill, re.S).group(1)
    cmds = [ln for ln in block.splitlines() if ln.strip()]
    check("SKILL.md §13.4 有命令", len(cmds) >= 10)
    for ln in cmds:
        argv = shlex.split(ln.replace("<skill目录>", str(ROOT)), comments=True)
        r = subprocess.run([sys.executable] + argv[1:], capture_output=True, text=True, env=env8)
        check("SKILL.md 命令可跑: %s" % " ".join(argv[2:5]), r.returncode == 0, r.stderr.strip())
    readme = README.read_text(encoding="utf-8")
    for ln in re.findall(r"^python3 scripts/learn\.py .*$", readme, re.M):
        argv = shlex.split(ln, comments=True)
        r = subprocess.run([sys.executable, str(LEARN)] + argv[2:], capture_output=True, text=True, env=env8)
        check("README 命令可跑: %s" % " ".join(argv[2:5]), r.returncode in (0, 2), r.stderr.strip())

    r = run("--version")
    check("--version 输出 3.0.0", r.stdout.strip() == "3.0.0")
finally:
    check("真实 lexicon.json 内容未被改动", REAL.read_bytes() == REAL_BYTES)
    check("真实 lexicon.json mtime 未被改动", REAL.stat().st_mtime_ns == REAL_MTIME)
    check("真实词库旁没有残留锁文件以外的临时文件", not list(REAL.parent.glob(".lexicon-*.tmp")))
    shutil.rmtree(TMP, ignore_errors=True)
    check("临时目录已删除", not TMP.exists())

total = len(results) + 2
skill_text = SKILL.read_text(encoding="utf-8")
readme_text = README.read_text(encoding="utf-8")
check("SKILL.md 写的 test_learn 断言数与实际一致", "`test_learn.py` 断言 %d 项" % total in skill_text, "应为 %d" % total)
check("README 写的 test_learn 断言数与实际一致", "test_learn.py`：断言 %d 项" % total in readme_text)
failed = [n for n, ok in results if not ok]
print("---")
print("断言 %d 项，失败 %d 项" % (len(results), len(failed)))
for n in failed:
    print("  FAIL " + n)
sys.exit(1 if failed else 0)
