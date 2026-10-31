#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一致性快照：用 VACUUM INTO 拍一份完整自洽的库副本。

为什么不能用 Copy-Item 直接拷 .db：
    SQLite 在 WAL 模式下，最新修改可能还在 -wal 里。
    直接拷 .db 得到的是一份"缺了最近修改"的残件——打开可能直接报坏。
    （我们自己的机器上就因此丢过一份备份：2026-10-28 停电，两个库都坏，
      当晚 00:43 那份 Copy-Item 备份同样打不开。）

用法:
    python tools/snapshot.py                    # 快照 config.DB_PATH → ./snapshots/
    python tools/snapshot.py --src a.db --outdir snaps --keep 14
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C  # noqa: E402


def snapshot(src: str, dst: str) -> tuple:
    if os.path.exists(dst):
        os.remove(dst)
    con = sqlite3.connect(src, timeout=30)
    try:
        con.execute("pragma busy_timeout=20000")
        con.execute("VACUUM INTO ?", (dst,))
    finally:
        con.close()

    chk = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    try:
        result = chk.execute("pragma quick_check").fetchone()[0]
    finally:
        chk.close()
    size = round(os.path.getsize(dst) / 1024 / 1024, 2)
    return result, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=C.DB_PATH)
    ap.add_argument("--outdir", default="snapshots")
    ap.add_argument("--keep", type=int, default=14, help="保留最近几份")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print(f"源库不存在: {args.src}")
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.src))[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(args.outdir, f"{stem}-{stamp}.db")

    try:
        result, size = snapshot(args.src, dst)
    except Exception as e:
        print(f"快照失败: {str(e)[:160]}")
        return 1

    if result != "ok":
        print(f"快照可疑（quick_check={str(result)[:80]}）: {dst}")
        return 1

    print(f"快照 OK  {dst}  {size} MB")

    # 轮转：只留最近 N 份
    olds = sorted(
        [os.path.join(args.outdir, f) for f in os.listdir(args.outdir) if f.startswith(stem + "-")],
        key=os.path.getmtime, reverse=True)
    for p in olds[args.keep:]:
        os.remove(p)
        print(f"  轮转删除 {os.path.basename(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
