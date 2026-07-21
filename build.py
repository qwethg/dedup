# -*- coding: utf-8 -*-
"""
FileDedup 打包脚本
用法: python build.py
"""
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))

def main():
    # Install deps
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'flask', 'send2trash', 'pyinstaller'])

    # Build
    add_data = [
        f'index.html{os.pathsep}.',
        f'style.css{os.pathsep}.',
        f'app.js{os.pathsep}.',
    ]
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--name', 'FileDedup',
        '--icon', 'NONE',
        '--console',
        '--clean',
    ]
    for item in add_data:
        cmd += ['--add-data', item]
    cmd.append(os.path.join(BASE, 'app.py'))
    # 注意：config.json 不打包进 exe，程序会在 exe 旁边自动生成默认配置，
    # 这样用户的配置修改能跨重启保留
    subprocess.check_call(cmd, cwd=BASE)

    print(f"\n✅ 打包完成！exe 文件在: {os.path.join(BASE, 'dist', 'FileDedup.exe')}")

if __name__ == '__main__':
    main()
