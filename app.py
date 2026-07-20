# -*- coding: utf-8 -*-
"""
FileDedup - 文件去重工具后端
Flask local HTTP server for file deduplication tool.
"""
import os
import sys
import json
import hashlib
import shutil
import re
import time
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from flask import Flask, request, jsonify, send_file, Response

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

app = Flask(__name__, static_folder=None)

# ─── Paths ───
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
LOG_PATH = os.path.join(BASE_DIR, 'operation_log.txt')

# ─── Default config ───
DEFAULT_CONFIG = {
    "folders": [],
    "protected_folders": [],
    "api_key": "",
    "api_base": "https://api.openai.com/v1",
    "api_model": "gpt-4o-mini",
    "scan_mode": "exact",
    "min_size_kb": 1,
    "file_types": "work",
}

# ─── File type presets ───
FILE_TYPE_PRESETS = {
    "work": {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".dwg", ".dxf", ".zip", ".rar", ".7z", ".txt", ".csv", ".rtf",
        ".wps", ".jpg", ".jpeg", ".png", ".msg", ".eml", ".vsd", ".vsdx", ".mpp"
    },
    "all": None  # None = no filter
}

# ─── Scan state ───
scan_state = {
    "scanning": False,
    "progress": 0,
    "total": 0,
    "current_path": "",
    "result": None,
    "error": None,
}
scan_lock = threading.Lock()


# ─── Folder stats cache (avoid re-walking on every GET /api/folders) ───
folder_stats_cache = {}  # path -> {count, size, ts}

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def log_operation(action, filepath, detail=""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {action} | {filepath}"
    if detail:
        line += f" | {detail}"
    line += "\n"
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line)


def format_size(n):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def get_file_type_set(cfg):
    preset = cfg.get("file_types", "work")
    return FILE_TYPE_PRESETS.get(preset, FILE_TYPE_PRESETS["work"])


def md5_file(path, chunk_size=65536):
    try:
        h = hashlib.md5()
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def get_folder_label(path, cfg):
    """Try to label a file path with its source folder tag."""
    for folder in cfg.get("folders", []):
        fp = folder["path"]
        if path.startswith(fp):
            return folder.get("label", fp)
    # Auto-detect
    pl = path.lower()
    if "wxwork" in pl or "企业微信" in path:
        return "企业微信"
    if "wechat" in pl or "xwechat" in pl or "微信" in path:
        return "微信"
    return "其他"


def is_protected(path, cfg):
    for pf in cfg.get("protected_folders", []):
        if path.startswith(pf):
            return True
    return False


def scan_files(cfg):
    """Scan all configured folders and return grouped duplicates."""
    allowed_exts = get_file_type_set(cfg)
    min_size = cfg.get("min_size_kb", 1) * 1024
    mode = cfg.get("scan_mode", "exact")

    all_files = []
    for folder in cfg.get("folders", []):
        fpath = folder["path"]
        label = folder.get("label", fpath)
        if not os.path.isdir(fpath):
            continue
        for root, dirs, filenames in os.walk(fpath):
            for fname in filenames:
                scan_state["current_path"] = os.path.join(root, fname)
                scan_state["progress"] += 1
                _, ext = os.path.splitext(fname)
                ext_lower = ext.lower()
                if allowed_exts is not None and ext_lower not in allowed_exts:
                    continue
                try:
                    fpath_full = os.path.normpath(os.path.join(root, fname))
                    fsize = os.path.getsize(fpath_full)
                    if fsize < min_size:
                        continue
                    mtime = os.path.getmtime(fpath_full)
                    protected = is_protected(fpath_full, cfg)
                    file_label = label if not protected else f"🔒 {label}"
                    all_files.append({
                        "id": f"f_{len(all_files)}",
                        "name": fname,
                        "path": fpath_full,
                        "size": fsize,
                        "ext": ext_lower,
                        "mtime": mtime,
                        "mtime_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                        "source": file_label,
                        "protected": protected,
                        "month": os.path.basename(root),
                    })
                except Exception:
                    continue

    # Group by mode
    if mode == "exact":
        groups_dict = defaultdict(list)
        for f in all_files:
            key = (f["name"], f["size"])
            groups_dict[key].append(f)
        groups = [v for v in groups_dict.values() if len(v) > 1]

    elif mode == "content":
        # First group by size
        size_groups = defaultdict(list)
        for f in all_files:
            size_groups[f["size"]].append(f)
        # Only compute MD5 for groups with same size
        groups_dict = defaultdict(list)
        for size, files in size_groups.items():
            if len(files) <= 1:
                continue
            for f in files:
                md5 = md5_file(f["path"])
                if md5:
                    f["md5"] = md5
                    groups_dict[md5].append(f)
        groups = [v for v in groups_dict.values() if len(v) > 1]

    elif mode == "similar":
        groups_dict = defaultdict(list)
        for f in all_files:
            key = f["name"].lower()
            groups_dict[key].append(f)
        groups = [v for v in groups_dict.values() if len(v) > 1]
        # similar mode: just pass groups, is_similar computed later

    else:
        groups = []

    # Build result
    result_groups = []
    total_dup_files = 0
    total_savings = 0
    for i, grp in enumerate(groups):
        # Sort by mtime
        grp.sort(key=lambda x: x["mtime"])
        # Determine if similar (different sizes)
        sizes = set(f["size"] for f in grp)
        is_similar = len(sizes) > 1
        # Keep the first (oldest) as suggested-keep
        group_size = sum(f["size"] for f in grp)
        single_size = grp[0]["size"] if not is_similar else max(f["size"] for f in grp)
        savings = group_size - min(f["size"] for f in grp)
        total_dup_files += len(grp)
        total_savings += savings
        result_groups.append({
            "group_id": f"g_{i}",
            "filename": grp[0]["name"],
            "ext": grp[0]["ext"],
            "count": len(grp),
            "is_similar": is_similar,
            "savings": savings,
            "savings_str": format_size(savings),
            "files": grp,
        })

    # Sort by savings descending
    result_groups.sort(key=lambda x: x["savings"], reverse=True)

    return {
        "groups": result_groups,
        "total_files": len(all_files),
        "total_groups": len(result_groups),
        "total_dup_files": total_dup_files,
        "total_savings": total_savings,
        "total_savings_str": format_size(total_savings),
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
    }


def scan_thread(cfg):
    try:
        scan_state["scanning"] = True
        scan_state["progress"] = 0
        scan_state["total"] = 0
        scan_state["error"] = None
        scan_state["result"] = None
        # Pre-count and update folder stats cache
        allowed_exts = get_file_type_set(cfg)
        min_size = cfg.get("min_size_kb", 1) * 1024
        for folder in cfg.get("folders", []):
            fpath = folder["path"]
            if not os.path.isdir(fpath):
                continue
            f_count, f_size = 0, 0
            for root, dirs, filenames in os.walk(fpath):
                for fname in filenames:
                    scan_state["total"] += 1
                    try:
                        fp = os.path.join(root, fname)
                        f_count += 1
                        f_size += os.path.getsize(fp)
                    except Exception:
                        pass
            folder_stats_cache[fpath] = {"count": f_count, "size": f_size, "ts": time.time()}
        # Scan
        result = scan_files(cfg)
        scan_state["result"] = result
    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        scan_state["scanning"] = False


# ─── Routes ───

@app.route('/')
def index():
    html_path = os.path.join(BASE_DIR, 'index.html')
    return send_file(html_path)


@app.route('/api/config', methods=['GET'])
def get_config():
    cfg = load_config()
    # Don't return api_key in full
    safe = dict(cfg)
    safe["api_key"] = "***" if cfg.get("api_key") else ""
    return jsonify(safe)


@app.route('/api/config', methods=['POST'])
def update_config():
    cfg = load_config()
    data = request.json
    for k in ["api_key", "api_base", "api_model", "scan_mode", "min_size_kb", "file_types"]:
        if k in data:
            if k == "api_key" and data[k] == "***":
                continue
            cfg[k] = data[k]
    save_config(cfg)
    return jsonify({"ok": True})


@app.route('/api/folders', methods=['GET'])
def get_folders():
    cfg = load_config()
    folders = cfg.get("folders", [])
    protected = cfg.get("protected_folders", [])
    result = []
    for f in folders:
        fpath = f["path"]
        is_prot = fpath in protected
        # Use cached stats if available, otherwise return 0 (don't block UI on os.walk)
        cached = folder_stats_cache.get(fpath)
        if cached:
            count, size = cached["count"], cached["size"]
        else:
            count, size = 0, 0
        result.append({
            "label": f.get("label", fpath),
            "path": fpath,
            "count": count,
            "size": size,
            "size_str": format_size(size) if size else "-",
            "protected": is_prot,
        })
    return jsonify(result)


@app.route('/api/folders', methods=['POST'])
def add_folder():
    cfg = load_config()
    data = request.json
    path = data.get("path", "").strip()
    label = data.get("label", "").strip()
    if not path or not os.path.isdir(path):
        return jsonify({"error": "路径无效"}), 400
    if not label:
        label = os.path.basename(path)
    # Check duplicate
    for f in cfg["folders"]:
        if f["path"] == path:
            return jsonify({"error": "路径已存在"}), 400
    cfg["folders"].append({"path": path, "label": label})
    if data.get("protected"):
        cfg["protected_folders"].append(path)
    save_config(cfg)
    # Invalidate cache for this path so next GET recomputes
    folder_stats_cache.pop(path, None)
    return jsonify({"ok": True})


@app.route('/api/folders/pick', methods=['POST'])
def pick_folder():
    """Open native folder picker dialog using tkinter."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(parent=root)
        root.destroy()
        if folder:
            return jsonify({"path": folder})
        return jsonify({"path": ""})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/folders', methods=['DELETE'])
def remove_folder():
    cfg = load_config()
    path = request.args.get("path", "")
    cfg["folders"] = [f for f in cfg["folders"] if f["path"] != path]
    if path in cfg["protected_folders"]:
        cfg["protected_folders"].remove(path)
    save_config(cfg)
    # Invalidate cache
    folder_stats_cache.pop(path, None)
    return jsonify({"ok": True})


@app.route('/api/folders/protect', methods=['POST'])
def toggle_protect():
    cfg = load_config()
    data = request.json
    path = data.get("path", "")
    if path in cfg["protected_folders"]:
        cfg["protected_folders"].remove(path)
    else:
        cfg["protected_folders"].append(path)
    save_config(cfg)
    return jsonify({"ok": True, "protected": path in cfg["protected_folders"]})


@app.route('/api/presets', methods=['GET'])
def get_presets():
    """Return common WeChat/WXWork paths if they exist."""
    presets = []
    candidates = [
        ("企业微信", r"D:\WXwork files"),
        ("微信", r"D:\WeChat Files"),
        ("微信(新)", r"D:\xwechat_files"),
    ]
    for label, base in candidates:
        if os.path.isdir(base):
            presets.append({"label": label, "path": base})
    return jsonify(presets)


@app.route('/api/scan', methods=['POST'])
def start_scan():
    cfg = load_config()
    with scan_lock:
        if scan_state["scanning"]:
            return jsonify({"error": "正在扫描中..."}), 400
    t = threading.Thread(target=scan_thread, args=(cfg,))
    t.daemon = True
    t.start()
    return jsonify({"ok": True})


@app.route('/api/scan/status', methods=['GET'])
def scan_status():
    return jsonify({
        "scanning": scan_state["scanning"],
        "progress": scan_state["progress"],
        "total": scan_state["total"],
        "current_path": scan_state["current_path"],
        "error": scan_state["error"],
        "has_result": scan_state["result"] is not None,
    })


@app.route('/api/scan/result', methods=['GET'])
def scan_result():
    if scan_state["result"]:
        return jsonify(scan_state["result"])
    return jsonify({"groups": [], "total_files": 0})


@app.route('/api/operate', methods=['POST'])
def operate():
    """Delete or move selected files."""
    cfg = load_config()
    data = request.json
    action = data.get("action")  # "delete" or "move"
    files = data.get("files", [])
    dest_dir = data.get("dest_dir", "")

    if action not in ("delete", "move"):
        return jsonify({"error": "无效操作"}), 400

    results = []
    for fpath in files:
        fpath = os.path.normpath(fpath)
        if not os.path.isfile(fpath):
            results.append({"path": fpath, "ok": False, "error": "文件不存在"})
            continue
        # Path safety check
        in_config = False
        for folder in cfg["folders"]:
            norm_folder = os.path.normpath(folder["path"])
            if fpath.startswith(norm_folder + os.sep) or fpath == norm_folder:
                in_config = True
                break
        if not in_config:
            results.append({"path": fpath, "ok": False, "error": "文件不在配置文件夹内"})
            continue

        try:
            if action == "delete":
                if HAS_SEND2TRASH:
                    send2trash(fpath)
                else:
                    os.remove(fpath)
                log_operation("DELETE", fpath)
                results.append({"path": fpath, "ok": True, "action": "deleted"})
            elif action == "move":
                if not dest_dir or not os.path.isdir(dest_dir):
                    results.append({"path": fpath, "ok": False, "error": "目标文件夹无效"})
                    continue
                fname = os.path.basename(fpath)
                dest_path = os.path.join(dest_dir, fname)
                # Auto-add suffix if exists
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(fname)
                    i = 1
                    while os.path.exists(dest_path):
                        dest_path = os.path.join(dest_dir, f"{base} ({i}){ext}")
                        i += 1
                shutil.move(fpath, dest_path)
                log_operation("MOVE", fpath, f"-> {dest_path}")
                results.append({"path": fpath, "ok": True, "action": "moved", "dest": dest_path})
        except Exception as e:
            log_operation("ERROR", fpath, str(e))
            results.append({"path": fpath, "ok": False, "error": str(e)})

    ok_count = sum(1 for r in results if r["ok"])
    return jsonify({
        "ok": True,
        "success": ok_count,
        "failed": len(results) - ok_count,
        "results": results,
    })


@app.route('/api/ai', methods=['POST'])
def ai_command():
    """Use OpenAI-compatible API to parse natural language into selection rules."""
    cfg = load_config()
    data = request.json
    user_text = data.get("text", "").strip()
    groups = data.get("groups", [])

    if not user_text:
        return jsonify({"error": "请输入指令"}), 400
    if not groups:
        return jsonify({"error": "没有重复组数据"}), 400

    api_key = cfg.get("api_key", "")
    api_base = cfg.get("api_base", "https://api.openai.com/v1")
    api_model = cfg.get("api_model", "gpt-4o-mini")

    if not api_key or api_key == "***":
        return jsonify({"error": "请先在设置中配置 API Key"}), 400

    # Build summary of groups for AI
    summary_parts = []
    file_index = []
    for g in groups:
        for f in g["files"]:
            file_index.append({
                "id": f["id"],
                "name": f["name"],
                "path": f["path"],
                "source": f["source"],
                "size": f["size"],
                "mtime": f["mtime_str"],
                "protected": f.get("protected", False),
                "group_id": g["group_id"],
            })
        summary_parts.append(
            f"组 {g['group_id']} ({g['filename']}, x{g['count']}):\n" +
            "\n".join(
                f"  - id={f['id']}, name={f['name']}, source={f['source']}, "
                f"size={format_size(f['size'])}, date={f['mtime_str']}, "
                f"{'PROTECTED' if f.get('protected') else 'not protected'}, "
                f"path={f['path']}"
                for f in g["files"]
            )
        )

    files_summary = "\n".join(summary_parts)

    system_prompt = """你是一个文件去重工具的AI助手。用户会用自然语言描述删除/保留规则。
你需要将自然语言转换为结构化的勾选指令。

规则：
1. 被标记为 PROTECTED 的文件不能被勾选删除
2. 每个重复组至少保留一个文件（不能全部勾选）
3. 返回JSON格式，包含：
   - understanding: 你对用户指令的理解（一句话）
   - selections: 需要勾选删除的文件id列表

只返回JSON，不要其他内容。"""

    user_prompt = f"""用户指令：{user_text}

重复组数据：
{files_summary}

请分析并返回JSON，格式为：
{{"understanding": "...", "selections": ["f_0", "f_3", ...]}}"""

    try:
        import urllib.request
        import urllib.error

        url = f"{api_base}/chat/completions"
        payload = {
            "model": api_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode('utf-8'))
            content = resp_data["choices"][0]["message"]["content"].strip()
            # Extract JSON from response
            # Try to find JSON in markdown code block
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            else:
                # Try to find raw JSON
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    content = content[json_start:json_end]
            result = json.loads(content)
            return jsonify(result)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        return jsonify({"error": f"API错误 ({e.code}): {err_body}"}), 500
    except Exception as e:
        return jsonify({"error": f"请求失败: {str(e)}"}), 500


@app.route('/api/quick_rule', methods=['POST'])
def quick_rule():
    """Apply a quick rule to select files for deletion."""
    data = request.json
    rule = data.get("rule")
    groups = data.get("groups", [])
    keep_label = data.get("keep_label", "")

    selections = []

    for g in groups:
        files = g.get("files", [])
        if len(files) <= 1:
            continue

        if rule == "keep_newest":
            # Keep the one with latest mtime
            newest = max(files, key=lambda x: x.get("mtime", 0))
            for f in files:
                if f["id"] != newest["id"] and not f.get("protected", False):
                    selections.append(f["id"])

        elif rule == "keep_oldest":
            oldest = min(files, key=lambda x: x.get("mtime", 0))
            for f in files:
                if f["id"] != oldest["id"] and not f.get("protected", False):
                    selections.append(f["id"])

        elif rule == "keep_label":
            # Keep files whose source matches keep_label
            for f in files:
                if keep_label and keep_label not in f.get("source", ""):
                    if not f.get("protected", False):
                        selections.append(f["id"])

    return jsonify({"selections": selections})


@app.route('/api/export', methods=['GET'])
def export_data():
    fmt = request.args.get("format", "json")
    if scan_state["result"]:
        data = scan_state["result"]
    else:
        return jsonify({"error": "没有扫描结果"}), 400

    if fmt == "json":
        import io
        buf = io.BytesIO()
        buf.write(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"dedup_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mimetype="application/json"
        )
    elif fmt == "csv":
        import io
        lines = ["group_id,filename,ext,source,path,size,mtime,protected"]
        for g in data["groups"]:
            for f in g["files"]:
                lines.append(
                    f"{g['group_id']},{f['name']},{f['ext']},{f['source']},"
                    f"{f['path']},{f['size']},{f['mtime_str']},"
                    f"{'yes' if f.get('protected') else 'no'}"
                )
        csv_content = "\n".join(lines)
        buf = io.BytesIO()
        buf.write(('\ufeff' + csv_content).encode('utf-8'))
        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"dedup_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mimetype="text/csv"
        )
    elif fmt == "log":
        if not os.path.exists(LOG_PATH):
            return jsonify({"error": "没有日志"}), 400
        return send_file(LOG_PATH, as_attachment=True, download_name="operation_log.txt")

    return jsonify({"error": "无效格式"}), 400


@app.route('/api/log', methods=['GET'])
def get_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, 'r', encoding='utf-8') as f:
            return Response(f.read(), mimetype='text/plain')
    return Response("(空)", mimetype='text/plain')


def main():
    port = 18739
    print(f"FileDedup 服务启动中... http://localhost:{port}")
    # Open browser after slight delay
    def open_browser():
        time.sleep(1)
        webbrowser.open(f"http://localhost:{port}")
    t = threading.Thread(target=open_browser)
    t.daemon = True
    t.start()
    app.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    main()
