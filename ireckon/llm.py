# -*- coding: utf-8 -*-
"""最小 LLM 客户端：只用标准库 urllib，兼容 OpenAI 风格接口。"""
import json
import urllib.error
import urllib.request

try:                       # 项目根的 config.py 优先
    import config as C
except ImportError:        # pip 安装后没有 config.py，用包内默认值（没有兜底时这里直接崩）
    from . import config_defaults as C


class LLMError(RuntimeError):
    pass


def chat(messages, model=None, temperature=0.7, max_tokens=800,
         json_mode=False, api_key=None, base_url=None) -> str:
    """一次调用，返回文本。失败抛 LLMError（带服务端原话，方便定位）。"""
    key = api_key or C.API_KEY
    if not key or key.startswith("sk-你的"):
        raise LLMError("config.py 里还没填 API_KEY")

    body = {
        "model": model or C.MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        (base_url or C.BASE_URL).rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
    except Exception as e:
        raise LLMError(str(e))
    return data["choices"][0]["message"]["content"]


def chat_json(messages, **kw):
    """要求返回 JSON 并把 ``` 围栏剥掉再解析。"""
    kw.setdefault("json_mode", True)
    txt = chat(messages, **kw).strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[-1]
        if txt.rstrip().endswith("```"):
            txt = txt.rstrip()[:-3]
    return json.loads(txt.strip())
