# -*- coding: utf-8 -*-
"""版本与名称审计（0.2.8 起随包发布）。

为什么要有它：
    他说过两次"包号与版号不一致"。逐次手工去修，修一处漏一处——
    2026-10-31 连着出现 0.1.7 / 0.2.4 / 0.2.5 / 0.2.6 / IRckon / 俺寻思-*.zip 混着出现。
    所以把"哪几处必须同号"写成可执行的检查，放进测试里。

判据：
  硬性（不一致就失败）
    pyproject.version == ireckon.__version__ == CHANGELOG 第一条版本
    pyproject.name == "ireckon"
    tools/package.py 的产物前缀 == IReckon
    README 前 40 行里出现当前版本号与对外名
  软性（只报告，不失败）
    其它文件里出现的版本号，如果既不是当前版本、也不在 CHANGELOG 历史里 → 打印出来
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "IReckon"
PKG_NAME = "ireckon"
SCAN_EXT = ('.py', '.md', '.toml', '.sh', '.txt')
SKIP_DIRS = {'__pycache__', '.pytest_cache', '.git', 'logs', 'backups', 'dist', 'build'}


def current_version() -> str:
    s = open(os.path.join(ROOT, 'pyproject.toml'), encoding='utf-8').read()
    return re.search(r'version\s*=\s*"([^"]+)"', s).group(1)


def _iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(SCAN_EXT):
                yield os.path.join(dirpath, fn)


def audit(strict: bool = True) -> int:
    v = current_version()
    problems, notes = [], []

    pyproject = open(os.path.join(ROOT, 'pyproject.toml'), encoding='utf-8').read()
    init = open(os.path.join(ROOT, 'ireckon', '__init__.py'), encoding='utf-8').read()
    cl = open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8').read()
    readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    pkg = open(os.path.join(ROOT, 'tools', 'package.py'), encoding='utf-8').read()

    v_py = re.search(r'version\s*=\s*"([^"]+)"', pyproject).group(1)
    v_init = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    v_cl = re.search(r'##\s*([\d.]+)', cl).group(1)
    for label, got in (('pyproject', v_py), ('__init__', v_init), ('CHANGELOG', v_cl)):
        if got != v:
            problems.append(f'{label} 版本是 {got}，应为 {v}')

    name = re.search(r'name\s*=\s*"([^"]+)"', pyproject).group(1)
    if name != PKG_NAME:
        problems.append(f'pyproject name 是 {name}，应为 {PKG_NAME}')
    if f'__name_ascii__ = "{NAME}"' not in init:
        problems.append(f'__init__ 里没有 __name_ascii__ = "{NAME}"')
    if f'f"{NAME}-{{_version()}}.zip"' not in pkg:
        problems.append(f'package.py 的产物名不是 {NAME}-<version>.zip')

    head = "\n".join(readme.splitlines()[:40])
    if v not in head:
        problems.append(f'README 前 40 行里没有当前版本号 {v}')
    if NAME not in head:
        problems.append(f'README 前 40 行里没有对外名 {NAME}')

    # 软性：其它地方出现的版本号
    cl_history = set(re.findall(r'##\s*([\d.]+)', cl))
    pat = re.compile(r'\b0\.\d+\.\d+\b')
    for path in _iter_files():
        rel = os.path.relpath(path, ROOT)
        if rel in ('pyproject.toml', 'CHANGELOG.md'):
            continue
        try:
            txt = open(path, encoding='utf-8', errors='ignore').read()
        except Exception:
            continue
        for m in set(pat.findall(txt)):
            if m == v:
                continue
            if m in cl_history:
                notes.append(f'{rel} 提到历史版本 {m}（可接受）')
            else:
                notes.append(f'{rel} 出现陌生版本号 {m}')

    print(f'  当前版本 {v} / 对外名 {NAME}')
    if problems:
        print('  硬性问题：')
        for p in problems:
            print('   ✗', p)
    else:
        print('  硬性检查全部通过')
    if notes:
        print('  软性提示（不失败）：')
        for n in sorted(set(notes))[:12]:
            print('   ·', n)
    return 1 if (strict and problems) else 0


if __name__ == '__main__':
    sys.exit(audit(strict='--lax' not in sys.argv))
