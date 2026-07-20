#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys
from collections import defaultdict

SCAN_DIR = r'D:\WXwork files\WXWorkLocal\1688849874813897_1970325076174789\Cache\File'
INCLUDE_EXTS = {'.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.dwg','.dxf','.zip','.rar','.7z','.txt','.csv','.rtf','.wps','.jpg','.jpeg','.png','.msg','.eml','.vsd','.vsdx','.mpp'}

def main():
    name_groups = defaultdict(list)
    for root, dirs, filenames in os.walk(SCAN_DIR):
        for fname in filenames:
            _, ext = os.path.splitext(fname)
            if ext.lower() not in INCLUDE_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                fsize = os.path.getsize(fpath)
                if fsize == 0:
                    continue
                month = os.path.basename(root)
                name_groups[fname].append({'path': fpath, 'size': fsize, 'month': month})
            except:
                pass

    dups = {k: v for k, v in name_groups.items() if len(v) > 1}
    print('Same-name groups: %d' % len(dups))
    sys.stdout.flush()
    for name, files in sorted(dups.items()):
        sizes = set(f['size'] for f in files)
        same_size = len(sizes) == 1
        print('  %s x%d %s' % (name, len(files), '(SAME SIZE)' if same_size else '(DIFFERENT SIZES)'))
        for f in files:
            print('    %s | %d bytes | %s' % (f['month'], f['size'], f['path']))
    sys.stdout.flush()

if __name__ == '__main__':
    main()
