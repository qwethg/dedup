# -*- coding: utf-8 -*-
"""Verify dedup logic fixes and file-type settings in app.py (no server)."""
import os, sys, tempfile, json
import app

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {name}")
    else: failed += 1; print(f"  FAIL  {name}")

# ── 1. get_file_type_set ──
print("[1] get_file_type_set")
check("'all' -> None (no filter)", app.get_file_type_set({"file_types": "all"}) is None)
check("'work' -> preset set", app.get_file_type_set({"file_types": "work"}) == app.FILE_TYPE_PRESETS["work"])
check("missing key -> work preset", app.get_file_type_set({}) == app.FILE_TYPE_PRESETS["work"])
custom = app.get_file_type_set({"file_types": ["pdf", "*.DOCX", " .Xls "]})
check("custom list normalized", custom == {".pdf", ".docx", ".xls"})
check("empty list -> None (no filter)", app.get_file_type_set({"file_types": []}) is None)

# ── 2. path helpers ──
print("[2] path boundary / case")
check("inside protected dir", app._is_under(r"D:\docs\a\f.pdf", r"D:\docs"))
check("sibling prefix NOT matched", not app._is_under(r"D:\docs2\f.pdf", r"D:\docs"))
check("case-insensitive on Windows", app._is_under(r"d:\DOCS\f.pdf", r"D:\docs") == (os.name == "nt"))
cfg_p = {"protected_folders": [r"D:\docs"]}
check("is_protected inside", app.is_protected(r"D:\docs\x\a.pdf", cfg_p))
check("is_protected sibling false", not app.is_protected(r"D:\docs2\a.pdf", cfg_p))

# ── 3. normalize_name (similar mode) ──
print("[3] normalize_name")
check("'报告 (1).docx' -> '报告.docx'", app.normalize_name("报告 (1).docx") == "报告.docx")
check("'报告-副本.docx' -> '报告.docx'", app.normalize_name("报告-副本.docx") == "报告.docx")
check("'报告（2）.docx' -> '报告.docx'", app.normalize_name("报告（2）.docx") == "报告.docx")
check("'plan_copy.pdf' -> 'plan.pdf'", app.normalize_name("plan_copy.pdf") == "plan.pdf")
check("'a (1) (2).txt' -> 'a.txt'", app.normalize_name("a (1) (2).txt") == "a.txt")
check("plain name unchanged", app.normalize_name("报告.docx") == "报告.docx")

# ── 4. scan_files grouping (build a temp tree) ──
print("[4] scan_files grouping & savings")
tmp = tempfile.mkdtemp()
def mkfile(rel, content):
    p = os.path.join(tmp, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh: fh.write(content)
    return p

# same name (diff case) + same size + SAME content -> exact-mode group
mkfile("a/Report.pdf", b"x" * 2048)
mkfile("b/report.pdf", b"x" * 2048)
# same name + same size but DIFFERENT content -> must NOT group (exact verifies content)
mkfile("c/report.pdf", b"y" * 2048)
# same name, different size -> similar-mode group; oldest kept for savings
p_old = mkfile("d/plan.docx", b"z" * 1024)
p_new = mkfile("e/plan.docx", b"z" * 4096)
os.utime(p_old, (1000000, 1000000))   # older
os.utime(p_new, (2000000, 2000000))   # newer
# copy-suffix variants -> similar-mode normalized-name group
mkfile("f/汇总.docx", b"s" * 2048)
mkfile("g/汇总 (1).docx", b"s" * 2048)
mkfile("h/汇总-副本.docx", b"t" * 3072)
# unique file
mkfile("i/uniq.pdf", b"u" * 2048)

cfg = {"folders": [{"path": tmp, "label": "T"}], "protected_folders": [],
       "file_types": "all", "min_size_kb": 1, "scan_mode": "exact"}
app.scan_state.update({"progress": 0, "current_path": "", "cancel": False})

res = app.scan_files(cfg)
check("exact: same name+size+content groups", res["total_groups"] == 1)
check("exact: group has exactly 2 files (3rd differs in content)",
      res["total_groups"] == 1 and res["groups"][0]["count"] == 2)
check("exact: savings = size of one copy", res["groups"][0]["savings"] == 2048)

cfg["scan_mode"] = "content"
app.scan_state.update({"progress": 0, "cancel": False})
res = app.scan_files(cfg)
# x-group (Report.pdf x2) and s-group (汇总.docx + 汇总 (1).docx) both have identical content
grp_x = next((g for g in res["groups"] if g["filename"].lower() == "report.pdf"), None)
check("content: identical content groups regardless of name/case",
      grp_x is not None and grp_x["count"] == 2)
# c/report.pdf has same name+size but different content -> must not appear in any group
c_path = os.path.normpath(os.path.join(tmp, "c", "report.pdf")).lower()
check("content: y-variant (same name+size, diff content) excluded",
      all(f["path"].lower() != c_path for g in res["groups"] for f in g["files"]))

cfg["scan_mode"] = "similar"
app.scan_state.update({"progress": 0, "cancel": False})
res = app.scan_files(cfg)
grp_plan = next((g for g in res["groups"] if g["filename"] == "plan.docx"), None)
grp_hz = next((g for g in res["groups"] if g["filename"] == "汇总.docx"), None)
check("similar: plan.docx group exists", grp_plan is not None)
check("similar: is_similar flag", grp_plan and grp_plan["is_similar"] is True)
check("similar: savings keeps oldest (files[0])",
      grp_plan and grp_plan["savings"] == 4096 and grp_plan["files"][0]["size"] == 1024)
check("similar: copy-suffix variants grouped (3 files)",
      grp_hz is not None and grp_hz["count"] == 3)

# ── 5. cancel support ──
print("[5] cancel")
cfg["scan_mode"] = "exact"
app.scan_state.update({"progress": 0, "cancel": True})
check("cancelled scan returns None", app.scan_files(cfg) is None)
app.scan_state["cancel"] = False

# ── 6. config POST validation of file_types (call view directly) ──
print("[6] update_config file_types handling")
test_cfg_path = app.CONFIG_PATH
app.CONFIG_PATH = os.path.join(tmp, "cfg.json")
with app.app.test_request_context(json={"file_types": ["PDF", "txt"]}):
    app.update_config()
saved = json.load(open(app.CONFIG_PATH, encoding="utf-8"))
check("list saved normalized+sorted", saved["file_types"] == [".pdf", ".txt"])
with app.app.test_request_context(json={"file_types": "bogus"}):
    app.update_config()
saved = json.load(open(app.CONFIG_PATH, encoding="utf-8"))
check("bad string falls back to 'work'", saved["file_types"] == "work")
with app.app.test_request_context(json={"file_types": "all"}):
    app.update_config()
saved = json.load(open(app.CONFIG_PATH, encoding="utf-8"))
check("'all' preserved", saved["file_types"] == "all")
app.CONFIG_PATH = test_cfg_path

# ── 7. CSV export escaping ──
print("[7] CSV export escaping")
app.scan_state["result"] = {
    "groups": [{"group_id": "g_0", "files": [{
        "name": 'a,"b".pdf', "ext": ".pdf", "source": "T",
        "path": r"D:\x\a,""b"".pdf", "size": 5, "mtime_str": "2026-01-01 00:00",
    }]}],
}
with app.app.test_request_context("/api/export?format=csv"):
    resp = app.export_data()
csv_bytes = b"".join(resp.response)   # send_file uses direct passthrough
csv_text = csv_bytes.decode("utf-8-sig")
import csv as _csv, io as _io
rows = list(_csv.reader(_io.StringIO(csv_text)))
check("csv row parses to 8 cols", len(rows[1]) == 8)
check("csv filename round-trips", rows[1][1] == 'a,"b".pdf')
app.scan_state["result"] = None

# ── 8. scan result persistence ──
print("[8] scan result cache")
test_cache = os.path.join(tmp, "cache.json")
app.save_cache(test_cache, {"groups": [1], "total_files": 9})
loaded = app.load_cache(test_cache)
check("cache round-trip", loaded == {"groups": [1], "total_files": 9})
check("missing cache -> None", app.load_cache(os.path.join(tmp, "nope.json")) is None)

# ── 9. operate: move auto-creates dest dir; log written BEFORE action ──
print("[9] operate move/delete")
log_path_bak = app.LOG_PATH
app.LOG_PATH = os.path.join(tmp, "oplog.txt")
app.CONFIG_PATH = os.path.join(tmp, "cfg2.json")
src = mkfile("mv/a.txt", b"hello")
dest_root = os.path.join(tmp, "archive", "分类A")  # does not exist yet
cfg2 = {"folders": [{"path": tmp, "label": "T"}], "protected_folders": []}
json.dump(cfg2, open(app.CONFIG_PATH, "w", encoding="utf-8"))
with app.app.test_request_context(json={"action": "move", "files": [src], "dest_dir": dest_root}):
    resp = app.operate()
r = json.loads(resp.data)
check("move succeeded", r["success"] == 1)
check("dest dir auto-created", os.path.isfile(os.path.join(dest_root, "a.txt")))
log_lines = open(app.LOG_PATH, encoding="utf-8").read()
check("log written", "MOVE" in log_lines and "a.txt" in log_lines)
app.LOG_PATH = log_path_bak
app.CONFIG_PATH = test_cfg_path

# ── 10. quick_rule keep_label: 保底与统计 ──
print("[10] quick_rule keep_label safety")
def _f(fid, source, protected=False, mtime=1):
    return {"id": fid, "source": source, "protected": protected, "mtime": mtime}
groups_qr = [
    # 组1：微信 + 企业微信 -> 保留微信，勾选企业微信
    {"files": [_f("a1", "微信:xwechat_files"), _f("a2", "企业微信:wxwork")]},
    # 组2：全部来自微信 -> 无可勾选
    {"files": [_f("b1", "微信:xwechat_files"), _f("b2", "微信:xwechat_files")]},
    # 组3：组内没有微信 -> 整组跳过（旧逻辑会把整组全部勾选，危险）
    {"files": [_f("c1", "企业微信:wxwork"), _f("c2", "其他")]},
    # 组4：对方来源是受保护文件 -> 不勾选
    {"files": [_f("d1", "微信:xwechat_files"), _f("d2", "企业微信:wxwork", protected=True)]},
]
sel, stats = app.quick_rule_selections("keep_label", groups_qr, "微信:xwechat_files")
check("keep_label selects only non-kept-source, non-protected", sel == ["a2"])
check("groups_applied counts groups containing the source", stats["groups_applied"] == 3)
check("group without the source is skipped entirely", stats["groups_skipped"] == 1)
sel_amb, stats_amb = app.quick_rule_selections("keep_label", groups_qr, "微信")
check("exact match: '微信' does NOT match '企业微信' or '微信:xwechat_files'",
      sel_amb == [] and stats_amb["groups_skipped"] == 4)
sel2, stats2 = app.quick_rule_selections("keep_label", groups_qr, "不存在的来源")
check("unknown source selects nothing", sel2 == [] and stats2["groups_skipped"] == 4)
sel3, stats3 = app.quick_rule_selections("keep_label", groups_qr, "")
check("empty keep_label selects nothing", sel3 == [] and stats3["groups_skipped"] == 0)
sel4, _ = app.quick_rule_selections("keep_newest",
    [{"files": [_f("e1", "A", mtime=1), _f("e2", "B", mtime=2)]}])
check("keep_newest still works", sel4 == ["e1"])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
