#!/usr/bin/env python3
"""
微信/企业微信文件去重工具 — 扫描器
扫描指定目录，按文件名+大小判定重复，输出 JSON 供前端展示。
"""

import os
import json
import sys
import hashlib
from datetime import datetime
from collections import defaultdict
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import webbrowser
import urllib.parse

# ============ 配置 ============
SCAN_DIRS = [
    {
        "label": "企业微信",
        "path": r"D:\WXwork files\WXWorkLocal\1688849874813897_1970325076174789\Cache\File",
    },
    {
        "label": "微信",
        "path": r"D:\WeChat Files\o_yiy_o\FileStorage\File",
    },
    {
        "label": "微信(二号)",
        "path": r"D:\WeChat Files\wxid_c398f8usxf7l22\FileStorage\File",
    },
]

# 只扫描这些有意义的文件扩展名（工作文档类）
INCLUDE_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".dwg", ".dxf", ".zip", ".rar", ".7z", ".gz",
    ".txt", ".csv", ".rtf", ".wps", ".et", ".dps",
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
    ".mp4", ".mp3", ".wav", ".avi", ".mov",
    ".xml", ".html", ".htm", ".cad",
    ".vsd", ".vsdx", ".mpp", ".mppx",
    ".msg", ".eml",
}

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def human_size(num):
    """将字节转换为人类可读格式"""
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"


def scan_directory(dir_path, label):
    """扫描单个目录，返回文件列表"""
    files = []
    if not os.path.exists(dir_path):
        print(f"  [跳过] 目录不存在: {dir_path}")
        return files

    for root, dirs, filenames in os.walk(dir_path):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            try:
                fsize = os.path.getsize(fpath)
                _, ext = os.path.splitext(fname)

                # 只包含有意义的文件类型
                if ext.lower() not in INCLUDE_EXTS:
                    continue

                # 跳过0字节文件
                if fsize == 0:
                    continue

                # 获取相对路径（相对于扫描目录）
                rel_path = os.path.relpath(fpath, dir_path)

                # 修改时间
                mtime = os.path.getmtime(fpath)
                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

                files.append({
                    "name": fname,
                    "size": fsize,
                    "size_human": human_size(fsize),
                    "ext": ext.lower(),
                    "path": fpath,
                    "rel_path": rel_path,
                    "source": label,
                    "mtime": mtime_str,
                    "id": hashlib.md5(f"{fpath}".encode()).hexdigest()[:12],
                })
            except (OSError, PermissionError) as e:
                pass

    return files


def find_duplicates(files):
    """按文件名+大小分组，找出重复"""
    groups = defaultdict(list)
    for f in files:
        key = f"{f['name']}|{f['size']}"
        groups[key].append(f)

    # 只保留有重复的组（>1）
    dup_groups = []
    for key, group in groups.items():
        if len(group) > 1:
            # 按修改时间排序
            group.sort(key=lambda x: x["mtime"])
            # 计算可节省空间（保留1份，删其余）
            saveable = sum(f["size"] for f in group[1:])
            dup_groups.append({
                "key": key,
                "filename": group[0]["name"],
                "size": group[0]["size"],
                "size_human": group[0]["size_human"],
                "count": len(group),
                "saveable_size": saveable,
                "saveable_human": human_size(saveable),
                "files": group,
            })

    # 按可节省空间降序
    dup_groups.sort(key=lambda x: x["saveable_size"], reverse=True)
    return dup_groups


def generate_report():
    """主函数：扫描并生成报告"""
    print("=" * 60)
    print("微信/企业微信文件去重工具")
    print("=" * 60)

    all_files = []
    for d in SCAN_DIRS:
        print(f"\n扫描: {d['label']} — {d['path']}")
        files = scan_directory(d["path"], d["label"])
        print(f"  找到 {len(files)} 个文件")
        all_files.extend(files)

    print(f"\n总计: {len(all_files)} 个文件")

    print("\n分析重复文件...")
    dup_groups = find_duplicates(all_files)

    total_dup_files = sum(g["count"] for g in dup_groups)
    total_saveable = sum(g["saveable_size"] for g in dup_groups)

    print(f"  重复组: {len(dup_groups)}")
    print(f"  涉及文件: {total_dup_files}")
    print(f"  可节省空间: {human_size(total_saveable)}")

    # 生成 JSON
    report = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": len(all_files),
        "dup_groups": dup_groups,
        "total_dup_files": total_dup_files,
        "total_saveable": total_saveable,
        "total_saveable_human": human_size(total_saveable),
        "sources": [{"label": d["label"], "path": d["path"]} for d in SCAN_DIRS],
    }

    json_path = os.path.join(OUTPUT_DIR, "scan_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已生成: {json_path}")
    return report


class DedupHTTPRequestHandler(SimpleHTTPRequestHandler):
    """自定义 HTTP handler，支持删除文件"""

    def __init__(self, *args, **kwargs):
        self.directory = OUTPUT_DIR
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)

    def do_POST(self):
        """处理删除请求"""
        if self.path == "/api/delete":
            content_length = int(self.headers["Content-Length"])
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            file_path = data.get("path", "")

            # 安全检查：只允许删除扫描目录下的文件
            allowed_roots = [d["path"].lower() for d in SCAN_DIRS]
            norm_path = os.path.normpath(file_path).lower()
            in_allowed = any(norm_path.startswith(root.lower()) for root in allowed_roots)

            if not in_allowed:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "路径不在允许范围内"}).encode())
                return

            try:
                if os.path.exists(file_path):
                    # 移到回收站而不是直接删除
                    import send2trash
                    send2trash.send2trash(file_path)
                    result = {"success": True, "message": "已移至回收站"}
                else:
                    result = {"success": False, "error": "文件不存在"}
            except ImportError:
                # 没有 send2trash，使用直接删除
                try:
                    os.remove(file_path)
                    result = {"success": True, "message": "已删除"}
                except Exception as e:
                    result = {"success": False, "error": str(e)}
            except Exception as e:
                result = {"success": False, "error": str(e)}

            # 记录删除日志
            if result["success"]:
                log_path = os.path.join(OUTPUT_DIR, "delete_log.txt")
                with open(log_path, "a", encoding="utf-8") as log:
                    log.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] DELETED: {file_path}\n")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


if __name__ == "__main__":
    # 生成报告
    report = generate_report()

    # 启动本地服务器
    PORT = 18739
    print(f"\n启动本地服务: http://localhost:{PORT}")
    print(f"在浏览器中打开: http://localhost:{PORT}/index.html")
    print("按 Ctrl+C 退出")

    handler = DedupHTTPRequestHandler
    server = HTTPServer(("127.0.0.1", PORT), handler)

    # 自动打开浏览器
    webbrowser.open(f"http://localhost:{PORT}/index.html")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()
