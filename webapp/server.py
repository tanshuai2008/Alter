"""
Alter â€”â€” æœ¬åœ°å¾…å®¡ç½‘é¡µåŽç«¯ï¼ˆè·¯çº¿ Aï¼‰

ä¸€ä¸ªé›¶é¢å¤–ä¾èµ–çš„æœ¬åœ°æœåŠ¡å™¨ï¼ˆPython æ ‡å‡†åº“ + boto3ï¼‰ï¼Œè´Ÿè´£ï¼š
  1. æ‰˜ç®¡ç½‘é¡µç•Œé¢ï¼ˆindex.htmlï¼‰
  2. æä¾› JSON APIï¼š
     - GET  /api/drafts            åˆ—å‡ºè‰ç¨¿ï¼ˆå¯æŒ‰ ?status= / ?topics= ç­›é€‰ï¼‰
     - POST /api/drafts/status     æ›´æ–°ä¸€æ¡è‰ç¨¿çš„çŠ¶æ€ï¼ˆå·²å‘/å¿½ç•¥/å¾…å®¡ï¼‰
     - GET  /api/feeds             åˆ—å‡ºå…³æ³¨å¯¹è±¡ï¼ˆå«æ ‡ç­¾ï¼‰
     - POST /api/feeds             æ–°å¢žä¸€ä¸ªå…³æ³¨å¯¹è±¡
     - DELETE /api/feeds           åˆ é™¤ä¸€ä¸ªå…³æ³¨å¯¹è±¡
     - GET  /api/topics            åˆ—å‡ºæ‰€æœ‰å·²ç”¨è¿‡çš„æ ‡ç­¾
     - POST /api/fetch             æ‰‹åŠ¨è§¦å‘æŠ“å–ï¼ˆå¯é€‰ ?topics= åªæŠ“æŸäº›æ ‡ç­¾ï¼‰

å®‰å…¨ï¼šåªç»‘å®š 127.0.0.1ï¼ˆæœ¬æœºï¼‰ï¼Œä¸å¯¹å…¬ç½‘æš´éœ²ã€‚å…³ç”µè„‘å³åœã€‚
ç”¨æ³•ï¼šåœ¨ Alter ç›®å½•å¼€ PowerShellï¼Œè¿è¡Œ run.ps1ï¼Œæµè§ˆå™¨è‡ªåŠ¨å¼€ http://127.0.0.1:8765
"""

import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config

# â”€â”€ é…ç½®ï¼ˆä¸Žé¡¹ç›®å…¶ä½™éƒ¨åˆ†ä¸€è‡´ï¼‰â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
REGION = os.environ.get("AWS_REGION", "us-east-1")
PROFILE = os.environ.get("ALTER_PROFILE", "alter-dev")
DRAFTS_TABLE = os.environ.get("TABLE_NAME", "alter-drafts")
FETCHER_FUNCTION = os.environ.get("FETCHER_FUNCTION", "alter-fetcher")
PORT = int(os.environ.get("ALTER_WEB_PORT", "8765"))

_HERE = os.path.dirname(os.path.abspath(__file__))
# feeds.json ç”± fetcher å’Œç½‘é¡µå…±äº«ï¼Œæ”¾åœ¨ functions/fetcher ä¸‹
_FEEDS_PATH = os.path.join(_HERE, "..", "functions", "fetcher", "feeds.json")
_PERSONAS_PATH = os.path.join(_HERE, "..", "functions", "fetcher", "personas.json")
_MODELS_PATH = os.path.join(_HERE, "..", "functions", "fetcher", "models.json")

# ç”¨æŒ‡å®š profile å»º sessionï¼ˆè´¦å·æ— å…³è®¾è®¡ï¼‰
_session = boto3.Session(profile_name=PROFILE, region_name=REGION)
_dynamodb = _session.resource("dynamodb")
_drafts = _dynamodb.Table(DRAFTS_TABLE)
# Lambda åŒæ­¥ç”Ÿæˆè¯„è®ºå¯èƒ½è·‘ 1~2 åˆ†é’Ÿï¼ˆé•¿æ¡£åŒè¯­ï¼‰ï¼Œboto3 é»˜è®¤è¯»è¶…æ—¶åªæœ‰ 60s
# ä¼šå¯¼è‡´å‡æ€§å¡ä½ + åŽå°è‡ªåŠ¨é‡è¯•ã€‚æ”¾å®½åˆ° 300sï¼ˆä¸Ž Lambda è‡ªèº«ä¸Šé™å¯¹é½ï¼‰ã€å…³é—­é‡è¯•ã€‚
_lambda_cfg = Config(
    read_timeout=300,
    connect_timeout=15,
    retries={"max_attempts": 0},
)
_lambda = _session.client("lambda", config=_lambda_cfg)


# â”€â”€ feeds.json è¯»å†™ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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


# â”€â”€ personas.json è¯»å†™ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def load_personas_file() -> dict:
    if not os.path.exists(_PERSONAS_PATH):
        return {"personas": []}
    with open(_PERSONAS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_personas_file(data: dict):
    with open(_PERSONAS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _slug(name: str) -> str:
    """ä»Žåå­—ç”Ÿæˆä¸€ä¸ª idï¼ˆæ— åˆé€‚å­—ç¬¦æ—¶ç”¨æ—¶é—´æˆ³å…œåº•ï¼‰ã€‚"""
    import re
    import time as _t
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or f"persona_{int(_t.time())}"


# â”€â”€ models.json è¯»å– â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def load_models() -> list:
    if not os.path.exists(_MODELS_PATH):
        return []
    with open(_MODELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("models", [])


# â”€â”€ Decimal æ¸…æ´—ï¼ˆDynamoDB å–å›žçš„æ˜¯ Decimalï¼ŒJSON ä¸èƒ½ç›´æŽ¥åºåˆ—åŒ–ï¼‰â”€â”€
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
    """æ±‡æ€»æ‰€æœ‰å·²ç”Ÿæˆè‰ç¨¿çš„ç´¯è®¡ token / æˆæœ¬ï¼Œä»¥åŠæŒ‰æ¨¡åž‹æ‹†åˆ†ã€‚"""
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


# â”€â”€ DynamoDB è‰ç¨¿è¯»å†™ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def list_drafts(status=None, topics=None) -> list:
    """å…¨è¡¨æ‰«æè‰ç¨¿ï¼ˆPOC æ•°æ®é‡å°ï¼Œscan è¶³å¤Ÿï¼‰ã€‚å¯æŒ‰çŠ¶æ€å’Œæ ‡ç­¾ç­›é€‰ã€‚"""
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

    # è‹¥æŒ‰æ ‡ç­¾ç­›é€‰ï¼šç”¨ feeds.json æŠŠ author -> topics æ˜ å°„å‡ºæ¥ï¼Œå†è¿‡æ»¤
    if topics:
        topic_set = set(topics)
        author_topics = {}
        for feed in load_feeds_file().get("feeds", []):
            author_topics[feed["name"]] = set(feed.get("topics", []))
        items = [
            it for it in items
            if author_topics.get(it.get("author", ""), set()) & topic_set
        ]

    # Decimal -> intï¼ŒæŒ‰æ—¶é—´å€’åº
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


# â”€â”€ æ‰‹åŠ¨è§¦å‘æŠ“å– / å•ç¯‡ç”Ÿæˆ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    """æŠ“å–æˆåŠŸåŽï¼Œç»™æœ¬åœ° feeds.json é‡Œå®žé™…è¢«æŠ“çš„æºå†™ä¸Š last_scanned æ—¶é—´æˆ³ã€‚
    feeds=æŒ‰åå­—ç‚¹é€‰ï¼›topics=æŒ‰æ ‡ç­¾ï¼›ä¸¤è€…éƒ½ç©º=å…¨éƒ¨æºã€‚
    æ³¨æ„ï¼šè¿™æ˜¯"æœ¬åœ°ä¸Šæ¬¡æŠ“å–æ—¶é—´"ï¼Œäº‘ç«¯æ¯å‘¨ä¸€å®šæ—¶ä»»åŠ¡ä¸ç»è¿‡è¿™é‡Œï¼Œä¸ä¼šæ›´æ–°å®ƒã€‚"""
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
            hit = True  # å…¨éƒ¨æº
        if hit:
            feed["last_scanned"] = now
    save_feeds_file(data)


def trigger_fetch(topics=None, feeds=None, generate=False, persona=None, word_limit=None) -> dict:
    """åŒæ­¥è°ƒç”¨ fetcher Lambda æŠ“å–ã€‚é»˜è®¤åªæŠ“ä¸ç”Ÿæˆï¼ˆA æ–¹æ¡ˆï¼‰ã€‚
    feeds=ç‚¹é€‰çš„ä¿¡æ¯æºåå­—åˆ—è¡¨ï¼›topics=æŒ‰æ ‡ç­¾ï¼›ä¸¤è€…éƒ½ç©º=å…¨éƒ¨/å®šæ—¶é»˜è®¤ã€‚"""
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
    # æŠ“å–æ²¡æŠ¥é”™æ‰è®°å½•æ—¶é—´ï¼ˆLambda è¿”å›žé‡Œæ²¡æœ‰ error å­—æ®µå³è§†ä¸ºæˆåŠŸï¼‰
    if not (isinstance(result, dict) and result.get("error")):
        try:
            _mark_scanned(topics=topics, feeds=feeds)
        except Exception:  # noqa: BLE001 â€”â€” è®°å½•æ—¶é—´å¤±è´¥ä¸å½±å“æŠ“å–ç»“æžœ
            pass
    return result


def trigger_generate(draft_id: str, persona=None, word_limit=None, model_id=None) -> dict:
    """å¯¹ä¸€æ¡å·²æŠ“å–(NEW)çš„å¸–å­ï¼Œç”¨æŒ‡å®šå£å»+é•¿åº¦+æ¨¡åž‹ç”Ÿæˆè¯„è®ºã€‚"""
    payload = {"action": "generate", "draft_id": draft_id}
    if persona:
        payload["persona"] = persona
    if word_limit:
        payload["word_limit"] = int(word_limit)
    if model_id:
        payload["model_id"] = model_id
    return _invoke_fetcher(payload)


# â”€â”€ HTTP å¤„ç† â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    def log_message(self, *args):  # é™éŸ³é»˜è®¤æ—¥å¿—
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
                topics = qs.get("topics")  # list æˆ– None
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
                # æ–°å¢žæˆ–ç¼–è¾‘ï¼šæœ‰ id åˆ™è¦†ç›–åŒ idï¼Œå¦åˆ™æŒ‰ name ç”Ÿæˆ id æ–°å¢ž
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
    print(f"Alter å¾…å®¡ç½‘é¡µå·²å¯åŠ¨ï¼š {url}")
    print("å…³é—­è¿™ä¸ªçª—å£å³åœæ­¢ã€‚åªåœ¨æœ¬æœºå¯è®¿é—®ï¼Œä¸å¯¹å¤–å…¬å¼€ã€‚")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nå·²åœæ­¢ã€‚")


if __name__ == "__main__":
    main()

