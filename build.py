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
    subprocess.check_call([
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--name', 'FileDedup',
        '--add-data', f'index.html{os.pathsep}.',
        '--add-data', f'config.json{os.pathsep}.' if os.path.exists(os.path.join(BASE, 'config.json')) else f'index.html{os.pathsep}.',
        '--icon', 'NONE',
        '--console',
        '--clean',
        os.path.join(BASE, 'app.py'),
    ], cwd=BASE)

    print(f"\n✅ 打包完成！exe 文件在: {os.path.join(BASE, 'dist', 'FileDedup.exe')}")

if __name__ == '__main__':
    main()
