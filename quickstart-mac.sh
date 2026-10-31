#!/usr/bin/env bash
# macOS / Linux 一键自检：装依赖 → 跑测试 → 跑离线 demo
set -euo pipefail

cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "== Python: $($PY -V 2>&1)"
echo
echo "== 安装依赖（唯一的第三方依赖）"
$PY -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
$PY -m pip install --quiet "rank_bm25==0.2.2"

echo "== 标准测试套件"
$PY tests/test_pytest.py

echo
echo "== 离线结构验证（不需要 API key）"
$PY demo/offline_demo.py

echo
echo "== 注入预算实测"
$PY tools/verify_tokens.py | tail -4

echo
echo "全部完成。下一步："
echo "  1) 编辑 config.py 填 API_KEY（用云端）或 IRECKON_LOCAL（用本地模型）"
echo "  2) python examples/demo_local.py    # 接本地模型"
echo "  3) python examples/demo_chat.py     # 接云端模型"
