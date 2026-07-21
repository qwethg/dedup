# -*- coding: utf-8 -*-
"""AI 规则 DSL 解释器（interpret_rules）与 selections 强制校验（validate_selections）
的单元测试，以及 /api/ai 路由的集成测试（monkeypatch 掉网络调用，不依赖真实 API）。"""
import os
import sys
import json
import tempfile
from datetime import datetime
import app

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def ts(s):
    """'2024-06-01' -> 时间戳"""
    return datetime.strptime(s, "%Y-%m-%d").timestamp()


def mkfile(fid, name, size, mtime, source="A", protected=False, path=None):
    return {
        "id": fid, "name": name, "path": path or f"D:\\data\\{name}",
        "size": size, "ext": os.path.splitext(name)[1].lower(),
        "mtime": mtime, "mtime_str": "2024-01-01 00:00",
        "source": source, "protected": protected,
    }


def mkgroup(gid, files):
    return {"group_id": gid, "filename": files[0]["name"], "ext": files[0]["ext"],
            "count": len(files), "files": files}


MB = 1024 * 1024

# ── 1. keep_source ──
print("[1] keep_source")
g = mkgroup("g_0", [
    mkfile("f_0", "报告.pdf", 1 * MB, ts("2024-01-01"), source="企业微信"),
    mkfile("f_1", "报告.pdf", 1 * MB, ts("2024-02-01"), source="微信"),
    mkfile("f_2", "报告.pdf", 1 * MB, ts("2024-03-01"), source="其他"),
])
sel = app.interpret_rules([{"type": "keep_source", "value": "企业微信"}], [g])
check("保留企业微信来源，勾选其余", sel == {"f_1", "f_2"})
sel = app.interpret_rules([{"type": "keep_source", "value": ""}], [g])
check("空 value 不勾选任何文件", sel == set())

# ── 2. keep_newest / keep_oldest ──
print("[2] keep_newest / keep_oldest")
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01")),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01")),
    mkfile("f_2", "a.pdf", 1 * MB, ts("2024-01-01")),
])
check("keep_newest 保留最新", app.interpret_rules([{"type": "keep_newest"}], [g]) == {"f_0", "f_2"})
check("keep_oldest 保留最旧", app.interpret_rules([{"type": "keep_oldest"}], [g]) == {"f_1", "f_2"})

# ── 3. keep_largest / keep_smallest / keep_shortest_path / keep_longest_path ──
print("[3] keep_largest / keep_smallest / keep_shortest_path / keep_longest_path")
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 5 * MB, ts("2024-01-01"), path=r"D:\a.pdf"),
    mkfile("f_1", "a.pdf", 9 * MB, ts("2024-01-01"), path=r"D:\very\deep\dir\a.pdf"),
    mkfile("f_2", "a.pdf", 2 * MB, ts("2024-01-01"), path=r"D:\mid\x\a.pdf"),
])
check("keep_largest", app.interpret_rules([{"type": "keep_largest"}], [g]) == {"f_0", "f_2"})
check("keep_smallest", app.interpret_rules([{"type": "keep_smallest"}], [g]) == {"f_0", "f_1"})
check("keep_shortest_path", app.interpret_rules([{"type": "keep_shortest_path"}], [g]) == {"f_1", "f_2"})
check("keep_longest_path", app.interpret_rules([{"type": "keep_longest_path"}], [g]) == {"f_0", "f_2"})

# ── 4. select_where ──
print("[4] select_where")
g = mkgroup("g_0", [
    mkfile("f_0", "季度报告.pdf", 10 * MB, ts("2023-06-01"), source="企业微信"),
    mkfile("f_1", "季度报告.pdf", 60 * MB, ts("2024-06-01"), source="微信"),
    mkfile("f_2", "季度报告.pdf", 80 * MB, ts("2024-06-01"), source="企业微信"),
])
check("size_mb gt 50", app.interpret_rules(
    [{"type": "select_where", "field": "size_mb", "op": "gt", "value": 50}], [g]) == {"f_1", "f_2"})
check("size_mb lt 50", app.interpret_rules(
    [{"type": "select_where", "field": "size_mb", "op": "lt", "value": 50}], [g]) == {"f_0"})
check("date before 2024-01-01", app.interpret_rules(
    [{"type": "select_where", "field": "date", "op": "before", "value": "2024-01-01"}], [g]) == {"f_0"})
check("date after 2024-01-01", app.interpret_rules(
    [{"type": "select_where", "field": "date", "op": "after", "value": "2024-01-01"}], [g]) == {"f_1", "f_2"})
check("source contains 企业微信", app.interpret_rules(
    [{"type": "select_where", "field": "source", "op": "contains", "value": "企业微信"}], [g]) == {"f_0", "f_2"})
check("name contains 报告", app.interpret_rules(
    [{"type": "select_where", "field": "name", "op": "contains", "value": "季度"}], [g]) == {"f_0", "f_1", "f_2"})
check("ext eq .pdf", app.interpret_rules(
    [{"type": "select_where", "field": "ext", "op": "eq", "value": ".pdf"}], [g]) == {"f_0", "f_1", "f_2"})
check("非法 value 不勾选", app.interpret_rules(
    [{"type": "select_where", "field": "size_mb", "op": "gt", "value": "abc"}], [g]) == set())
check("未知规则类型被忽略", app.interpret_rules([{"type": "nuke_everything"}], [g]) == set())

# ── 5. scope 过滤 ──
print("[5] scope 过滤")
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01")),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01")),
    mkfile("f_2", "a.docx", 1 * MB, ts("2024-01-01")),
])
sel = app.interpret_rules([{"type": "keep_newest", "scope": {"ext": ".pdf"}}], [g])
check("scope ext：只在 pdf 内保留最新，docx 不受影响", sel == {"f_0"})
g2 = mkgroup("g_0", [
    mkfile("f_0", "小报告.pdf", 1 * MB, ts("2024-01-01")),
    mkfile("f_1", "大报告.pdf", 100 * MB, ts("2024-01-01")),
    mkfile("f_2", "大数据.pdf", 100 * MB, ts("2024-01-01")),
])
sel = app.interpret_rules([{"type": "select_where", "field": "name", "op": "contains",
                            "value": "报告", "scope": {"size_gt_mb": 50}}], [g2])
check("scope size_gt_mb + name_contains 叠加", sel == {"f_1"})
sel = app.interpret_rules([{"type": "select_where", "field": "ext", "op": "eq", "value": ".pdf",
                            "scope": {"date_before": "2024-06-01"}}], [g2])
check("scope date_before", sel == {"f_0", "f_1", "f_2"})

# ── 6. validate_selections ──
print("[6] validate_selections 强制校验")
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01")),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01"), protected=True),
    mkfile("f_2", "a.pdf", 1 * MB, ts("2024-01-01")),
])
final, stats = app.validate_selections(["f_0", "f_1", "f_2", "f_999"], [g])
check("剔除无效 id", stats["dropped_invalid"] == 1 and "f_999" not in final)
check("剔除 protected 文件", stats["dropped_protected"] == 1 and "f_1" not in final)
check("有效勾选保留", set(final) == {"f_0", "f_2"})

# 整组被全选 -> 保底保留 mtime 最新的未保护文件
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01")),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01")),
    mkfile("f_2", "a.pdf", 1 * MB, ts("2024-01-01")),
])
final, stats = app.validate_selections(["f_0", "f_1", "f_2"], [g])
check("全选时保底保留最新", final == ["f_0", "f_2"] and stats["kept_per_group"] == 1)

# 全组都 protected -> 整组不选
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01"), protected=True),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01"), protected=True),
])
final, stats = app.validate_selections(["f_0", "f_1"], [g])
check("全组 protected 整组不选", final == [])

# ── 7. extract_json ──
print("[7] extract_json")
check("markdown 代码块", app.extract_json('前言```json\n{"a": 1, "b": {"c": 2}}\n```后记') == {"a": 1, "b": {"c": 2}})
check("首尾花括号", app.extract_json('说明文字 {"a": [1, 2]} 尾部') == {"a": [1, 2]})
try:
    app.extract_json("没有 JSON")
    check("无 JSON 抛错", False)
except ValueError:
    check("无 JSON 抛错", True)

# ── 8. 规则 + 校验联动（模拟路由主流程） ──
print("[8] 规则解释 + 校验联动")
g = mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01"), source="微信"),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01"), source="微信"),
])
sel = app.interpret_rules([{"type": "keep_source", "value": "企业微信"}], [g])
final, stats = app.validate_selections(sel, [g])
check("规则想全选时校验保底留 1 个", len(final) == 1 and stats["kept_per_group"] == 1)

# ── 9. /api/ai 路由集成（monkeypatch 网络层，不联网） ──
print("[9] /api/ai 路由集成")
tmp = tempfile.mkdtemp()
log_bak = app.LOG_PATH
load_bak = app.load_config
call_bak = app.call_ai_json
app.LOG_PATH = os.path.join(tmp, "ai_test_log.txt")

captured = {}


def fake_call_ai_json(cfg, system_prompt, user_prompt, max_tokens=None):
    captured["system_prompt"] = system_prompt
    captured["user_prompt"] = user_prompt
    return {"understanding": "测试理解", "rules": [{"type": "keep_newest"}], "selections": ["f_bad"]}


def fake_cfg(send_paths=False):
    return {"api_key": "sk-test", "api_base": "http://localhost:9/v1",
            "api_model": "test-model", "ai_send_paths": send_paths}


app.call_ai_json = fake_call_ai_json
client = app.app.test_client()

groups = [mkgroup("g_0", [
    mkfile("f_0", "a.pdf", 1 * MB, ts("2023-01-01")),
    mkfile("f_1", "a.pdf", 1 * MB, ts("2024-06-01")),
])]

# 无令牌 -> 401
app.load_config = lambda: fake_cfg(False)
r = client.post('/api/ai', json={"text": "保留最新", "groups": groups})
check("无令牌返回 401", r.status_code == 401)

# 有令牌 -> 正常；校验剔除无效 id；规则生效
r = client.post('/api/ai', json={"text": "保留最新", "groups": groups},
                headers={"X-App-Token": app.APP_TOKEN})
d = r.get_json()
check("路由返回 200", r.status_code == 200)
check("规则 keep_newest 生效", d["selections"] == ["f_0"])
check("无效 id 被剔除", d["validation"]["dropped_invalid"] == 1)
check("返回 understanding 与 rules", d["understanding"] == "测试理解" and len(d["rules"]) == 1)
check("groups_affected 正确", d["groups_affected"] == 1)

# 隐私开关：关 -> prompt 无路径；开 -> prompt 有路径
check("ai_send_paths=False 时 prompt 不含路径", r"D:\data" not in captured["user_prompt"]
      and "path=" not in captured["user_prompt"])
app.load_config = lambda: fake_cfg(True)
r = client.post('/api/ai', json={"text": "保留最新", "groups": groups},
                headers={"X-App-Token": app.APP_TOKEN})
check("ai_send_paths=True 时 prompt 含路径", r"D:\data\a.pdf" in captured["user_prompt"])

# 规模保护：超过 MAX_AI_GROUPS 组时截断并在 prompt 注明
many = [mkgroup(f"g_{i}", [
    mkfile(f"f_{i}_a", "a.pdf", 1 * MB, ts("2023-01-01")),
    mkfile(f"f_{i}_b", "a.pdf", 1 * MB, ts("2024-06-01")),
]) for i in range(app.MAX_AI_GROUPS + 10)]
app.load_config = lambda: fake_cfg(False)
r = client.post('/api/ai', json={"text": "保留最新", "groups": many},
                headers={"X-App-Token": app.APP_TOKEN})
d = r.get_json()
check("超出上限时 truncated=True", d["truncated"] is True and d["total_groups"] == app.MAX_AI_GROUPS + 10)
check("prompt 注明截断", f"只列出了前 {app.MAX_AI_GROUPS} 组" in captured["user_prompt"])

# AI 事件日志：有记录且不含 API Key / 完整 prompt
log_text = open(app.LOG_PATH, encoding="utf-8").read()
check("日志记录 AI_SELECT", "AI_SELECT" in log_text and "测试理解" in log_text)
check("日志不含 API Key", "sk-test" not in log_text)
check("日志不含完整 prompt", "重复组数据" not in log_text)

app.LOG_PATH = log_bak
app.load_config = load_bak
app.call_ai_json = call_bak

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
