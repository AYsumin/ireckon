#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 I Reckon（俺寻思）：排除数据库/缓存，扫描敏感内容，生成干净 zip。

用法:
    python tools/package.py              # 打包 + 扫描（产物写到 OUT）
    python tools/package.py --no-scan    # 只打包
    python tools/package.py --check      # **只读预检**：打包进临时目录，报条目与扫描结果，
                                         #   不写 OUT、不删 OUT；该排除的东西混进来就中止

排除：
    *.db / *.db-wal / *.db-shm     ← 记忆数据库，绝不外发
    __pycache__ / *.pyc            ← 编译缓存（会带本机绝对路径）
    .env / .git / *.log
    tools/blocklist.local          ← **黑名单本身也不外发**

黑名单：
    放在 tools/blocklist.local（每行一个词），随包排除。
    **不要把敏感词直接写进这个脚本**——脚本是要发出去的。

扫描（对 zip 内每个文件做字节级检查）：
    API key 形态、手机号形态、本地黑名单词
    一旦命中就报错退出，不出 zip

--check 存在的理由（2026-10-31 补）：
    2026-10-30 我为出两个版本手搓了一个打包脚本，没有 SKIP 规则，
    把 tools/blocklist.local（他的 QQ 号、名字、地名）打进了 0.1.0–0.1.7 每一个包。
    官方规则里写着"黑名单本身不外发"，而我绕过了它——检查变成了装饰。
    所以：**先干跑一遍，看得见条目，再决定要不要真出包。**
"""
import os
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _version() -> str:
    """版本号只认 pyproject，别让产物名跟版本走散（2026-10-31 补）。"""
    try:
        import re as _re
        txt = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
        m = _re.search(r'version\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else "0.0.0"
    except Exception:
        return "0.0.0"


OUT = os.path.join(os.path.dirname(ROOT), f"IReckon-{_version()}.zip")
BLOCKLIST = os.path.join(ROOT, "tools", "blocklist.local")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".idea", ".vscode", "node_modules",
             "backups", "snapshots", "logs"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm", ".local"}
SKIP_NAMES = {".env", ".DS_Store"}

PATTERNS = [
    (re.compile(rb"sk-[A-Za-z0-9]{16,}"), "疑似 API Key"),
    (re.compile(rb"(?<!\d)1[3-9]\d{9}(?!\d)"), "疑似手机号"),
]

# 预检硬闸门：这些一出现就是事故。不看 SKIP 有没有生效，直接中止。
FORBIDDEN_EXT = {".db", ".db-wal", ".db-shm", ".log", ".local", ".pyc", ".pyo"}
FORBIDDEN_NAMES = {"blocklist.local", ".env"}
# 目录级的硬闸门：编译缓存与测试缓存会带本机绝对路径，绝不许进包
FORBIDDEN_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "logs"}


def load_blocklist():
    if not os.path.exists(BLOCKLIST):
        return []
    words = []
    with open(BLOCKLIST, encoding="utf-8") as f:
        for line in f:
            w = line.strip()
            if w and not w.startswith("#"):
                words.append(w)
    return words


def include(path: str) -> bool:
    name = os.path.basename(path)
    if name in SKIP_NAMES:
        return False
    if os.path.splitext(name)[1].lower() in SKIP_EXT:
        return False
    parts = set(os.path.relpath(path, ROOT).split(os.sep))
    return not (parts & SKIP_DIRS)


def collect():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if include(p):
                files.append(p)
    return sorted(files)


def scan(zip_path: str, blocklist):
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            data = z.read(info)
            for pat, label in PATTERNS:
                m = pat.search(data)
                if m:
                    problems.append((info.filename, label,
                                     m.group(0).decode("utf-8", "ignore")[:24]))
            for w in blocklist:
                if w.encode("utf-8") in data:
                    problems.append((info.filename, "黑名单词", w))
    return problems


def guard_entries(names):
    """预检闸门：包里不许出现数据库 / 日志 / 黑名单本体的条目。"""
    bad = []
    for n in names:
        base = os.path.basename(n)
        ext = os.path.splitext(base)[1].lower()
        parts = set(n.split('/'))
        if ext in FORBIDDEN_EXT or base in FORBIDDEN_NAMES or (parts & FORBIDDEN_DIRS):
            bad.append(n)
    return bad


def build_zip(zip_path: str, files):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, os.path.relpath(p, ROOT).replace(os.sep, "/"))


def existing_entries():
    """现有正式包里的条目（用来比对这次会不会多出东西）。"""
    if not os.path.exists(OUT):
        return None
    try:
        with zipfile.ZipFile(OUT) as z:
            return sorted(z.namelist())
    except Exception:
        return None


def check(blocklist):
    """只读预检：打包进临时目录 → 过闸门 → 过扫描 → 与旧包比对；全程不碰 OUT。"""
    files = collect()
    tmpdir = tempfile.mkdtemp(prefix="ireckon-check-")
    tmpzip = os.path.join(tmpdir, "check.zip")
    try:
        build_zip(tmpzip, files)
        names = [os.path.relpath(p, ROOT).replace(os.sep, "/") for p in files]
        size = round(os.path.getsize(tmpzip) / 1024, 1)

        print(f"[check] 将要打进包：{len(files)} 个条目，{size} KB（临时文件，不写 {os.path.basename(OUT)}）")
        for n in names:
            print("   +", n)

        bad = guard_entries(names)
        problems = scan(tmpzip, blocklist)
        prev = existing_entries()

        print()
        print("[check] 黑名单：" + (
            f"tools/blocklist.local（{len(blocklist)} 词，不随包走）" if blocklist else "无"))
        if prev is None:
            print("[check] 没有已存在的正式包可比对")
        else:
            added = sorted(set(names) - set(prev))
            removed = sorted(set(prev) - set(names))
            print(f"[check] 与已存在的正式包比对：原来 {len(prev)} 条 → 现在 {len(names)} 条")
            for n in added:
                print("   + 新增", n)
            for n in removed:
                print("   - 少了", n)
            if not added and not removed:
                print("   条目集合一致")

        rc = 0
        if bad:
            print("！！闸门拦下：包里出现了本该排除的东西")
            for n in bad:
                print("   ", n)
            rc = 1
        if problems:
            print("！！扫描到可疑内容")
            for f, label, frag in problems:
                print(f"   {f}: {label} → {frag}")
            rc = 1
        if rc == 0:
            print("[check] 通过：无数据库/日志/黑名单条目，无 key / 手机号 / 黑名单词")
        return rc
    finally:
        try:
            if os.path.exists(tmpzip):
                os.remove(tmpzip)
            os.rmdir(tmpdir)
        except Exception:
            pass


def main():
    if "--check" in sys.argv:
        return check(load_blocklist())      # 只读预检：绝不触碰 OUT

    do_scan = "--no-scan" not in sys.argv
    blocklist = load_blocklist()
    files = collect()

    if os.path.exists(OUT):
        os.remove(OUT)
    build_zip(OUT, files)

    size = round(os.path.getsize(OUT) / 1024, 1)
    print(f"打包完成：{OUT}  {size} KB  {len(files)} 个文件")
    print(f"黑名单：{'tools/blocklist.local（' + str(len(blocklist)) + ' 词，不随包走）' if blocklist else '无（未建 blocklist.local）'}")
    for p in files:
        print("   +", os.path.relpath(p, ROOT))

    if not do_scan:
        return 0

    problems = scan(OUT, blocklist)
    print()
    if problems:
        print("！！扫描到可疑内容，这个 zip 不要发：")
        for f, label, frag in problems:
            print(f"   {f}: {label} → {frag}")
        os.remove(OUT)
        print("已删除 zip")
        return 1
    print("扫描通过：无 key / 无手机号 / 无黑名单词")
    return 0


if __name__ == "__main__":
    sys.exit(main())
