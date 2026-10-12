#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳 MCP Streamable HTTP 客户端（仅标准库，零第三方依赖）。

对 https://mcp.mcd.cn 做 JSON-RPC 直连：
    initialize -> tools/list -> tools/call

子命令：
    ping                     校验 Token 与连通性
    tools-list               列出全部工具（可 --out 导出含入参 schema）
    call <tool> '<json>'     调用任意工具
    fetch-stores             查询门店（city + keyword，beType 1 到店 / 5 得来速）
    now-time                 获取服务器当前时间

Token 读取优先级：--token > 环境变量 MCD_MCP_TOKEN > .env 文件。
Token 只用于请求头，不会写入任何输出文件。

实测契约（2026-10-12 live 校验，勿凭记忆修改）：
  * 响应同时带 content[].text 与 structuredContent，优先取 structuredContent。
  * 业务体为 {success, code, message, datetime, traceId, data}；
    success=false 时 code 携带业务错误码（600014 地址无结果 / 600058 城市或关键词为空）。
  * isError 恒为 false，业务失败藏在 success=false + code/message，必须先校验 success。
  * Token 无效 → HTTP 401 + 纯文本响应体（非 JSON）。
  * query-nearby-stores 单次最多返回 5 家门店，无分页参数；
    longitude / latitude 恒为 0.0，不可用于地图渲染。
  * query-meals 价格单位为元（字符串）；calculate-price 全部金额为分（整数）。
  * 券：只有 query-store-coupons 返回结构化 couponId/couponCode 可进核价；
    券是独立加购商品，必须作为一个 item 传才生效，挂到普通餐品 item 上无效。
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 40
RETRY = 3
RETRY_SLEEP = 1.0
USER_AGENT = "mcd-combo-lab/1.0.0"

# 官方限流：单 Token 600 次/分钟。扇出时保持温和间隔。
DEFAULT_THROTTLE = 0.15


class McpError(RuntimeError):
    """MCP 调用失败：携带 code / message，便于上层区分业务错误与网络错误。"""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------ Token

def load_token(cli_token: str = "") -> str:
    if cli_token:
        return cli_token
    env = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if env:
        return env
    roots = (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.getcwd())
    for base in roots:
        env_path = os.path.join(base, ".env")
        if os.path.isfile(env_path):
            with open(env_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("MCD_MCP_TOKEN="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ------------------------------------------------------ 协议

def rpc(method: str, params: dict, token: str, url: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 10 ** 9, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + token,
        "User-Agent": USER_AGENT,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_exc = None
    for attempt in range(RETRY):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = resp.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code == 401:
                raise McpError("401 Token 无效、已过期或未提供，请检查 MCD_MCP_TOKEN。", 401)
            if exc.code == 429:
                raise McpError("429 触发限流（600 次/分钟），请降低调用频率。", 429)
            if 500 <= exc.code < 600 and attempt < RETRY - 1:
                last_exc = "HTTP %s：%s" % (exc.code, detail[:200])
                time.sleep(RETRY_SLEEP)
                continue
            raise McpError("HTTP %s：%s" % (exc.code, detail[:400]), exc.code)
        except urllib.error.URLError as exc:
            if attempt < RETRY - 1:
                last_exc = "网络错误：%r" % (exc,)
                time.sleep(RETRY_SLEEP)
                continue
            raise McpError("网络错误：%r（若需代理请检查系统代理设置）" % (exc,))
    else:  # pragma: no cover - 仅当重试全部耗尽
        raise McpError(str(last_exc))

    raw = raw.strip()
    if not raw:
        return {}
    # 兼容 SSE 形态响应
    if raw.startswith("event:") or raw.startswith("data:") or "\ndata:" in raw:
        raw = "\n".join(ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:"))
    try:
        msg = json.loads(raw)
    except ValueError:
        raise McpError("响应不是合法 JSON：%s" % raw[:300])
    if "error" in msg:
        err = msg["error"]
        raise McpError("MCP 错误 %s：%s" % (err.get("code"), err.get("message")), err.get("code"))
    return msg.get("result", {})


def initialize(token: str, url: str) -> dict:
    return rpc("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "mcd-combo-lab", "version": "1.0.0"},
    }, token, url)


def tools_list(token: str, url: str) -> list:
    return rpc("tools/list", {}, token, url).get("tools", [])


def call_tool(name: str, arguments: dict, token: str, url: str) -> dict:
    return rpc("tools/call", {"name": name, "arguments": arguments or {}}, token, url)


# ------------------------------------------------------ 响应解析

def extract_json(text):
    """兜底：从「说明文字 + JSON」混合体里抠出 JSON。"""
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    starts = [i for i in (stripped.find('{"'), stripped.find("[{")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    open_ch = stripped[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for idx in range(start, len(stripped)):
        ch = stripped[idx]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stripped[start:idx + 1])
                except ValueError:
                    break
    return None


def unwrap(result: dict):
    """把 MCP 工具返回统一成 Python 业务对象。

    实测：structuredContent 已是规整的 {success, code, message, data}，
    优先取它；缺省时才回退到 content[].text 解析。
    """
    if not isinstance(result, dict):
        return result

    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        if not structured.get("success", True):
            raise McpError("接口返回失败 code=%s：%s" % (
                structured.get("code"), structured.get("message")), structured.get("code"))
        data = structured.get("data")
        inner = extract_json(data) if isinstance(data, str) else data
        return inner if inner is not None else data

    content = result.get("content") or []
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    joined = "\n".join(texts).strip()
    parsed = extract_json(joined)
    if parsed is None:
        if result.get("isError"):
            raise McpError("工具调用失败：%s" % joined[:300])
        return joined
    if isinstance(parsed, dict) and "success" in parsed:
        if not parsed.get("success"):
            raise McpError("接口返回失败 code=%s：%s" % (
                parsed.get("code"), parsed.get("message")), parsed.get("code"))
        data = parsed.get("data")
        inner = extract_json(data) if isinstance(data, str) else data
        return inner if inner is not None else data
    if result.get("isError"):
        raise McpError("工具调用失败：%s" % joined[:300])
    return parsed


# ------------------------------------------------------ 子命令

def cmd_fetch_stores(args, token, url):
    params = {"beType": args.be_type, "searchType": 2, "city": args.city, "keyword": args.keyword}
    data = unwrap(call_tool("query-nearby-stores", params, token, url))
    stores = data if isinstance(data, list) else []
    payload = {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_tool": "query-nearby-stores",
        "params": params,
        "count": len(stores),
        "stores": stores,
    }
    dump(payload, args.out)
    if not args.out:
        for s in stores:
            print("%s | %s | %s | %sm" % (s.get("storeCode"), s.get("storeName"),
                                          s.get("address"), s.get("distance")))
    else:
        print("门店数量：%d" % len(stores))
    return 0


def cmd_now_time(args, token, url):
    data = unwrap(call_tool("now-time-info", {}, token, url))
    payload = {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_tool": "now-time-info",
        "time": data,
    }
    dump(payload, args.out)
    if isinstance(data, dict):
        print("服务器时间：%s（%s）" % (data.get("formatted"), data.get("timezone")))
    return 0


def dump(payload, out_path):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("已写入：%s" % os.path.abspath(out_path))
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="麦当劳 MCP Streamable HTTP 客户端")
    parser.add_argument("action", choices=["ping", "tools-list", "call",
                                           "fetch-stores", "now-time"])
    parser.add_argument("tool_or_args", nargs="?", default="")
    parser.add_argument("arguments", nargs="?", default="{}")
    parser.add_argument("--token", default="")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", default="")
    # fetch-stores
    parser.add_argument("--city", default="")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--be-type", type=int, default=1,
                        help="1-到店自提（无 beCode），5-得来速车道取餐（返回 beCode）")
    args = parser.parse_args()

    token = load_token(args.token)
    if not token:
        print("缺少 MCP Token：使用 --token、设置环境变量 MCD_MCP_TOKEN，或写入 .env。", file=sys.stderr)
        return 2

    try:
        if args.action == "ping":
            print(json.dumps(initialize(token, args.url), ensure_ascii=False))
            return 0
        if args.action == "tools-list":
            tools = tools_list(token, args.url)
            print("工具数量：%d" % len(tools))
            for t in tools:
                print("- %s | %s" % (t.get("name"), (t.get("description") or "").split("\n")[0][:70]))
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(tools, fh, ensure_ascii=False, indent=2)
                print("已写入：%s" % os.path.abspath(args.out))
            return 0
        if args.action == "call":
            try:
                params = json.loads(args.arguments or "{}")
            except ValueError as exc:
                print("参数不是合法 JSON：%r" % (exc,), file=sys.stderr)
                return 2
            result = unwrap(call_tool(args.tool_or_args, params, token, url=args.url))
            if args.out:
                dump(result, args.out)
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2)
                      if isinstance(result, (dict, list)) else str(result))
            return 0
        if args.action == "fetch-stores":
            if not args.city or not args.keyword:
                print("fetch-stores 需要同时提供 --city 与 --keyword（缺失会触发 600058）。",
                      file=sys.stderr)
                return 2
            return cmd_fetch_stores(args, token, args.url)
        if args.action == "now-time":
            return cmd_now_time(args, token, args.url)
    except McpError as exc:
        print("MCP 调用失败：%s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
