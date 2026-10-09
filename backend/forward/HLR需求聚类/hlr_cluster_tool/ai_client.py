# -*- coding: utf-8 -*-
"""
AI 接口层（LLM 客户端）
=======================
用于在需求文档变更后，调用大模型自动重建 / 增量更新 AI 辅助映射表
（name_map / state_map / signal_map）。

设计要点
--------
1. **只用标准库**（urllib）实现 OpenAI 兼容的 `/chat/completions` 调用，不引入额外依赖，
   保持本工具"仅需 PyYAML"的轻量特性；内网可离线运行（不配 key 即不启用 AI）。
2. **兼容一切 OpenAI 协议的服务**：DeepSeek、通义千问、Kimi、智谱、
   以及内部部署的 vLLM / Ollama / one-api 网关等——只需配置 `base_url` + `model` + `api_key`。
3. 配置优先级：**命令行覆盖 > 环境变量 > 配置文件**；base_url / model / api_key 均**无内置默认**，必须显式提供（方便随时切换服务商/模型，不让代码绑定某家）。

配置文件 `ai_config.json`（**base_url / model / api_key 均为必填，无代码内置默认**）
-------------------------------------------------------------------------------
{
  "base_url": "<OpenAI 兼容端点，如 https://api.deepseek.com/v1 或内网网关>",
  "api_key": "<你的 Key>",
  "model": "<模型名，如 deepseek-chat / qwen-plus / Qwen2.5-32B-Instruct>",
  "timeout": 120,
  "max_retries": 2,
  "temperature": 0.0,
  "batch_size": 20
}

环境变量（优先级高于配置文件；三者皆需提供，无内置默认）
------------------------------------------------------
  仅认：BASE_URL / API_KEY / AI_MODEL（已去除历史兼容别名）
"""
import os
import re
import sys
import json
import time
import urllib.request
import urllib.error

# 注意：base_url / model / api_key 均**不内置默认**，必须由
# 环境变量(BASE_URL / API_KEY / AI_MODEL) 或 ai_config.json 显式提供；缺任一则 AI 不启用。
# 这样可避免把某个具体服务商(如 DeepSeek)硬编码进代码，方便随时切换模型/网关。
DEFAULT_CONFIG = {
    'base_url': '',
    'model': '',
    'api_key': '',
    'timeout': 120,
    'max_retries': 2,
    'temperature': 0.0,
    'batch_size': 20,
}

# 仅认主用变量名：BASE_URL / API_KEY / AI_MODEL（已去除历史兼容别名）。
ENV_KEYS = {
    'base_url': ['BASE_URL'],
    'api_key': ['API_KEY'],
    'model': ['AI_MODEL'],
}
ENV_NUM_KEYS = {
    'timeout': ['HLR_AI_TIMEOUT'],
    'batch_size': ['HLR_AI_BATCH_SIZE'],
}


class AIError(Exception):
    """AI 调用或解析失败"""


def extract_json(text):
    """从模型返回文本中稳健地提取 JSON 对象/数组"""
    if not text or not text.strip():
        raise AIError('模型返回为空')
    s = text.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', s, re.S)   # ```json ... ``` 包裹
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # 兜底：截取第一个 { / [ 到最后一个 } / ]
    for open_ch, close_ch in (('{', '}'), ('[', ']')):
        i, j = s.find(open_ch), s.rfind(close_ch)
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    raise AIError(f'无法解析模型返回的 JSON：{s[:200]}')


def _normalize_overrides(overrides):
    """把环境变量名（BASE_URL/API_KEY/AI_MODEL）归一为内部键
    （base_url/api_key/model），再交给 __init__ 覆盖。"""
    if not overrides:
        return {}
    env_to_internal = {}
    for internal, names in ENV_KEYS.items():
        for n in names:
            env_to_internal[n] = internal
    out = {}
    for k, v in (overrides or {}).items():
        if v in (None, ''):
            continue
        out[env_to_internal.get(k, k)] = v
    return out


class AIClient:
    """OpenAI 兼容协议的轻量客户端（标准库实现）"""

    def __init__(self, config=None):
        self.cfg = dict(DEFAULT_CONFIG)
        for k, v in (config or {}).items():
            if v not in (None, ''):
                self.cfg[k] = v
        self.base_url = str(self.cfg['base_url']).rstrip('/')
        self.call_count = 0
        self.usage_tokens = 0

    # ---------- 构造 ----------
    @classmethod
    def from_config(cls, config_path=None, overrides=None, explicit=False):
        """按 配置文件 + 环境变量 + 命令行覆盖 的顺序装配配置

        explicit=True 表示 config_path 由用户显式指定；此时文件不存在会给出警告，
        默认路径缺失则静默（因为环境变量/命令行同样能提供配置）。
        """
        cfg = {}
        if config_path:
            if os.path.exists(config_path):
                with open(config_path, encoding='utf-8') as f:
                    cfg.update(json.load(f))
            elif explicit:
                print(f"警告：找不到 AI 配置文件 {config_path}", file=sys.stderr)
        for key, envs in ENV_KEYS.items():
            for e in envs:
                if os.environ.get(e):
                    cfg[key] = os.environ[e]
                    break
        for key, envs in ENV_NUM_KEYS.items():
            for e in envs:
                if os.environ.get(e):
                    try:
                        cfg[key] = int(os.environ[e])
                    except ValueError:
                        pass
                    break
        cfg.update(_normalize_overrides(overrides))
        return cls(cfg)

    # ---------- 状态 ----------
    @property
    def enabled(self):
        """是否具备调用条件：base_url / model / api_key 三者齐全才视为启用，
        任一项缺失则未启用，调用方应降级为纯正则/静态映射。"""
        return bool(self.cfg.get('base_url')) and bool(self.cfg.get('model')) \
            and bool(self.cfg.get('api_key'))

    def describe(self):
        key = str(self.cfg.get('api_key') or '')
        masked = (key[:4] + '***' + key[-4:]) if len(key) > 8 else ('***' if key else '(未配置)')
        model = self.cfg.get('model') or '(未配置)'
        base = self.base_url or '(未配置)'
        return f"{model} @ {base} (key={masked})"

    # ---------- 调用 ----------
    def chat(self, system, user, json_mode=True):
        """发起一次对话补全，返回模型文本"""
        if not self.base_url or not self.cfg.get('model'):
            raise AIError(
                'AI 未配置 base_url / model，请在环境变量 '
                '(BASE_URL / AI_MODEL) 或 ai_config.json 中设置')
        url = self.base_url + '/chat/completions'
        messages = ([{'role': 'system', 'content': system}] if system else []) + \
                   [{'role': 'user', 'content': user}]
        payload = {
            'model': self.cfg['model'],
            'messages': messages,
            'temperature': self.cfg.get('temperature', 0.0),
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {self.cfg['api_key']}",
        }
        retries = int(self.cfg.get('max_retries', 2))
        timeout = float(self.cfg.get('timeout', 120))
        last_err = None
        took_off_json_mode = False

        for attempt in range(retries + 1):
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method='POST')
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode('utf-8'))
                self.call_count += 1
                usage = body.get('usage') or {}
                self.usage_tokens += int(usage.get('total_tokens') or 0)
                return self._content_of(body)
            except urllib.error.HTTPError as e:
                detail = ''
                try:
                    detail = e.read().decode('utf-8', 'ignore')[:300]
                except Exception:
                    pass
                # 部分服务不支持 response_format，去掉后重试一次
                if e.code == 400 and 'response_format' in payload and not took_off_json_mode:
                    payload.pop('response_format', None)
                    took_off_json_mode = True
                    last_err = AIError(f'HTTP {e.code}: {detail}')
                    continue
                last_err = AIError(f'HTTP {e.code}：{detail}')
            except urllib.error.URLError as e:
                last_err = AIError(f'连接失败（{e.reason}）：{url}')
            except Exception as e:
                last_err = AIError(f'{type(e).__name__}: {e}')
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise last_err or AIError('AI 调用失败')

    def chat_json(self, system, user):
        """调用并解析为 JSON"""
        return extract_json(self.chat(system, user, json_mode=True))

    @staticmethod
    def _content_of(body):
        try:
            choices = body.get('choices') or []
            msg = choices[0].get('message') or {}
            # 兼容推理模型把内容放在 reasoning_content 的情况
            return msg.get('content') or msg.get('reasoning_content') or ''
        except Exception:
            raise AIError(f'响应结构异常：{str(body)[:200]}')

    # ---------- 连通性自检 ----------
    def ping(self):
        """最小代价连通性测试，返回 (ok, 说明)"""
        if not self.enabled:
            return False, '未配置 base_url / model / api_key（请检查环境变量或 ai_config.json）'
        try:
            r = self.chat_json('你是一个测试助手。', '只输出 JSON：{"ok": 1}')
            return ('ok' in r), f'连通正常，返回 {r}'
        except AIError as e:
            return False, str(e)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='AI 接口连通性自检')
    ap.add_argument('--ai-config', default=None, help='AI 配置文件 ai_config.json')
    ap.add_argument('--ai-base', default=None, help='覆盖 base_url')
    ap.add_argument('--ai-key', default=None, help='覆盖 api_key')
    ap.add_argument('--ai-model', default=None, help='覆盖 model')
    a = ap.parse_args()
    cli = AIClient.from_config(a.ai_config,
                               {'base_url': a.ai_base, 'api_key': a.ai_key, 'model': a.ai_model},
                               explicit=bool(a.ai_config))
    print('配置:', cli.describe())
    print('启用:', cli.enabled)
    if not cli.enabled:
        print('提示：未配置 base_url / model / api_key。可设置环境变量 '
              'BASE_URL / AI_MODEL / API_KEY，或填写 ai_config.json。')
        sys.exit(2)
    ok, msg = cli.ping()
    print('自检:', '通过' if ok else '失败', '-', msg)
    sys.exit(0 if ok else 1)
