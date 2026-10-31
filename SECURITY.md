# 安全说明

## API Key

- 仓库里的 `config.py` 只有占位符 `sk-你的key`，**不含任何可用的 key**。
- 请自行填入你的 key，并且**不要把它提交到仓库**。建议用环境变量：

```bash
set DEEPSEEK_API_KEY=sk-...        # Windows
export DEEPSEEK_API_KEY=sk-...     # Linux/macOS
```

- 如果某次测试把 key 写进了 `config.py`，请在使用后立刻到服务商后台轮换。

## 数据

- 记忆库是本地 SQLite 文件（默认 `memory.db`），不上传任何地方。
- 只有在你配置了 `API_KEY` 并显式调用蒸馏/对话时，才会有内容发往你所选的模型服务商。

## 报告问题

发现安全问题请开 issue，不要附带真实 key、真实对话内容或任何个人信息。
