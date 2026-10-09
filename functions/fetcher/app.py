"""
Alter —— RSS 抓取 Lambda（云端版，POC 第二步）

职责：
  1. 读 feeds.json 里的新闻源（默认只抓 water_tier=true 的水务子集）
  2. 抓每个源的 RSS，取最新若干条
  3. 用去重表跳过处理过的条目（按 entry 链接/guid 去重）
  4. 对新条目调用 Bedrock 生成评论草稿，写进 alter-drafts 表（状态=待审）

触发方式：EventBridge 每天定时触发。绝不自动发布。

依赖：feedparser（见 requirements.txt）。只用标准库 + feedparser + boto3。
"""

import json
import os
import time
import uuid
import hashlib
import calendar

import boto3
import feedparser

# ── 环境变量（SAM 注入）────────────────────────────────
TABLE_NAME = os.environ["TABLE_NAME"]
SEEN_TABLE_NAME = os.environ["SEEN_TABLE_NAME"]
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")
COMMENT_TONE = os.environ.get("COMMENT_TONE", "专业、有见地、简洁")
COMMENT_MAX_WORDS = int(os.environ.get("COMMENT_MAX_WORDS", "80"))
# 每个源每次最多处理几条新条目（控制成本，避免一次灌太多）
MAX_ITEMS_PER_FEED = int(os.environ.get("MAX_ITEMS_PER_FEED", "3"))
# 只抓水务子集（先跑通）；设为 "false" 则抓全部 15 家
WATER_TIER_ONLY = os.environ.get("WATER_TIER_ONLY", "true").lower() == "true"
# 只处理过去 N 天内发布的条目（配合每周抓取；0 表示不限时间）
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
# 去重表的 TTL（秒），默认 90 天后自动清理旧记录
SEEN_TTL_SECONDS = int(os.environ.get("SEEN_TTL_SECONDS", str(90 * 24 * 3600)))

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(TABLE_NAME)
_seen = _dynamodb.Table(SEEN_TABLE_NAME)

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_feeds(topics=None, names=None) -> list:
    """加载源。
    - names 非空：只保留名字在列表里的源（网页"点选信息源"用，最精确）。
    - topics 非空：只保留含任一指定标签的源（按类扫描）。
    - 都为空：遵循 WATER_TIER_ONLY 开关（定时任务用）。"""
    with open(os.path.join(_HERE, "feeds.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    feeds = data.get("feeds", [])
    if names:
        name_set = set(names)
        feeds = [x for x in feeds if x.get("name") in name_set]
    elif topics:
        topic_set = set(topics)
        feeds = [x for x in feeds if set(x.get("topics", [])) & topic_set]
    elif WATER_TIER_ONLY:
        feeds = [x for x in feeds if x.get("water_tier")]
    return feeds


def entry_key(entry) -> str:
    """用 guid 优先，否则 link，否则标题 —— 作为去重键。"""
    raw = getattr(entry, "id", None) or getattr(entry, "link", None) or getattr(entry, "title", "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_recent(entry) -> bool:
    """条目是否在过去 LOOKBACK_DAYS 天内发布。
    拿不到发布时间时返回 True（宁可处理，不漏掉）。LOOKBACK_DAYS=0 表示不限。"""
    if LOOKBACK_DAYS <= 0:
        return True
    tm = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not tm:
        return True
    published_ts = calendar.timegm(tm)  # struct_time 视为 UTC
    return published_ts >= (time.time() - LOOKBACK_DAYS * 24 * 3600)


def already_seen(key: str) -> bool:
    resp = _seen.get_item(Key={"entry_key": key})
    return "Item" in resp


def mark_seen(key: str):
    _seen.put_item(Item={"entry_key": key, "ttl": int(time.time()) + SEEN_TTL_SECONDS})


def load_persona(persona_id=None) -> dict:
    """按 id 读取口吻档案；找不到或为 default 时返回 None（走通用口吻）。"""
    if not persona_id or persona_id == "default":
        return None
    path = os.path.join(_HERE, "personas.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for p in data.get("personas", []):
        if p.get("id") == persona_id:
            return p
    return None


def persona_block(persona: dict) -> str:
    """把口吻的风格说明 + 范文拼成一段 few-shot 示范，插入提示词。"""
    if not persona:
        return ""
    lines = [
        "\nIMPORTANT — Mimic the following author's VOICE and writing style "
        "(tone, sentence rhythm, vocabulary, how they open and close). "
        "Learn the STYLE only; do not copy their sentences or claim their opinions.",
        f"Style notes: {persona.get('style', '')}",
    ]
    samples = [s for s in persona.get("samples", []) if s.strip()]
    if samples:
        lines.append("Reference posts written by this author (style samples):")
        for i, s in enumerate(samples, 1):
            lines.append(f"[Sample {i}] {s}")
    return "\n".join(lines) + "\n"


def build_prompt(post: dict, persona: dict = None, word_limit: int = None) -> str:
    style_line = persona.get("style") if persona else COMMENT_TONE
    limit = word_limit or COMMENT_MAX_WORDS
    return (
        f"You are a practitioner with real, in-depth insight in the relevant field. "
        f"Write a social-media comment draft on the item below. "
        f"Style: {style_line}. Have a concrete point of view; no empty pleasantries; "
        f"no obvious AI tone.\n"
        f"{persona_block(persona)}"
        f"\nOutput EXACTLY two paragraphs, in this order:\n"
        f"1) First an ENGLISH paragraph (<= {limit} words).\n"
        f"2) Then a blank line, then a SIMPLIFIED CHINESE paragraph that conveys the "
        f"same point (not a word-for-word translation; natural Chinese, <= {limit} 字).\n"
        f"Do not add any labels, headers, or quotation marks around the paragraphs.\n\n"
        f"Title: {post['title']}\n"
        f"Summary: {post['summary']}\n"
        f"Source: {post['url']}\n\n"
        f"Comment draft:"
    )


def generate_draft(post: dict, persona: dict = None, word_limit: int = None) -> str:
    # 输出 token 上限随词数档位放宽：长档需要更多 token 才不被截断
    limit = word_limit or COMMENT_MAX_WORDS
    max_tokens = max(600, int(limit) * 6)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": build_prompt(post, persona, word_limit)}],
    }
    resp = _bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"].strip()


def save_draft(source_name: str, entry, draft: str, persona_name: str = "",
               status: str = "PENDING_REVIEW", topics=None) -> str:
    """存一条草稿。draft 为空串时表示"只抓未生成"（状态 NEW）。返回新 id。"""
    item_id = str(uuid.uuid4())
    _table.put_item(
        Item={
            "id": item_id,
            "created_at": int(time.time()),
            "author": source_name,
            "title": getattr(entry, "title", ""),
            "url": getattr(entry, "link", ""),
            "summary": getattr(entry, "summary", "")[:1000],
            "topics": topics or [],
            "draft": draft,
            "persona": persona_name,  # 本条草稿用的口吻（空=通用）
            "status": status,  # NEW=只抓未生成 / PENDING_REVIEW=已生成待审
        }
    )
    return item_id


def _entry_from_item(item: dict):
    """把 DynamoDB 里的一条 NEW 草稿还原成一个有 title/link 属性的轻量对象，
    复用 save_draft 的 getattr 取值方式。"""
    class _E:
        pass
    e = _E()
    e.title = item.get("title", "")
    e.link = item.get("url", "")
    e.summary = item.get("summary", "")
    return e


def handle_generate(event) -> dict:
    """单篇生成：读某条 NEW 草稿，用指定口吻+长度生成评论，原地更新为 PENDING_REVIEW。"""
    draft_id = event.get("draft_id")
    if not draft_id:
        return {"error": "missing draft_id"}
    persona = load_persona(event.get("persona"))
    persona_name = persona.get("name", "") if persona else ""
    word_limit = int(event.get("word_limit") or COMMENT_MAX_WORDS)

    resp = _table.get_item(Key={"id": draft_id})
    item = resp.get("Item")
    if not item:
        return {"error": f"draft {draft_id} not found"}

    post = {
        "author": item.get("author", ""),
        "title": item.get("title", ""),
        "summary": (item.get("summary") or "")[:1000],
        "url": item.get("url", ""),
    }
    draft = generate_draft(post, persona, word_limit)
    _table.update_item(
        Key={"id": draft_id},
        UpdateExpression="SET #d = :d, persona = :p, #s = :s, word_limit = :w, generated_at = :g",
        ExpressionAttributeNames={"#d": "draft", "#s": "status"},
        ExpressionAttributeValues={
            ":d": draft, ":p": persona_name, ":s": "PENDING_REVIEW",
            ":w": word_limit, ":g": int(time.time()),
        },
    )
    return {"ok": True, "id": draft_id, "persona": persona_name or "通用",
            "word_limit": word_limit, "draft": draft}


def lambda_handler(event, context):
    if not isinstance(event, dict):
        event = {}

    # 动作一：单篇生成评论（网页逐篇点"生成"时调用）
    if event.get("action") == "generate":
        result = handle_generate(event)
        print(json.dumps(result, ensure_ascii=False))
        return result

    # 动作二：抓取新闻。默认只抓不生成（存 status=NEW，draft 空），
    # 评论由网页逐篇按需生成，省 token。
    # 事件可带：topics(按标签) / feeds(按名字点选) / generate(True 时抓时即生成，兼容旧行为)
    topics = event.get("topics") or None
    names = event.get("feeds") or None
    generate_now = bool(event.get("generate", False))
    persona = load_persona(event.get("persona")) if generate_now else None
    persona_name = persona.get("name", "") if persona else ""
    word_limit = int(event.get("word_limit") or COMMENT_MAX_WORDS)

    feeds = load_feeds(topics=topics, names=names)
    stats = {"feeds": len(feeds), "new_items": 0, "new_drafts": 0, "skipped": 0,
             "mode": "generate" if generate_now else "fetch_only", "errors": []}

    for feed in feeds:
        name, url = feed["name"], feed["url"]
        feed_topics = feed.get("topics", [])
        try:
            parsed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{name}: parse error {type(e).__name__}: {e}")
            continue

        count = 0
        for entry in parsed.entries:
            if count >= MAX_ITEMS_PER_FEED:
                break
            key = entry_key(entry)
            if already_seen(key):
                stats["skipped"] += 1
                continue
            if not is_recent(entry):
                stats["skipped"] += 1
                mark_seen(key)
                continue

            try:
                if generate_now:
                    post = {
                        "author": name,
                        "title": getattr(entry, "title", ""),
                        "summary": getattr(entry, "summary", "")[:1000],
                        "url": getattr(entry, "link", ""),
                    }
                    draft = generate_draft(post, persona, word_limit)
                    save_draft(name, entry, draft, persona_name,
                               status="PENDING_REVIEW", topics=feed_topics)
                    stats["new_drafts"] += 1
                else:
                    # 只抓不生成：存一条空草稿等用户逐篇生成
                    save_draft(name, entry, "", "", status="NEW", topics=feed_topics)
                    stats["new_items"] += 1
                mark_seen(key)
                count += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"{name}: error {type(e).__name__}: {e}")

    print(json.dumps(stats, ensure_ascii=False))
    return stats
