#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昨日重现：把某一天的锚摆出来给认，并记录认定。

用法：
    python tools/reproduce.py 2026-10-30              # 看那天（带原文片段）
    python tools/reproduce.py --yesterday             # 看昨天
    python tools/reproduce.py --yesterday --write     # 写到 out/reproduce/YYYY-MM-DD.md（供定时任务用）
    python tools/reproduce.py 2026-10-30 --verdict 12 confirmed
    python tools/reproduce.py 2026-10-30 --anchor 12 --verdict corrected --text "改成……" --evidence "原文片段"
    （--verdict 后面跟数字时按老写法理解：id + 类型；否则用 --anchor 指定 id）
    python tools/reproduce.py --stats 7               # 最近 7 天核账
"""
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ireckon import Memory                       # noqa: E402
from ireckon import recognition as R             # noqa: E402
import config as C                               # noqa: E402

OUT_DIR = (os.environ.get("IRECKON_REPRODUCE_DIR")
           or getattr(C, "REPRODUCE_DIR", None)
           or os.path.join(os.path.dirname(ROOT), "reproduce"))


def _parse(argv):
    day = None
    verdict = None
    text = ""
    note = ""
    days = 7
    write = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--yesterday":
            day = (date.today() - timedelta(days=1)).isoformat()
        elif a == "--today":
            day = date.today().isoformat()
        elif a == "--write":
            write = True
        elif a == "--verdict":
            verdict = argv[i + 1]
            i += 1
        elif a == "--text":
            text = argv[i + 1]
            i += 1
        elif a == "--note":
            note = argv[i + 1]
            i += 1
        elif a == "--stats":
            days = int(argv[i + 1])
            i += 1
        elif not a.startswith("-"):
            if day is None:
                day = a
            else:
                verdict_id = a
                i -= 0
                # 见 main 里处理
        i += 1
    return day, verdict, text, note, days, write


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0

    day = None
    verdict = None
    text = ""
    note = ""
    evidence = ""
    days = 7
    write = False
    anchor_id = None
    stats_mode = False

    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--yesterday":
            day = (date.today() - timedelta(days=1)).isoformat()
        elif a == "--today":
            day = date.today().isoformat()
        elif a == "--write":
            write = True
        elif a == "--stats":
            stats_mode = True
            if i + 1 < len(argv) and argv[i + 1].isdigit():
                days = int(argv[i + 1]); i += 1
        elif a == "--verdict":
            v1 = argv[i + 1]; i += 1
            if v1.isdigit():
                # 兼容老写法：--verdict 12 confirmed
                anchor_id = int(v1)
                verdict = argv[i + 1]; i += 1
            else:
                verdict = v1
        elif a == "--anchor":
            anchor_id = int(argv[i + 1]); i += 1
        elif a == "--text":
            text = argv[i + 1]; i += 1
        elif a == "--note":
            note = argv[i + 1]; i += 1
        elif a == "--evidence":
            evidence = argv[i + 1]; i += 1
        elif a.startswith("--"):
            print(f"未知参数 {a}")
            return 2
        else:
            if day is None and len(a) == 10 and a[4] == '-':
                day = a
            elif a.isdigit():
                anchor_id = int(a)
            elif day is None:
                day = a
        i += 1

    m = Memory()
    try:
        if stats_mode:
            st = m.recognition_stats(days=days)
            print(f"最近 {days} 天：")
            for d in st["days"]:
                print(f"  {d['day']}  {d['total']} 条，答了 {d['answered']} 条，"
                      f"互认率 {'—' if d['rate'] is None else d['rate']}")
            t = st["totals"]
            print(f"合计：{t['total']} 条，认 {t['confirmed']} / 认不出 {t['disputed']} / "
                  f"改 {t['corrected']} / 未核 {t['unknown']}")
            print(f"没人核的天数：{st['unanswered_days']}  互认率：{st['rate']}")
            return 0

        day = day or (date.today() - timedelta(days=1)).isoformat()
        if verdict:
            if anchor_id is None:
                print("要改认定得给锚 id：--verdict <id> <verdict>")
                return 2
            res = m.record_verdict(day, anchor_id, verdict, corrected_text=text,
                                   note=note, evidence=evidence)
            tag = "已改锚" if res.get("applied") else "只记了认定（没改锚）"
            print(f"已记：{day} 锚 {anchor_id} → {verdict}；{tag}；记录 {res['log_id']}")
            if res.get("note"):
                print(f"  备注：{res['note']}")
            return 0

        rep = m.reproduce_day(day)
        out = R.render(rep)
        print(out)
        if write:
            os.makedirs(OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, f"{day}.md")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(out + "\n")
            print(f"\n已写到 {path}")
        return 0
    finally:
        m.close()


if __name__ == "__main__":
    sys.exit(main())
