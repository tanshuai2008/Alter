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


def load_feeds(topics=None) -> list:
    """加载源。topics 非空时只保留含任一指定标签的源；
    否则遵循 WATER_TIER_ONLY 开关（定时任务用）。"""
    with open(os.path.join(_HERE, "feeds.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    feeds = data.get("feeds", [])
    if topics:
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


def build_prompt(post: dict, persona: dict = None) -> str:
    style_line = persona.get("style") if persona else COMMENT_TONE
    return (
        f"You are a practitioner with real, in-depth insight in the relevant field. "
        f"Write a social-media comment draft on the item below. "
        f"Style: {style_line}. Have a concrete point of view; no empty pleasantries; "
        f"no obvious AI tone.\n"
        f"{persona_block(persona)}"
        f"\nOutput EXACTLY two paragraphs, in this order:\n"
        f"1) First an ENGLISH paragraph (<= {COMMENT_MAX_WORDS} words).\n"
        f"2) Then a blank line, then a SIMPLIFIED CHINESE paragraph that conveys the "
        f"same point (not a word-for-word translation; natural Chinese, <= {COMMENT_MAX_WORDS} 字).\n"
        f"Do not add any labels, headers, or quotation marks around the paragraphs.\n\n"
        f"Title: {post['title']}\n"
        f"Summary: {post['summary']}\n"
        f"Source: {post['url']}\n\n"
        f"Comment draft:"
    )


def generate_draft(post: dict, persona: dict = None) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
        "messages": [{"role": "user", "content": build_prompt(post, persona)}],
    }
    resp = _bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"].strip()


def save_draft(source_name: str, entry, draft: str, persona_name: str = ""):
    _table.put_item(
        Item={
            "id": str(uuid.uuid4()),
            "created_at": int(time.time()),
            "author": source_name,
            "title": getattr(entry, "title", ""),
            "url": getattr(entry, "link", ""),
            "draft": draft,
            "persona": persona_name,  # 本条草稿用的口吻（空=通用）
            "status": "PENDING_REVIEW",  # 待真人审核 —— 永不自动发布
        }
    )


def lambda_handler(event, context):
    # 事件可带 topics 列表（按标签只抓某些源）和 persona（口吻 id）
    topics = None
    persona_id = None
    if isinstance(event, dict):
        topics = event.get("topics") or None
        persona_id = event.get("persona") or None
    persona = load_persona(persona_id)
    persona_name = persona.get("name", "") if persona else ""
    feeds = load_feeds(topics=topics)
    stats = {"feeds": len(feeds), "new_drafts": 0, "skipped": 0,
             "persona": persona_name or "通用", "errors": []}

    for feed in feeds:
        name, url = feed["name"], feed["url"]
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
                # 旧条目也标记为已见，避免下次重复判断
                mark_seen(key)
                continue

            post = {
                "author": name,
                "title": getattr(entry, "title", ""),
                "summary": getattr(entry, "summary", "")[:1000],
                "url": getattr(entry, "link", ""),
            }
            try:
                draft = generate_draft(post, persona)
                save_draft(name, entry, draft, persona_name)
                mark_seen(key)
                stats["new_drafts"] += 1
                count += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"{name}: draft error {type(e).__name__}: {e}")

    print(json.dumps(stats, ensure_ascii=False))
    return stats
