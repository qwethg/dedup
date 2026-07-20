# -*- coding: utf-8 -*-
"""
文件整理、清理器 - 后端
Flask local HTTP server for file deduplication and organization tool.
"""
import os
import sys
import json
import hashlib
import shutil
import subprocess
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
SCAN_CACHE_PATH = os.path.join(BASE_DIR, 'scan_cache.json')
ORGANIZE_CACHE_PATH = os.path.join(BASE_DIR, 'organize_cache.json')

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
    "organize_keywords": {
        "会议纪要": ["会议纪要", "纪要", "会议记录", "会议", "meeting", "minutes"],
        "通知文件": ["通知", "公告", "通报", "批复"],
        "设计文件": ["设计图", "施工图", "图纸", "平面图", "布置图", "接线图", "系统图", "原理图", "方案"],
        "变更文件": ["变更", "变更设计", "变更通知"],
        "合同文件": ["合同", "协议", "招标", "投标", "中标"],
        "报告文件": ["报告", "总结", "汇报", "分析", "研究"],
        "规章制度": ["规程", "规范", "制度", "办法", "规定", "细则"],
    },
    "organize_exts": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".dwg", ".dxf", ".txt", ".csv", ".rtf", ".wps", ".vsd", ".vsdx", ".mpp"],
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

# ─── Scan state (dedup) ───
scan_state = {
    "scanning": False,
    "progress": 0,
    "total": 0,
    "current_path": "",
    "result": None,
    "error": None,
    "cancel": False,
}
scan_lock = threading.Lock()

# ─── Organize state ───
organize_state = {
    "scanning": False,
    "progress": 0,
    "total": 0,
    "current_path": "",
    "result": None,
    "error": None,
    "cancel": False,
}
organize_lock = threading.Lock()

# ─── Organize file types ───
ORGANIZE_DEFAULT_EXTS = [
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".dwg", ".dxf", ".txt", ".csv", ".rtf", ".wps", ".vsd", ".vsdx", ".mpp"
]


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


LOG_MAX_BYTES = 2 * 1024 * 1024    # rotate when log exceeds 2 MB
LOG_KEEP_BYTES = 1 * 1024 * 1024   # keep the newest 1 MB after rotation


def _rotate_log_if_needed():
    try:
        if not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) <= LOG_MAX_BYTES:
            return
        with open(LOG_PATH, 'rb') as f:
            f.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            tail = f.read().decode('utf-8', errors='replace')
        # Drop the first partial line
        nl = tail.find('\n')
        if nl >= 0:
            tail = tail[nl + 1:]
        with open(LOG_PATH, 'w', encoding='utf-8') as f:
            f.write(f"--- log rotated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(tail)
    except Exception:
        pass


def log_operation(action, filepath, detail=""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {action} | {filepath}"
    if detail:
        line += f" | {detail}"
    line += "\n"
    _rotate_log_if_needed()
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line)


def format_size(n):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def normalize_exts(exts):
    """Normalize a user-supplied extension list.
    Accepts 'pdf', '.pdf', '*.PDF' etc.; returns a set of lowercase '.ext'."""
    result = set()
    for e in exts:
        e = str(e).strip().lower().lstrip("*").strip()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        result.add(e)
    return result


def get_file_type_set(cfg):
    """Resolve the extension filter from config.

    file_types may be:
      - "all"           -> None (no filter)
      - a list of exts  -> custom user filter (empty list = no filter)
      - a preset name   -> legacy preset ("work", "all")
    """
    ft = cfg.get("file_types", "work")
    if isinstance(ft, list):
        exts = normalize_exts(ft)
        return exts if exts else None
    if ft == "all":
        return None
    return FILE_TYPE_PRESETS.get(ft, FILE_TYPE_PRESETS["work"])


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


def quick_hash(path, head_bytes=4096):
    """Cheap pre-filter hash: MD5 of the first chunk only.
    Files with different heads can never have the same full MD5."""
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read(head_bytes)).hexdigest()
    except Exception:
        return None


def group_by_hash(files, state=None):
    """Split same-size files into duplicate groups by content.
    Uses a head-only quick hash first; full MD5 only for head collisions."""
    # Stage 1: head hash
    by_head = defaultdict(list)
    for f in files:
        if state is not None and state.get("cancel"):
            return []
        qh = quick_hash(f["path"])
        if qh:
            by_head[qh].append(f)
    # Stage 2: full MD5 only where heads collide
    by_md5 = defaultdict(list)
    for head_group in by_head.values():
        if len(head_group) <= 1:
            continue
        for f in head_group:
            if state is not None and state.get("cancel"):
                return []
            md5 = md5_file(f["path"])
            if md5:
                f["md5"] = md5
                by_md5[md5].append(f)
    return [v for v in by_md5.values() if len(v) > 1]


_COPY_SUFFIX_RE = re.compile(
    r'(?:[\s_\-]*[（(]\d+[）)]|[\s_\-]*(?:副本|拷贝|copy))+\s*$', re.IGNORECASE)


def normalize_name(fname):
    """Strip copy/version suffixes so '报告 (1).docx' / '报告-副本.docx'
    group together with '报告.docx' in similar mode."""
    base, ext = os.path.splitext(fname)
    prev = None
    while prev != base:
        prev = base
        base = _COPY_SUFFIX_RE.sub('', base)
    return (base.strip() + ext).lower()


def _norm_path(p):
    """Normalize a path for comparison: absolute form + OS case rules."""
    return os.path.normcase(os.path.normpath(p))


def _is_under(path, root):
    """True if `path` equals `root` or lives strictly inside it."""
    path, root = _norm_path(path), _norm_path(root)
    return path == root or path.startswith(root + os.sep)


def get_folder_label(path, cfg):
    """Try to label a file path with its source folder tag."""
    for folder in cfg.get("folders", []):
        if _is_under(path, folder["path"]):
            return folder.get("label", folder["path"])
    # Auto-detect
    pl = path.lower()
    if "wxwork" in pl or "企业微信" in path:
        return "企业微信"
    if "wechat" in pl or "xwechat" in pl or "微信" in path:
        return "微信"
    return "其他"


def is_protected(path, cfg):
    for pf in cfg.get("protected_folders", []):
        if _is_under(path, pf):
            return True
    return False


def walk_files(base):
    """Yield (dirpath, DirEntry) recursively via os.scandir.
    Faster than os.walk + os.path.getsize because DirEntry caches stat info."""
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            yield current, entry
                    except OSError:
                        continue
        except OSError:
            continue


def scan_files(cfg):
    """Single-pass scan of all configured folders; returns grouped duplicates.
    Returns None when cancelled mid-scan."""
    allowed_exts = get_file_type_set(cfg)
    min_size = cfg.get("min_size_kb", 1) * 1024
    mode = cfg.get("scan_mode", "exact")

    all_files = []
    folder_stats = defaultdict(lambda: [0, 0])  # folder path -> [count, bytes]

    for folder in cfg.get("folders", []):
        fpath = folder["path"]
        label = folder.get("label", fpath)
        if not os.path.isdir(fpath):
            continue
        for root, entry in walk_files(fpath):
            if scan_state.get("cancel"):
                return None
            scan_state["current_path"] = entry.path
            scan_state["progress"] += 1
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            folder_stats[fpath][0] += 1
            folder_stats[fpath][1] += st.st_size
            ext_lower = os.path.splitext(entry.name)[1].lower()
            if allowed_exts is not None and ext_lower not in allowed_exts:
                continue
            if st.st_size < min_size:
                continue
            fpath_full = os.path.normpath(entry.path)
            protected = is_protected(fpath_full, cfg)
            file_label = label
            all_files.append({
                "id": f"f_{len(all_files)}",
                "name": entry.name,
                "path": fpath_full,
                "size": st.st_size,
                "ext": ext_lower,
                "mtime": st.st_mtime,
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "source": file_label,
                "protected": protected,
                "month": os.path.basename(root),
            })

    # Folder stats were collected in the same pass — no separate pre-count walk
    for fp, (cnt, size) in folder_stats.items():
        folder_stats_cache[fp] = {"count": cnt, "size": size, "ts": time.time()}

    # Group by mode
    if mode == "exact":
        # Name (case-insensitive) + size candidates, then VERIFY by content:
        # same name+size does not guarantee identical content, so candidate
        # groups are split by hash before anything can be selected for delete.
        candidates = defaultdict(list)
        for f in all_files:
            candidates[(f["name"].lower(), f["size"])].append(f)
        groups = []
        for files in candidates.values():
            if len(files) > 1:
                groups.extend(group_by_hash(files, scan_state))

    elif mode == "content":
        # Group by size first, then head-hash + full MD5 verification
        size_groups = defaultdict(list)
        for f in all_files:
            size_groups[f["size"]].append(f)
        groups = []
        for files in size_groups.values():
            if len(files) > 1:
                groups.extend(group_by_hash(files, scan_state))

    elif mode == "similar":
        # Normalized name: '报告 (1).docx' / '报告-副本.docx' group with '报告.docx'
        groups_dict = defaultdict(list)
        for f in all_files:
            groups_dict[normalize_name(f["name"])].append(f)
        groups = [v for v in groups_dict.values() if len(v) > 1]

    else:
        groups = []

    if scan_state.get("cancel"):
        return None

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
        # The first (oldest) file is the one the UI marks as 建议保留,
        # so savings must be computed against keeping THAT file.
        group_size = sum(f["size"] for f in grp)
        savings = group_size - grp[0]["size"]
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


def save_cache(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def load_cache(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def scan_thread(cfg):
    try:
        scan_state["scanning"] = True
        scan_state["progress"] = 0
        scan_state["total"] = 0
        scan_state["error"] = None
        scan_state["result"] = None
        scan_state["cancel"] = False
        result = scan_files(cfg)
        if result is not None:  # None = cancelled
            scan_state["result"] = result
            save_cache(SCAN_CACHE_PATH, result)
    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        scan_state["scanning"] = False


# ─── Organize scan ───

def classify_file(fname, keywords_cfg):
    """Classify a file by matching its name against keyword categories."""
    name_lower = fname.lower()
    for category, keywords in keywords_cfg.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                return category
    return "其他"


def organize_scan_thread(cfg):
    """Scan folders and classify files by keyword categories."""
    try:
        organize_state["scanning"] = True
        organize_state["progress"] = 0
        organize_state["total"] = 0
        organize_state["error"] = None
        organize_state["result"] = None
        organize_state["cancel"] = False

        allowed_exts = set(cfg.get("organize_exts", ORGANIZE_DEFAULT_EXTS))
        keywords_cfg = cfg.get("organize_keywords", {})

        # Single-pass scan (no pre-count walk)
        all_files = []
        for folder in cfg.get("folders", []):
            fpath = folder["path"]
            label = folder.get("label", fpath)
            if not os.path.isdir(fpath):
                continue
            for root, entry in walk_files(fpath):
                if organize_state.get("cancel"):
                    return
                organize_state["current_path"] = entry.path
                organize_state["progress"] += 1
                ext_lower = os.path.splitext(entry.name)[1].lower()
                if ext_lower not in allowed_exts:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    fpath_full = os.path.normpath(entry.path)
                    category = classify_file(entry.name, keywords_cfg)
                    protected = is_protected(fpath_full, cfg)
                    file_label = label
                    all_files.append({
                        "id": f"o_{len(all_files)}",
                        "name": entry.name,
                        "path": fpath_full,
                        "size": st.st_size,
                        "ext": ext_lower,
                        "mtime": st.st_mtime,
                        "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "source": file_label,
                        "protected": protected,
                        "category": category,
                        "month": os.path.basename(root),
                    })
                except Exception:
                    continue

        if organize_state.get("cancel"):
            return

        # Group by category
        categories_dict = defaultdict(list)
        for f in all_files:
            categories_dict[f["category"]].append(f)

        # Build result
        result_categories = []
        for cat, files in categories_dict.items():
            files.sort(key=lambda x: x["mtime"], reverse=True)
            total_size = sum(f["size"] for f in files)
            result_categories.append({
                "category": cat,
                "count": len(files),
                "total_size": total_size,
                "total_size_str": format_size(total_size),
                "files": files,
            })

        # Sort categories by count descending, "其他" always last
        result_categories.sort(key=lambda x: x["count"], reverse=True)
        result_categories = [c for c in result_categories if c["category"] != "其他"] + \
                           [c for c in result_categories if c["category"] == "其他"]

        organize_state["result"] = {
            "categories": result_categories,
            "total_files": len(all_files),
            "total_categories": len(result_categories),
            "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_cache(ORGANIZE_CACHE_PATH, organize_state["result"])
    except Exception as e:
        organize_state["error"] = str(e)
    finally:
        organize_state["scanning"] = False


# ─── Routes ───

@app.route('/')
def index():
    html_path = os.path.join(BASE_DIR, 'index.html')
    return send_file(html_path)


@app.route('/style.css')
def style_css():
    return send_file(os.path.join(BASE_DIR, 'style.css'), mimetype='text/css')


@app.route('/app.js')
def app_js():
    return send_file(os.path.join(BASE_DIR, 'app.js'), mimetype='application/javascript')


@app.route('/api/config', methods=['GET'])
def get_config():
    cfg = load_config()
    # Don't return api_key in full
    safe = dict(cfg)
    safe["api_key"] = "***" if cfg.get("api_key") else ""
    # Expose the built-in default extension list so the UI can offer
    # "restore defaults" without hardcoding it in the frontend.
    safe["file_type_defaults"] = sorted(FILE_TYPE_PRESETS["work"])
    # Let the UI warn when deletes would bypass the recycle bin
    safe["has_send2trash"] = HAS_SEND2TRASH
    return jsonify(safe)


@app.route('/api/config', methods=['POST'])
def update_config():
    cfg = load_config()
    data = request.json
    for k in ["api_key", "api_base", "api_model", "scan_mode", "min_size_kb", "file_types", "organize_keywords", "organize_exts"]:
        if k in data:
            if k == "api_key" and data[k] == "***":
                continue
            if k == "file_types":
                v = data[k]
                if isinstance(v, list):
                    # Custom user-entered extension list
                    cfg[k] = sorted(normalize_exts(v))
                elif v in ("all", "work"):
                    cfg[k] = v
                else:
                    cfg[k] = "work"
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


@app.route('/api/open', methods=['POST'])
def open_path():
    """Open a file with its default app, or reveal it in Explorer.

    Security: the path must live inside one of the configured scan folders —
    the same boundary enforced for delete/move operations.
    """
    data = request.json or {}
    path = data.get("path", "")
    dir_mode = bool(data.get("dir"))
    if not path:
        return jsonify({"error": "缺少路径"}), 400
    path = os.path.normpath(path)
    cfg = load_config()
    if not any(_is_under(path, f["path"]) for f in cfg.get("folders", [])):
        return jsonify({"error": "路径不在已配置的文件夹内"}), 403
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在（可能已被移动或删除）"}), 404
    try:
        if dir_mode:
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route('/api/scan/cancel', methods=['POST'])
def cancel_scan():
    scan_state["cancel"] = True
    return jsonify({"ok": True})


@app.route('/api/scan/result', methods=['GET'])
def scan_result():
    if scan_state["result"]:
        return jsonify(scan_state["result"])
    # Fall back to the last persisted scan so a restart doesn't lose results
    cached = load_cache(SCAN_CACHE_PATH)
    if cached:
        scan_state["result"] = cached
        return jsonify(cached)
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

    if action == "move":
        if not dest_dir:
            return jsonify({"error": "目标文件夹无效"}), 400
        try:
            os.makedirs(dest_dir, exist_ok=True)  # auto-create (e.g. per-category folders)
        except Exception as e:
            return jsonify({"error": f"无法创建目标文件夹: {e}"}), 400

    results = []
    for fpath in files:
        fpath = os.path.normpath(fpath)
        if not os.path.isfile(fpath):
            results.append({"path": fpath, "ok": False, "error": "文件不存在"})
            continue
        # Path safety check
        in_config = any(_is_under(fpath, folder["path"]) for folder in cfg["folders"])
        if not in_config:
            results.append({"path": fpath, "ok": False, "error": "文件不在配置文件夹内"})
            continue

        # Log the intent BEFORE the destructive action, so a crash mid-way
        # still leaves a record of what was attempted.
        log_operation(action.upper(), fpath, f"-> {dest_dir}" if action == "move" else "")
        try:
            if action == "delete":
                if HAS_SEND2TRASH:
                    send2trash(fpath)
                else:
                    os.remove(fpath)
                results.append({"path": fpath, "ok": True, "action": "deleted"})
            elif action == "move":
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


@app.route('/api/organize/scan', methods=['POST'])
def start_organize_scan():
    cfg = load_config()
    with organize_lock:
        if organize_state["scanning"]:
            return jsonify({"error": "正在扫描中..."}), 400
    t = threading.Thread(target=organize_scan_thread, args=(cfg,))
    t.daemon = True
    t.start()
    return jsonify({"ok": True})


@app.route('/api/organize/status', methods=['GET'])
def organize_status():
    return jsonify({
        "scanning": organize_state["scanning"],
        "progress": organize_state["progress"],
        "total": organize_state["total"],
        "current_path": organize_state["current_path"],
        "error": organize_state["error"],
        "has_result": organize_state["result"] is not None,
    })


@app.route('/api/organize/cancel', methods=['POST'])
def cancel_organize():
    organize_state["cancel"] = True
    return jsonify({"ok": True})


@app.route('/api/organize/result', methods=['GET'])
def organize_result():
    if organize_state["result"]:
        return jsonify(organize_state["result"])
    # Fall back to the last persisted result so a restart doesn't lose it
    cached = load_cache(ORGANIZE_CACHE_PATH)
    if cached:
        organize_state["result"] = cached
        return jsonify(cached)
    return jsonify({"categories": [], "total_files": 0})


@app.route('/api/organize/keywords', methods=['GET'])
def get_organize_keywords():
    cfg = load_config()
    return jsonify(cfg.get("organize_keywords", {}))


@app.route('/api/organize/keywords', methods=['POST'])
def update_organize_keywords():
    cfg = load_config()
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"error": "无效格式"}), 400
    cfg["organize_keywords"] = data
    save_config(cfg)
    return jsonify({"ok": True})


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
        import csv
        text_buf = io.StringIO()
        writer = csv.writer(text_buf)
        writer.writerow(["group_id", "filename", "ext", "source", "path", "size", "mtime", "protected"])
        for g in data["groups"]:
            for f in g["files"]:
                writer.writerow([
                    g["group_id"], f["name"], f["ext"], f["source"],
                    f["path"], f["size"], f["mtime_str"],
                    "yes" if f.get("protected") else "no",
                ])
        buf = io.BytesIO()
        buf.write(('\ufeff' + text_buf.getvalue()).encode('utf-8'))
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
    print(f"文件整理、清理器服务启动中... http://localhost:{port}")
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
