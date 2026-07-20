#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, hashlib, sys
from datetime import datetime
from collections import defaultdict

SCAN_DIRS = [
    {'label': '企业微信', 'path': r'D:\WXwork files\WXWorkLocal\1688849874813897_1970325076174789\Cache\File'},
    {'label': '微信', 'path': r'D:\WeChat Files\o_yiy_o\FileStorage\File'},
    {'label': '微信(二号)', 'path': r'D:\WeChat Files\wxid_c398f8usxf7l22\FileStorage\File'},
]

INCLUDE_EXTS = {
    '.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx',
    '.dwg','.dxf','.zip','.rar','.7z','.gz',
    '.txt','.csv','.rtf','.wps','.et','.dps',
    '.jpg','.jpeg','.png','.bmp','.tif','.tiff',
    '.mp4','.mp3','.wav','.avi','.mov',
    '.xml','.html','.htm','.cad',
    '.vsd','.vsdx','.mpp','.mppx',
    '.msg','.eml',
}

def human_size(num):
    for unit in ['B','KB','MB','GB']:
        if num < 1024.0:
            return '%.1f %s' % (num, unit)
        num /= 1024.0
    return '%.1f TB' % num

def main():
    all_files = []
    for d in SCAN_DIRS:
        print('Scanning: %s - %s' % (d['label'], d['path']))
        sys.stdout.flush()
        if not os.path.exists(d['path']):
            print('  SKIP - not found')
            continue
        count = 0
        for root, dirs, filenames in os.walk(d['path']):
            for fname in filenames:
                fpath = os.path.join(root, fname)
                try:
                    fsize = os.path.getsize(fpath)
                    _, ext = os.path.splitext(fname)
                    if ext.lower() not in INCLUDE_EXTS:
                        continue
                    if fsize == 0:
                        continue
                    rel_path = os.path.relpath(fpath, d['path'])
                    mtime = os.path.getmtime(fpath)
                    mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                    all_files.append({
                        'name': fname,
                        'size': fsize,
                        'size_human': human_size(fsize),
                        'ext': ext.lower(),
                        'path': fpath,
                        'rel_path': rel_path,
                        'source': d['label'],
                        'mtime': mtime_str,
                        'id': hashlib.md5(fpath.encode()).hexdigest()[:12],
                    })
                    count += 1
                except:
                    pass
        print('  Found %d files' % count)
        sys.stdout.flush()

    print('Total: %d files' % len(all_files))
    sys.stdout.flush()

    # Find duplicates
    groups = defaultdict(list)
    for f in all_files:
        key = '%s|%d' % (f['name'], f['size'])
        groups[key].append(f)

    dup_groups = []
    for key, group in groups.items():
        if len(group) > 1:
            group.sort(key=lambda x: x['mtime'])
            saveable = sum(f['size'] for f in group[1:])
            dup_groups.append({
                'key': key,
                'filename': group[0]['name'],
                'size': group[0]['size'],
                'size_human': group[0]['size_human'],
                'count': len(group),
                'saveable_size': saveable,
                'saveable_human': human_size(saveable),
                'files': group,
            })

    dup_groups.sort(key=lambda x: x['saveable_size'], reverse=True)

    total_dup_files = sum(g['count'] for g in dup_groups)
    total_saveable = sum(g['saveable_size'] for g in dup_groups)

    print('Dup groups: %d' % len(dup_groups))
    print('Dup files: %d' % total_dup_files)
    print('Saveable: %s' % human_size(total_saveable))
    print()
    print('Top 20:')
    for i, g in enumerate(dup_groups[:20]):
        print('  %d. %s %s x%d -> %s' % (i+1, g['filename'], g['size_human'], g['count'], g['saveable_human']))
    sys.stdout.flush()

    report = {
        'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_files': len(all_files),
        'dup_groups': dup_groups,
        'total_dup_files': total_dup_files,
        'total_saveable': total_saveable,
        'total_saveable_human': human_size(total_saveable),
        'sources': [{'label': d['label'], 'path': d['path']} for d in SCAN_DIRS],
    }

    with open('scan_result.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print()
    print('Report saved to scan_result.json!')

if __name__ == '__main__':
    main()
