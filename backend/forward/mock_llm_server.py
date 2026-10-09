# -*- coding: utf-8 -*-
"""
本地 mock LLM server —— 仅用于"无真实 API key"地冒烟跑通正向检查多 judge 工程。

设计要点：
1. 完全兼容 OpenAI /chat/completions（也兼容 /v1/chat/completions）。
2. 按 prompt 关键词区分四个调用方：
   - 阶段2 映射更新（ai_maps）：返回空映射 JSON（下游 matcher 不依赖 map 也能跑）。
   - 阶段3 身份匹配（name_ai_matcher）：返回 N 行"i. 匹配/不匹配"。
   - 阶段4 属性比对（attr_mismatch）：返回 N 行"i. 一致/不一致"。
   - 仲裁（multi_judge_arbitrator）：返回 final_verdict 等 JSON。
3. 用请求体里的 model 名做 hash 种子：同 model 的 judge 得到一致判定（模拟
   "同模型重复在无温度抖动时结论一致"），异 model 得到不同判定（模拟"异模型分歧"）。
   这样 mock 跑天然能演示多 judge 嫁接的核心价值。
4. 响应体总字节 > 512，以通过 matcher 的 read_stream_with_stall_guard（min_bytes=512）。
"""
import json
import hashlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8731
PAD = "#" + "x" * 600  # 无害 padding，不含"匹配/不一致"等关键词，且 > 512 字节


def _h(s: str) -> int:
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)


def _count_groups(user_text: str) -> int:
    """阶段3/4 的 build_prompt 把每对渲染成 '--- 组 N ---'，据此数对数。"""
    return len(re.findall(r"--- 组 (\d+) ---", user_text))


def _build_response(body: dict) -> dict:
    messages = body.get("messages", []) or []
    sys_txt, user_txt = "", ""
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        if role == "system":
            sys_txt += content + "\n"
        elif role == "user":
            user_txt += content + "\n"
    model = (body.get("model") or "mock").strip()
    text = sys_txt + "\n" + user_txt

    # ---- 阶段判定（按关键词，特异性从高到低）----
    is_map = ("name_map" in user_txt) or ("OneState" in user_txt) or ("signal_map" in user_txt)
    is_identity = ("判断每组 HLR 需求名称与 EoICD 信号名称" in user_txt)
    is_attr = ("属性比对专家" in sys_txt) or ("属性值是否等价" in user_txt)
    is_arb = ("复查仲裁者" in sys_txt) or ("final_verdict" in text and "仲裁" in text)

    if is_map:
        # 阶段2：空映射即可，下游用语义匹配；返回合法 JSON 字符串
        content = json.dumps({}, ensure_ascii=False)

    elif is_identity:
        n = _count_groups(user_txt)
        lines = []
        for i in range(1, n + 1):
            r = _h(f"{model}|id|{i}|{user_txt[:200]}") % 100
            verdict = "匹配" if r < 65 else "不匹配"
            lines.append(f"{i}. {verdict}")
        lines.append(PAD)
        content = "\n".join(lines)

    elif is_attr:
        n = _count_groups(user_txt)
        lines = []
        for i in range(1, n + 1):
            r = _h(f"{model}|attr|{i}|{user_txt[:200]}") % 100
            verdict = "一致" if r < 65 else "不一致"
            lines.append(f"{i}. {verdict}")
        lines.append(PAD)
        content = "\n".join(lines)

    elif is_arb:
        content = json.dumps({
            "final_verdict": "已落实",
            "final_matched_hlr": ["R1"],
            "analysis": "（mock 仲裁）维持/调整结论",
            "confidence": 0.8,
            "_pad": "x" * 600,
        }, ensure_ascii=False)

    else:
        # 兜底：空 JSON，避免任何调用方解析失败
        content = json.dumps({"_pad": "x" * 600}, ensure_ascii=False)

    return {"choices": [{"message": {"content": content}}]}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}
        out = _build_response(body)
        data = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # 静默


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    print(f"[mock-llm] listening on http://127.0.0.1:{PORT}/chat/completions", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
