"""
Alter —— 本地待审网页后端（路线 A）

一个零额外依赖的本地服务器（Python 标准库 + boto3），负责：
  1. 托管网页界面（index.html）
  2. 提供 JSON API：
     - GET  /api/drafts            列出草稿（可按 ?status= / ?topics= 筛选）
     - POST /api/drafts/status     更新一条草稿的状态（已发/忽略/待审）
     - GET  /api/feeds             列出关注对象（含标签）
     - POST /api/feeds             新增一个关注对象
     - DELETE /api/feeds           删除一个关注对象
     - GET  /api/topics            列出所有已用过的标签
     - POST /api/fetch             手动触发抓取（可选 ?topics= 只抓某些标签）

安全：只绑定 127.0.0.1（本机），不对公网暴露。关电脑即停。
用法：在 Alter 目录开 PowerShell，运行 run.ps1，浏览器自动开 http://127.0.0.1:8765
"""

import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config

# ── 配置（与项目其余部分一致）────────────────────────────
REGION = os.environ.get("AWS_REGION", "us-east-1")
PROFILE = os.environ.get("ALTER_PROFILE", "alter-dev")
DRAFTS_TABLE = os.environ.get("TABLE_NAME", "alter-drafts")
FETCHER_FUNCTION = os.environ.get("FETCHER_FUNCTION", "alter-fetcher")
PORT = int(os.environ.get("ALTER_WEB_PORT", "8765"))

_HERE = os.path.dirname(os.path.abspath(__file__))
# feeds.json 由 fetcher 和网页共享，放在 functions/fetcher 下
_FEEDS_PATH = os.path.join(_HERE, "..", "functions", "fetcher", "feeds.json")
_PERSONAS_PATH = os.path.join(_HERE, "..", "functions", "fetcher", "personas.json")
_MODELS_PATH = os.path.join(_HERE, "..", "functions", "fetcher", "models.json")

# 用指定 profile 建 session（账号无关设计）
_session = boto3.Session(profile_name=PROFILE, region_name=REGION)
_dynamodb = _session.resource("dynamodb")
_drafts = _dynamodb.Table(DRAFTS_TABLE)
# Lambda 同步生成评论可能跑 1~2 分钟（长档双语），boto3 默认读超时只有 60s
# 会导致假性卡住 + 后台自动重试。放宽到 300s（与 Lambda 自身上限对齐）、关闭重试。
_lambda_cfg = Config(
    read_timeout=300,
    connect_timeout=15,
    retries={"max_attempts": 0},
)
_lambda = _session.client("lambda", config=_lambda_cfg)


# ── feeds.json 读写 ─────────────────────────────────────
def load_feeds_file() -> dict:
    with open(_FEEDS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_feeds_file(data: dict):
    with open(_FEEDS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def all_topics() -> list:
    data = load_feeds_file()
    topics = set()
    for feed in data.get("feeds", []):
        for t in feed.get("topics", []):
            topics.add(t)
    return sorted(topics)


# ── personas.json 读写 ──────────────────────────────────
def load_personas_file() -> dict:
    if not os.path.exists(_PERSONAS_PATH):
        return {"personas": []}
    with open(_PERSONAS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_personas_file(data: dict):
    with open(_PERSONAS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _slug(name: str) -> str:
    """从名字生成一个 id（无合适字符时用时间戳兜底）。"""
    import re
    import time as _t
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or f"persona_{int(_t.time())}"


# ── models.json 读取 ────────────────────────────────────
def load_models() -> list:
    if not os.path.exists(_MODELS_PATH):
        return []
    with open(_MODELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("models", [])


# ── Decimal 清洗（DynamoDB 取回的是 Decimal，JSON 不能直接序列化）──
def _clean(obj):
    from decimal import Decimal
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    return obj


def usage_summary() -> dict:
    """汇总所有已生成草稿的累计 token / 成本，以及按模型拆分。"""
    total = {"drafts": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    by_model = {}
    for it in list_drafts(status="PENDING_REVIEW"):
        if not it.get("model_id"):
            continue
        total["drafts"] += 1
        it_in = int(it.get("input_tokens", 0) or 0)
        it_out = int(it.get("output_tokens", 0) or 0)
        it_cost = float(it.get("cost_usd", 0) or 0)
        total["input_tokens"] += it_in
        total["output_tokens"] += it_out
        total["cost_usd"] += it_cost
        m = it["model_id"]
        bm = by_model.setdefault(m, {"drafts": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        bm["drafts"] += 1
        bm["input_tokens"] += it_in
        bm["output_tokens"] += it_out
        bm["cost_usd"] += it_cost
    total["cost_usd"] = round(total["cost_usd"], 6)
    for m in by_model:
        by_model[m]["cost_usd"] = round(by_model[m]["cost_usd"], 6)
    return {"total": total, "by_model": by_model}


# ── DynamoDB 草稿读写 ───────────────────────────────────
def list_drafts(status=None, topics=None) -> list:
    """全表扫描草稿（POC 数据量小，scan 足够）。可按状态和标签筛选。"""
    scan_kwargs = {}
    if status:
        scan_kwargs["FilterExpression"] = Attr("status").eq(status)
    items = []
    resp = _drafts.scan(**scan_kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = _drafts.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))

    # 若按标签筛选：用 feeds.json 把 author -> topics 映射出来，再过滤
    if topics:
        topic_set = set(topics)
        author_topics = {}
        for feed in load_feeds_file().get("feeds", []):
            author_topics[feed["name"]] = set(feed.get("topics", []))
        items = [
            it for it in items
            if author_topics.get(it.get("author", ""), set()) & topic_set
        ]

    # Decimal -> int，按时间倒序
    for it in items:
        if "created_at" in it:
            it["created_at"] = int(it["created_at"])
    items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return items


def update_status(draft_id: str, status: str):
    _drafts.update_item(
        Key={"id": draft_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status},
    )


# ── 手动触发抓取 / 单篇生成 ──────────────────────────────
def _invoke_fetcher(payload: dict) -> dict:
    resp = _lambda.invoke(
        FunctionName=FETCHER_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = resp["Payload"].read().decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"raw": raw}


def _mark_scanned(topics=None, feeds=None):
    """抓取成功后，给本地 feeds.json 里实际被抓的源写上 last_scanned 时间戳。
    feeds=按名字点选；topics=按标签；两者都空=全部源。
    注意：这是"本地上次抓取时间"，云端每周一定时任务不经过这里，不会更新它。"""
    now = int(time.time())
    data = load_feeds_file()
    name_set = set(feeds) if feeds else None
    topic_set = set(topics) if topics else None
    for feed in data.get("feeds", []):
        hit = False
        if name_set is not None:
            hit = feed.get("name") in name_set
        elif topic_set is not None:
            hit = bool(set(feed.get("topics", [])) & topic_set)
        else:
            hit = True  # 全部源
        if hit:
            feed["last_scanned"] = now
    save_feeds_file(data)


def trigger_fetch(topics=None, feeds=None, generate=False, persona=None, word_limit=None) -> dict:
    """同步调用 fetcher Lambda 抓取。默认只抓不生成（A 方案）。
    feeds=点选的信息源名字列表；topics=按标签；两者都空=全部/定时默认。"""
    payload = {}
    if feeds:
        payload["feeds"] = feeds
    elif topics:
        payload["topics"] = topics
    if generate:
        payload["generate"] = True
        if persona:
            payload["persona"] = persona
        if word_limit:
            payload["word_limit"] = int(word_limit)
    result = _invoke_fetcher(payload)
    # 抓取没报错才记录时间（Lambda 返回里没有 error 字段即视为成功）
    if not (isinstance(result, dict) and result.get("error")):
        try:
            _mark_scanned(topics=topics, feeds=feeds)
        except Exception:  # noqa: BLE001 —— 记录时间失败不影响抓取结果
            pass
    return result


def trigger_generate(draft_id: str, persona=None, word_limit=None, model_id=None) -> dict:
    """对一条已抓取(NEW)的帖子，用指定口吻+长度+模型生成评论。"""
    payload = {"action": "generate", "draft_id": draft_id}
    if persona:
        payload["persona"] = persona
    if word_limit:
        payload["word_limit"] = int(word_limit)
    if model_id:
        payload["model_id"] = model_id
    return _invoke_fetcher(payload)


# ── HTTP 处理 ───────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, *args):  # 静音默认日志
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                self._send_file(os.path.join(_HERE, "index.html"), "text/html; charset=utf-8")
            elif path == "/api/drafts":
                status = (qs.get("status") or [None])[0]
                topics = qs.get("topics")  # list 或 None
                self._send_json(_clean(list_drafts(status=status, topics=topics)))
            elif path == "/api/feeds":
                self._send_json(load_feeds_file().get("feeds", []))
            elif path == "/api/topics":
                self._send_json(all_topics())
            elif path == "/api/personas":
                self._send_json(load_personas_file().get("personas", []))
            elif path == "/api/models":
                self._send_json(load_models())
            elif path == "/api/stats":
                self._send_json(usage_summary())
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        try:
            if path == "/api/drafts/status":
                update_status(body["id"], body["status"])
                self._send_json({"ok": True})
            elif path == "/api/feeds":
                data = load_feeds_file()
                data.setdefault("feeds", []).append({
                    "name": body["name"],
                    "url": body["url"],
                    "type": body.get("type", "native_rss"),
                    "topics": body.get("topics", []),
                    "water_tier": "water" in body.get("topics", []),
                })
                save_feeds_file(data)
                self._send_json({"ok": True})
            elif path == "/api/fetch":
                topics = body.get("topics") or None
                feeds = body.get("feeds") or None
                generate = bool(body.get("generate", False))
                persona = body.get("persona") or None
                word_limit = body.get("word_limit") or None
                self._send_json(trigger_fetch(
                    topics=topics, feeds=feeds, generate=generate,
                    persona=persona, word_limit=word_limit))
            elif path == "/api/generate":
                self._send_json(trigger_generate(
                    body["draft_id"],
                    persona=body.get("persona") or None,
                    word_limit=body.get("word_limit") or None,
                    model_id=body.get("model_id") or None))
            elif path == "/api/personas":
                # 新增或编辑：有 id 则覆盖同 id，否则按 name 生成 id 新增
                data = load_personas_file()
                plist = data.setdefault("personas", [])
                pid = body.get("id") or _slug(body.get("name", ""))
                samples = [s for s in body.get("samples", []) if str(s).strip()]
                entry = {
                    "id": pid,
                    "name": body.get("name", pid),
                    "style": body.get("style", ""),
                    "samples": samples,
                }
                for i, p in enumerate(plist):
                    if p.get("id") == pid:
                        plist[i] = entry
                        break
                else:
                    plist.append(entry)
                save_personas_file(data)
                self._send_json({"ok": True, "id": pid})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        try:
            if path == "/api/personas":
                data = load_personas_file()
                pid = body.get("id")
                data["personas"] = [p for p in data.get("personas", []) if p.get("id") != pid]
                save_personas_file(data)
                self._send_json({"ok": True})
            else:  # /api/feeds
                data = load_feeds_file()
                name = body.get("name")
                data["feeds"] = [f for f in data.get("feeds", []) if f.get("name") != name]
                save_feeds_file(data)
                self._send_json({"ok": True})
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Alter 待审网页已启动： {url}")
    print("关闭这个窗口即停止。只在本机可访问，不对外公开。")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
