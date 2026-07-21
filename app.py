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
import stat
import subprocess
import re
import time
import threading
import webbrowser
import secrets
import urllib.request
import urllib.error
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

# ─── 本地服务令牌鉴权 ───
# 启动时生成随机令牌，只存内存（不落盘、不进日志）。
# index() 渲染页面时注入前端；所有 /api/* 接口校验 X-App-Token 头，
# 防止本机其他进程或恶意网页冒用 localhost 接口执行删文件等操作。
APP_TOKEN = secrets.token_hex(16)


@app.before_request
def _check_app_token():
    """/api/* 必须携带 X-App-Token 头；/、/style.css、/app.js 不校验（页面要先能加载）。
    /api/export 额外允许 ?token= 查询参数（window.open 无法带请求头）。"""
    if not request.path.startswith('/api/'):
        return None
    token = request.headers.get('X-App-Token', '')
    if request.path == '/api/export':
        token = token or request.args.get('token', '')
    if token != APP_TOKEN:
        return jsonify({"error": "未授权的请求（缺少或错误的本地令牌）"}), 401
    return None

# ─── Paths ───
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：静态资源在临时解压目录 (_MEIPASS)，
    # 可写文件（配置/日志/缓存）放在 exe 所在目录，保证重启后保留
    RESOURCE_DIR = sys._MEIPASS
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = RESOURCE_DIR
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
    "ai_send_paths": False,   # 隐私开关：为 True 才把完整路径发给 AI
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
    "disclaimer_accepted": False,   # 首次打开的风险声明是否已确认
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

# ─── AI 智能分类任务状态（后台线程 + 轮询进度） ───
ai_classify_state = {
    "running": False,
    "total_others": 0,
    "total_batches": 0,
    "done_batches": 0,
    "classified": 0,
    "failed_batches": 0,
    "error": None,
    "has_result": False,
    "cancel": False,
    "force": False,   # 强行停止：连当前在飞的 AI 请求也放弃
}
ai_classify_lock = threading.Lock()

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
    # 渲染时注入内存令牌（替换占位符，不写入磁盘文件）
    html_path = os.path.join(RESOURCE_DIR, 'index.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    return Response(html.replace('{{APP_TOKEN}}', APP_TOKEN), mimetype='text/html')


@app.route('/style.css')
def style_css():
    return send_file(os.path.join(RESOURCE_DIR, 'style.css'), mimetype='text/css')


@app.route('/app.js')
def app_js():
    return send_file(os.path.join(RESOURCE_DIR, 'app.js'), mimetype='application/javascript')


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
    for k in ["api_key", "api_base", "api_model", "scan_mode", "min_size_kb", "file_types", "organize_keywords", "organize_exts", "ai_send_paths", "disclaimer_accepted"]:
        if k in data:
            if k == "api_key" and (data[k] == "***" or not str(data[k]).strip()):
                continue  # 掩码或留空 = 不修改已保存的 Key
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


# ─── 微信/企业微信目录自动发现 ───
WECHAT_DIR_PATTERNS = [
    ("微信", "xwechat_files"),      # 微信 4.x 新版数据目录
    ("微信", "WeChat Files"),       # 微信旧版数据目录
    ("企业微信", "WXwork files"),
    ("企业微信", "WXWork Files"),
]


def detect_wechat_folders():
    """在各固定盘根目录和用户 Documents 下自动发现微信/企业微信文件目录。"""
    search_roots = []
    for drive in "CDEFG":
        root = f"{drive}:\\"
        if os.path.isdir(root):
            search_roots.append(root)
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if os.path.isdir(docs):
        search_roots.append(docs)
    found, seen = [], set()
    for root in search_roots:
        for label, name in WECHAT_DIR_PATTERNS:
            p = os.path.join(root, name)
            key = _norm_path(p)
            if os.path.isdir(p) and key not in seen:
                seen.add(key)
                found.append({"label": f"{label}·{name}", "path": p})
    return found


@app.route('/api/folders/autodetect', methods=['POST'])
def autodetect_folders():
    """一键自动扫描并添加微信/企业微信文件文件夹（已配置或互相包含的跳过）。"""
    cfg = load_config()
    existing = [_norm_path(f["path"]) for f in cfg["folders"]]
    added, skipped = [], []
    for item in detect_wechat_folders():
        p = item["path"]
        if any(_is_under(p, e) or _is_under(e, p) for e in existing):
            skipped.append(p)
            continue
        cfg["folders"].append({"path": p, "label": item["label"]})
        existing.append(_norm_path(p))
        added.append(p)
    if added:
        save_config(cfg)
    return jsonify({"ok": True, "added": added, "skipped": skipped})


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
                # 微信/企业微信接收的文件常带只读属性，直接删会 WinError 5 拒绝访问；
                # 用户已确认删除，先去掉只读属性再送回收站
                try:
                    os.chmod(fpath, stat.S_IWRITE)
                except OSError:
                    pass
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


# ═══════════════════════════════════════════════════════════════
# ─── AI 基础设施：规则 DSL 解释器 + 强制校验 + HTTP 调用 ───
# ═══════════════════════════════════════════════════════════════

MAX_AI_GROUPS = 60        # 发给模型的重复组上限；超出截断并在 prompt 注明（单次调用，简单可靠）
AI_MAX_TOKENS = 8192      # chat 回复的 max_tokens；推理型模型（如 kimi-k2.6）的思考过程也占输出额度，给足避免截断
AI_CLASSIFY_BATCH = 50    # 整理 AI 语义分类每批文件数


def _parse_date_value(s):
    """把 '2025-01-01' / '2025-01-01 10:00' 等字符串解析成时间戳，失败返回 None。"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).timestamp()
        except (ValueError, TypeError):
            continue
    return None


def _match_scope(f, scope):
    """scope 过滤：文件需满足全部条件才算命中该规则的作用范围。
    支持 ext / name_contains / size_gt_mb / size_lt_mb / date_before / date_after。"""
    if not isinstance(scope, dict):
        return True
    if scope.get("ext"):
        ext = str(scope["ext"]).lower().lstrip("*")
        if not ext.startswith("."):
            ext = "." + ext
        if f.get("ext", "").lower() != ext:
            return False
    if scope.get("name_contains"):
        if str(scope["name_contains"]).lower() not in f.get("name", "").lower():
            return False
    if scope.get("size_gt_mb") is not None:
        try:
            if f.get("size", 0) <= float(scope["size_gt_mb"]) * 1024 * 1024:
                return False
        except (TypeError, ValueError):
            return False
    if scope.get("size_lt_mb") is not None:
        try:
            if f.get("size", 0) >= float(scope["size_lt_mb"]) * 1024 * 1024:
                return False
        except (TypeError, ValueError):
            return False
    if scope.get("date_before"):
        ts = _parse_date_value(scope["date_before"])
        if ts is None or f.get("mtime", 0) >= ts:
            return False
    if scope.get("date_after"):
        ts = _parse_date_value(scope["date_after"])
        if ts is None or f.get("mtime", 0) <= ts:
            return False
    return True


def _match_where(f, field, op, value):
    """select_where 规则的单文件条件判断。
    field: size_mb|date|name|source|ext；op: gt|lt|contains|before|after|eq。"""
    field, op = str(field), str(op)
    if field == "size_mb":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        mb = f.get("size", 0) / (1024 * 1024)
        if op == "gt":
            return mb > v
        if op == "lt":
            return mb < v
        if op == "eq":
            return abs(mb - v) < 1e-6
        return False
    if field == "date":
        ts = _parse_date_value(value)
        if ts is None:
            return False
        m = f.get("mtime", 0)
        if op in ("before", "lt"):
            return m < ts
        if op in ("after", "gt"):
            return m > ts
        if op == "eq":
            return abs(m - ts) < 86400
        return False
    if field in ("name", "source", "ext"):
        fv = str(f.get(field, "")).lower()
        vv = str(value).lower()
        if op == "contains":
            return vv in fv
        if op == "eq":
            return fv == vv
    return False


def _rule_keep_winner(files, rtype):
    """keep_* 类规则的保留者（在规则作用范围内的文件中挑一个）。"""
    if rtype == "keep_newest":
        return max(files, key=lambda x: x.get("mtime", 0))
    if rtype == "keep_oldest":
        return min(files, key=lambda x: x.get("mtime", 0))
    if rtype == "keep_shortest_path":
        return min(files, key=lambda x: len(x.get("path", "")))
    if rtype == "keep_longest_path":
        return max(files, key=lambda x: len(x.get("path", "")))
    if rtype == "keep_largest":
        return max(files, key=lambda x: x.get("size", 0))
    if rtype == "keep_smallest":
        return min(files, key=lambda x: x.get("size", 0))
    return None


_KEEP_RULE_TYPES = ("keep_newest", "keep_oldest", "keep_shortest_path",
                    "keep_longest_path", "keep_largest", "keep_smallest")


def interpret_rules(rules, groups):
    """确定性规则解释器：把 AI 返回的规则 DSL 转成勾选 id 集合。
    语义与 /api/quick_rule 对齐；每条规则可带 scope 过滤作用范围。
    受保护剔除与每组保底不在此处理，统一由 validate_selections 兜底。"""
    selections = set()
    if not isinstance(rules, list):
        return selections
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rtype = rule.get("type")
        scope = rule.get("scope") if isinstance(rule.get("scope"), dict) else {}
        for g in groups:
            files = g.get("files", [])
            if len(files) <= 1:
                continue
            scoped = [f for f in files if _match_scope(f, scope)]
            if not scoped:
                continue
            if rtype == "keep_source":
                # 组内 source 包含 value 的保留，其余勾选（对齐 quick_rule 的 keep_label）
                val = str(rule.get("value", ""))
                if not val:
                    continue
                for f in scoped:
                    if val not in f.get("source", ""):
                        selections.add(f["id"])
            elif rtype in _KEEP_RULE_TYPES:
                keep = _rule_keep_winner(scoped, rtype)
                if keep is None:
                    continue
                for f in scoped:
                    if f["id"] != keep["id"]:
                        selections.add(f["id"])
            elif rtype == "select_where":
                # 直接勾选符合条件的文件
                for f in scoped:
                    if _match_where(f, rule.get("field"), rule.get("op"), rule.get("value")):
                        selections.add(f["id"])
            # 未知规则类型：忽略，不报错
    return selections


def validate_selections(selections, groups):
    """服务端强制校验（规则解释结果和裸 selections 统一走这里）：
    1. 剔除不存在的 id
    2. 剔除受保护（protected）文件
    3. 每个重复组保底留 1 个：一组被全选时保留组内 mtime 最新的未保护文件；
       全组都受保护时整组不选
    返回 (最终 id 列表, 修正统计)。"""
    stats = {"dropped_invalid": 0, "dropped_protected": 0, "kept_per_group": 0}
    id_map = {}
    for g in groups:
        for f in g.get("files", []):
            id_map[f["id"]] = f
    final = set()
    for fid in selections:
        f = id_map.get(fid)
        if f is None:
            stats["dropped_invalid"] += 1
        elif f.get("protected"):
            stats["dropped_protected"] += 1
        else:
            final.add(fid)
    for g in groups:
        files = g.get("files", [])
        if len(files) <= 1:
            continue
        if all(f["id"] in final for f in files):
            unprotected = [f for f in files if not f.get("protected")]
            if unprotected:
                keep = max(unprotected, key=lambda x: x.get("mtime", 0))
                final.discard(keep["id"])
                stats["kept_per_group"] += 1
            else:
                # 全组都是受保护文件：整组不选
                for f in files:
                    final.discard(f["id"])
    return sorted(final), stats


# ─── AI HTTP 调用（OpenAI 兼容，继续用 urllib，不引入 SDK） ───

def call_ai_chat(cfg, system_prompt, user_prompt, max_tokens=AI_MAX_TOKENS,
                 use_json_format=True, timeout=60):
    """调用 OpenAI 兼容的 chat/completions，返回 content 字符串。
    请求体带 response_format json_object 与 max_tokens；超时 60s。
    兼容性降级：HTTP 400 时根据错误信息自动去掉不被支持的参数后重试——
    部分本地服务（如旧版 Ollama）不支持 response_format；
    部分模型（如 kimi-k2.6）只允许 temperature=1 或不接受该参数。"""
    api_key = cfg.get("api_key", "")
    api_base = cfg.get("api_base", DEFAULT_CONFIG["api_base"]).rstrip("/")
    api_model = cfg.get("api_model", DEFAULT_CONFIG["api_model"])
    url = f"{api_base}/chat/completions"
    payload = {
        "model": api_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if use_json_format:
        payload["response_format"] = {"type": "json_object"}

    def _do(body):
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode('utf-8'),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode('utf-8'))
        return resp_data["choices"][0]["message"]["content"].strip()

    for _ in range(3):  # 最多两次降级重试
        try:
            return _do(payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace').lower()
            if e.code != 400:
                raise
            if "temperature" in body and "temperature" in payload:
                payload.pop("temperature")  # 模型限制 temperature（如只允许 1），直接不传
                continue
            if ("response_format" in body or "response format" in body) \
                    and "response_format" in payload:
                payload.pop("response_format")  # 服务不支持 JSON 模式
                continue
            if use_json_format and "response_format" in payload:
                payload.pop("response_format")  # 无法确定原因时优先降级 JSON 模式
                continue
            raise
    raise RuntimeError("AI 请求多次降级后仍失败")


def extract_json(content):
    """从模型回复中抠出 JSON 对象：优先 markdown 代码块兜底，其次首尾花括号。"""
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', content, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    start = content.find('{')
    end = content.rfind('}')
    if start >= 0 and end > start:
        return json.loads(content[start:end + 1])
    raise ValueError("回复中未找到 JSON")


def call_ai_json(cfg, system_prompt, user_prompt, max_tokens=AI_MAX_TOKENS):
    """调用 AI 并解析 JSON 回复；解析失败时提示模型上次输出无法解析，重试一次。"""
    content = call_ai_chat(cfg, system_prompt, user_prompt, max_tokens=max_tokens)
    try:
        return extract_json(content)
    except (ValueError, json.JSONDecodeError):
        retry_prompt = (user_prompt +
                        "\n\n注意：你上次的回复无法解析为 JSON，"
                        "请只返回一个合法 JSON 对象，不要任何其他内容。")
        content = call_ai_chat(cfg, system_prompt, retry_prompt, max_tokens=max_tokens)
        return extract_json(content)


# ─── /api/ai：自然语言勾选（规则 DSL + 强制校验） ───

AI_SELECT_SYSTEM_PROMPT = """你是一个文件去重工具的 AI 助手。用户用自然语言描述想删除/保留哪些重复文件。
请把用户意图转换为结构化规则，优先返回 rules（规则列表）；实在无法用规则表达时才直接返回 selections（文件 id 列表）。

返回 JSON：
{"understanding": "对用户意图的一句话说明", "rules": [...], "selections": [...]}

支持的规则类型（rules 数组中每个元素）：
1. {"type":"keep_source","value":"企业微信"} — 每组中保留 source 包含 value 的文件，其余勾选删除
2. {"type":"keep_newest"} / {"type":"keep_oldest"} — 每组保留修改时间最新/最旧的，其余勾选
3. {"type":"keep_shortest_path"} / {"type":"keep_longest_path"} — 每组保留路径最短/最长的（仅当数据中提供了 path 字段时可用）
4. {"type":"keep_largest"} / {"type":"keep_smallest"} — 每组保留体积最大/最小的
5. {"type":"select_where","field":"size_mb|date|name|source|ext","op":"gt|lt|contains|before|after|eq","value":...} — 直接勾选符合条件的文件
每条规则可带可选 "scope" 过滤，如 {"ext":".pdf","size_gt_mb":50,"date_before":"2025-01-01","name_contains":"报告"}，表示该规则只作用于符合条件的文件。

约束：
- 标记 PROTECTED 的文件绝不能勾选（服务端会强制剔除）
- 每个重复组至少保留 1 个文件（服务端会强制保底）
- 只返回 JSON，不要输出任何其他内容"""


@app.route('/api/ai', methods=['POST'])
def ai_command():
    """AI 自然语言勾选：模型返回规则 DSL（或裸 id 列表），
    服务端确定性解释 + 强制校验后才返回给前端。"""
    cfg = load_config()
    data = request.json or {}
    user_text = data.get("text", "").strip()
    groups = data.get("groups", [])

    if not user_text:
        return jsonify({"error": "请输入指令"}), 400
    if not groups:
        return jsonify({"error": "没有重复组数据"}), 400

    api_key = cfg.get("api_key", "")
    if not api_key or api_key == "***":
        return jsonify({"error": "请先在设置中配置 API Key"}), 400

    # 隐私开关：默认不向 AI 发送完整路径（只发 name/source/size/date/protected）
    send_paths = bool(cfg.get("ai_send_paths", False))

    # 规模保护：组数超过 MAX_AI_GROUPS 时只发前 N 组，并在 prompt 注明总量与截断
    truncated = len(groups) > MAX_AI_GROUPS
    send_groups = groups[:MAX_AI_GROUPS]

    summary_parts = []
    for g in send_groups:
        lines = []
        for f in g["files"]:
            # prompt 中每个文件只保留必要字段
            parts = [
                f"id={f['id']}",
                f"name={f['name']}",
                f"source={f.get('source', '')}",
                f"size={format_size(f.get('size', 0))}",
                f"date={f.get('mtime_str', '')}",
                "PROTECTED" if f.get("protected") else "normal",
            ]
            if send_paths:
                parts.append(f"path={f.get('path', '')}")
            lines.append("  - " + ", ".join(parts))
        summary_parts.append(
            f"组 {g['group_id']} ({g['filename']}, x{g['count']}):\n" + "\n".join(lines))
    files_summary = "\n".join(summary_parts)

    trunc_note = ""
    if truncated:
        trunc_note = (f"\n\n注意：共有 {len(groups)} 个重复组，为控制规模只列出了前 "
                      f"{MAX_AI_GROUPS} 组，请只针对列出的文件给出规则。")

    user_prompt = f"""用户指令：{user_text}

重复组数据：
{files_summary}{trunc_note}

请分析并返回 JSON：
{{"understanding": "...", "rules": [...], "selections": [...]}}"""

    try:
        result = call_ai_json(cfg, AI_SELECT_SYSTEM_PROMPT, user_prompt,
                              max_tokens=AI_MAX_TOKENS)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        return jsonify({"error": f"API错误 ({e.code}): {err_body[:500]}"}), 500
    except Exception as e:
        return jsonify({"error": f"请求失败: {str(e)}"}), 500

    understanding = str(result.get("understanding", ""))
    rules = result.get("rules") if isinstance(result.get("rules"), list) else []
    raw_sel = result.get("selections") if isinstance(result.get("selections"), list) else []

    # 裸 id 列表与规则解释结果合并，统一走服务端强校验
    selections = set(str(x) for x in raw_sel)
    if rules:
        selections |= interpret_rules(rules, send_groups)
    final_sel, vstats = validate_selections(selections, send_groups)

    sel_set = set(final_sel)
    groups_affected = sum(
        1 for g in send_groups
        if any(f["id"] in sel_set for f in g.get("files", [])))

    # AI 事件日志：记录指令、理解、勾选数、校验修正；不记录 API Key、不记录完整 prompt
    log_operation("AI_SELECT", f"指令: {user_text}",
                  f"理解: {understanding} | 勾选 {len(final_sel)} 个 / 影响 {groups_affected} 组 | "
                  f"校验修正: {vstats}")

    return jsonify({
        "understanding": understanding,
        "rules": rules,
        "selections": final_sel,
        "validation": vstats,
        "groups_affected": groups_affected,
        "truncated": truncated,
        "total_groups": len(groups),
    })


@app.route('/api/ai/test', methods=['POST'])
def ai_test():
    """连通性测试：用当前配置（或表单里尚未保存的值）发一个极小请求验证 key/base/model。
    测试成功后自动把本次验证通过的 key/base/model 保存到 config.json。"""
    cfg = load_config()
    data = request.json or {}
    # 允许用设置面板中未保存的值测试；api_key 为空或 "***" 时用已保存的
    if data.get("api_base"):
        cfg["api_base"] = data["api_base"]
    if data.get("api_model"):
        cfg["api_model"] = data["api_model"]
    if data.get("api_key") and data["api_key"] != "***":
        cfg["api_key"] = data["api_key"]
    if not cfg.get("api_key"):
        return jsonify({"ok": False, "error": "未配置 API Key"})
    try:
        reply = call_ai_chat(cfg, "你是连通性测试助手。", "回复 ok 即可。",
                             max_tokens=64, use_json_format=False, timeout=30)
        # 测试通过即落盘保存这组验证过的配置
        save_config(cfg)
        return jsonify({"ok": True, "model": cfg.get("api_model"),
                        "reply": reply[:80], "saved": True})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:300]
        return jsonify({"ok": False, "error": f"HTTP {e.code}: {body}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ─── /api/ai/filter：自然语言筛选（只影响前端展示，不改扫描数据） ───

AI_FILTER_SYSTEM_PROMPT = """你是筛选条件解析器。把用户的自然语言筛选条件转成 JSON 过滤对象。
返回格式：{"understanding": "一句话说明", "filter": {...}}
filter 的全部字段都可选，用户没提到的条件不要出现：
- "ext": 小写带点扩展名，如 ".pdf"
- "size_gt_mb": 数字，文件大于该 MB
- "size_lt_mb": 数字，文件小于该 MB
- "date_after": "YYYY-MM-DD"，修改日期晚于该日
- "date_before": "YYYY-MM-DD"，修改日期早于该日
- "name_contains": 文件名包含的文字
只返回 JSON，不要其他内容。"""

AI_FILTER_KEYS = ("ext", "size_gt_mb", "size_lt_mb", "date_after", "date_before", "name_contains")


@app.route('/api/ai/filter', methods=['POST'])
def ai_filter():
    """把"只看2024年大于50MB的PDF"这类指令解析成结构化过滤对象。"""
    cfg = load_config()
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "请输入筛选条件"}), 400
    if not cfg.get("api_key") or cfg.get("api_key") == "***":
        return jsonify({"error": "请先在设置中配置 API Key"}), 400
    try:
        result = call_ai_json(cfg, AI_FILTER_SYSTEM_PROMPT,
                              f"筛选条件：{text}", max_tokens=512)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        return jsonify({"error": f"API错误 ({e.code}): {err_body[:500]}"}), 500
    except Exception as e:
        return jsonify({"error": f"请求失败: {str(e)}"}), 500
    raw = result.get("filter") if isinstance(result.get("filter"), dict) else result
    filt = {}
    for k in AI_FILTER_KEYS:
        v = raw.get(k)
        if v is None or v == "":
            continue
        if k in ("size_gt_mb", "size_lt_mb"):
            try:
                filt[k] = float(v)
            except (TypeError, ValueError):
                continue
        elif k == "ext":
            ext = str(v).lower().lstrip("*")
            if not ext.startswith("."):
                ext = "." + ext
            filt[k] = ext
        else:
            filt[k] = str(v)
    log_operation("AI_FILTER", f"指令: {text}", f"解析: {filt}")
    return jsonify({"filter": filt, "understanding": str(result.get("understanding", ""))})


# ─── /api/organize/ai_classify：整理模块 AI 语义分类 ───

AI_CLASSIFY_SYSTEM_PROMPT = """你是文件分类助手。给定一批"其他"类文件（id 和文件名）和已有分类列表，
请根据文件名把每个文件归入最合适的分类：
- 优先使用已有分类名（原样返回，不要改写）
- 都不合适时可以给出新的简洁中文分类名（2-6 个字）
- 实在无法判断归入"其他"
返回 JSON：{"mapping": {"<id>": "<分类名>", ...}}，覆盖所有给出的 id。只返回 JSON。"""


def _apply_ai_classify(result, mapping):
    """把 AI 分类结果应用到 organize 结果上：命中文件从"其他"移到目标分类
    （新类别名自动建桶），重新统计排序，"其他"垫底。"""
    others = next((c for c in result["categories"] if c["category"] == "其他"), None)
    if not others or not mapping:
        return
    cat_map = {c["category"]: c for c in result["categories"]}
    remaining = []
    for f in others["files"]:
        cat = mapping.get(f["id"])
        if not cat:
            remaining.append(f)
            continue
        f["category"] = cat
        bucket = cat_map.get(cat)
        if bucket is None:
            bucket = {"category": cat, "count": 0, "total_size": 0,
                      "total_size_str": "0 B", "files": []}
            cat_map[cat] = bucket
            result["categories"].append(bucket)
        bucket["files"].append(f)
    others["files"] = remaining
    if not remaining:
        result["categories"].remove(others)
    for c in result["categories"]:
        c["files"].sort(key=lambda x: x["mtime"], reverse=True)
        c["count"] = len(c["files"])
        c["total_size"] = sum(f["size"] for f in c["files"])
        c["total_size_str"] = format_size(c["total_size"])
    result["categories"].sort(key=lambda x: x["count"], reverse=True)
    result["categories"] = [c for c in result["categories"] if c["category"] != "其他"] + \
                           [c for c in result["categories"] if c["category"] == "其他"]
    result["total_categories"] = len(result["categories"])


def _call_ai_abortable(cfg, system_prompt, user_prompt, st):
    """在子线程中执行 AI 调用，主线程每 0.5s 检查一次强行停止标志。
    返回 ("ok", 结果) / ("error", 异常) / ("aborted", None)。
    命中强行停止时放弃本次请求：子线程为 daemon，迟到响应直接丢弃，
    主线程立即返回，不再等待网络。"""

    def work(box):
        try:
            box["r"] = call_ai_json(cfg, system_prompt, user_prompt,
                                    max_tokens=AI_MAX_TOKENS)
        except Exception as e:
            box["e"] = e

    box = {}
    t = threading.Thread(target=work, args=(box,), daemon=True)
    t.start()
    while t.is_alive():
        if st.get("force"):
            return ("aborted", None)
        t.join(0.5)
    if "e" in box:
        return ("error", box["e"])
    return ("ok", box.get("r"))


def ai_classify_thread(cfg, result):
    """后台线程：分批调 AI 归类"其他"类文件，实时更新 ai_classify_state 供轮询。"""
    st = ai_classify_state
    mapping = {}
    try:
        others = next(c for c in result["categories"] if c["category"] == "其他")
        existing_cats = [c["category"] for c in result["categories"] if c["category"] != "其他"]
        files = others["files"]
        # 按文件名分批（每批 AI_CLASSIFY_BATCH 个），避免单次 prompt 过长
        for i in range(0, len(files), AI_CLASSIFY_BATCH):
            if st["cancel"] or st["force"]:
                break
            batch = files[i:i + AI_CLASSIFY_BATCH]
            listing = "\n".join(f"{f['id']} = {f['name']}" for f in batch)
            user_prompt = f"已有分类：{'、'.join(existing_cats)}\n\n待分类文件：\n{listing}"
            status, payload = _call_ai_abortable(cfg, AI_CLASSIFY_SYSTEM_PROMPT,
                                                 user_prompt, st)
            if status == "aborted":
                break
            if status == "error":
                st["failed_batches"] += 1
                st["done_batches"] += 1
                continue
            parsed = payload
            raw_map = parsed.get("mapping") if isinstance(parsed.get("mapping"), dict) else parsed
            batch_ids = {f["id"] for f in batch}
            for fid, cat in raw_map.items():
                if fid in batch_ids and isinstance(cat, str):
                    cat = cat.strip()
                    if cat and cat != "其他":
                        mapping[fid] = cat
            st["classified"] = len(mapping)
            st["done_batches"] += 1
        if mapping:
            _apply_ai_classify(result, mapping)
            save_cache(ORGANIZE_CACHE_PATH, result)
            organize_state["result"] = result
        st["has_result"] = True
        stopped = "强行停止" if st["force"] else ("用户取消" if st["cancel"] else "")
        log_operation("AI_CLASSIFY", f"其他类 {st['total_others']} 个文件",
                      f"AI 分类 {len(mapping)} 个" +
                      (f" | 失败批次: {st['failed_batches']}" if st["failed_batches"] else "") +
                      (f" | {stopped}" if stopped else ""))
    except Exception as e:
        st["error"] = str(e)
    finally:
        st["running"] = False


@app.route('/api/organize/ai_classify', methods=['POST'])
def organize_ai_classify():
    """启动 AI 语义分类后台任务（立即返回），前端轮询 status 获取进度。
    尊重 ai_send_paths：无论开关如何都只发文件名，不发路径。"""
    cfg = load_config()
    if not cfg.get("api_key") or cfg.get("api_key") == "***":
        return jsonify({"error": "请先在设置中配置 API Key"}), 400
    if ai_classify_state["running"]:
        return jsonify({"error": "AI 分类正在进行中"}), 400

    result = organize_state["result"] or load_cache(ORGANIZE_CACHE_PATH)
    if not result:
        return jsonify({"error": "没有整理扫描结果，请先扫描"}), 400
    organize_state["result"] = result
    others = next((c for c in result["categories"] if c["category"] == "其他"), None)
    if not others or not others.get("files"):
        return jsonify({"error": "没有「其他」类文件需要分类"}), 400

    total = len(others["files"])
    with ai_classify_lock:
        ai_classify_state.update({
            "running": True, "total_others": total,
            "total_batches": (total + AI_CLASSIFY_BATCH - 1) // AI_CLASSIFY_BATCH,
            "done_batches": 0, "classified": 0, "failed_batches": 0,
            "error": None, "has_result": False, "cancel": False, "force": False,
        })
    t = threading.Thread(target=ai_classify_thread, args=(cfg, result), daemon=True)
    t.start()
    return jsonify({"ok": True, "started": True, "total_others": total,
                    "total_batches": ai_classify_state["total_batches"]})


@app.route('/api/organize/ai_classify/status', methods=['GET'])
def organize_ai_classify_status():
    return jsonify(ai_classify_state)


@app.route('/api/organize/ai_classify/cancel', methods=['POST'])
def organize_ai_classify_cancel():
    """温和取消：当前批次跑完后停止。"""
    ai_classify_state["cancel"] = True
    return jsonify({"ok": True})


@app.route('/api/organize/ai_classify/force_stop', methods=['POST'])
def organize_ai_classify_force_stop():
    """强行停止：放弃当前在飞的 AI 请求，立即收尾（已完成的批次结果保留）。"""
    ai_classify_state["cancel"] = True
    ai_classify_state["force"] = True
    return jsonify({"ok": True})


def quick_rule_selections(rule, groups, keep_label=""):
    """Apply a quick rule; returns (selections, stats).

    keep_label 保底：组内确实存在 keep_label 来源的文件时才勾选其余文件；
    组内没有该来源的组整组跳过——避免把某组的所有副本全部勾选删除
    （与 AI 路径 validate_selections 的"每组保底留 1 个"语义对齐）。"""
    selections = []
    stats = {"groups_applied": 0, "groups_skipped": 0}

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
            stats["groups_applied"] += 1

        elif rule == "keep_oldest":
            oldest = min(files, key=lambda x: x.get("mtime", 0))
            for f in files:
                if f["id"] != oldest["id"] and not f.get("protected", False):
                    selections.append(f["id"])
            stats["groups_applied"] += 1

        elif rule == "keep_label":
            # Keep files whose source matches keep_label (exact match:
            # 前端下拉框传的是完整来源名，精确匹配可避免 "微信" 误命中 "企业微信")
            if not keep_label:
                continue
            if not any(f.get("source", "") == keep_label for f in files):
                # 组内没有该来源 -> 整组跳过，绝不全选
                stats["groups_skipped"] += 1
                continue
            stats["groups_applied"] += 1
            for f in files:
                if f.get("source", "") != keep_label:
                    if not f.get("protected", False):
                        selections.append(f["id"])

    return selections, stats


@app.route('/api/quick_rule', methods=['POST'])
def quick_rule():
    """Apply a quick rule to select files for deletion."""
    data = request.json
    selections, stats = quick_rule_selections(
        data.get("rule"), data.get("groups", []), data.get("keep_label", ""))
    return jsonify({"selections": selections, **stats})


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


@app.route('/api/log', methods=['GET', 'DELETE'])
def get_log():
    if request.method == 'DELETE':
        try:
            # 截断而非删除文件，避免句柄/权限问题；清空后留一条记录可追溯
            with open(LOG_PATH, 'w', encoding='utf-8'):
                pass
            log_operation("LOG_CLEAR", "操作日志已清空")
        except OSError as e:
            return jsonify({"error": f"清空失败: {e}"}), 500
        return jsonify({"ok": True})
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
