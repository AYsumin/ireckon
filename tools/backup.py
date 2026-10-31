#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日备份：快照 + 打包 + 校验，一条命令。

它备份的是「机制能备份的东西」，不备份任何第三方数据。
默认只做三件事：

    1. 用 VACUUM INTO 给库拍一致性快照（不是 Copy-Item）
    2. 把 config.py / 自己的记忆目录一起收进去
    3. 校验快照的 quick_check，不 ok 就大声报错

用法:
    python tools/backup.py                 # 存到 ./backups/YYYYMMDD-HHMM/
    python tools/backup.py --keep 7        # 只留最近 7 份

Windows 定时任务（powershell，每天 04:30）：
    schtasks /create /tn "sjmemory-backup" /sc daily /st 04:30 ^
        /tr "python <项目路径>/tools/backup.py --keep 7"
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C  # noqa: E402
from tools.snapshot import snapshot  # noqa: E402

EXTRA = ["config.py"]          # 还想备份什么，加在这里


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="backups")
    ap.add_argument("--keep", type=int, default=7)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M")
    root = os.path.join(args.outdir, stamp)
    os.makedirs(root, exist_ok=True)
    ok = True
    files = []

    # 1) 数据库快照
    if os.path.exists(C.DB_PATH):
        dst = os.path.join(root, os.path.basename(C.DB_PATH))
        try:
            result, size = snapshot(C.DB_PATH, dst)
        except Exception as e:
            print(f"库快照失败: {str(e)[:160]}")
            return 1
        if result != "ok":
            print(f"库快照可疑: quick_check={result}")
            ok = False
        else:
            files.append((os.path.basename(dst), size))
    else:
        print(f"（还没有库文件 {C.DB_PATH}，跳过）")

    # 2) 额外文件
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in EXTRA:
        src = os.path.join(base, rel)
        if os.path.exists(src):
            shutil.copy2(src, root)
            files.append((rel, round(os.path.getsize(src) / 1024, 1)))

    # 3) 报告
    print(f"备份目录: {root}")
    for name, size in files:
        unit = "MB" if name.endswith((".db", ".index")) else "KB"
        print(f"  + {name}  {size} {unit}")
    if not ok:
        print("！！有快照校验失败，别删旧备份")
        return 1

    # 4) 轮转
    olds = sorted([os.path.join(args.outdir, d) for d in os.listdir(args.outdir)],
                  key=os.path.getmtime, reverse=True)
    for p in olds[args.keep:]:
        shutil.rmtree(p, ignore_errors=True)
        print(f"  轮转删除 {os.path.basename(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
