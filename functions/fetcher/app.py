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
# 去重表的 TTL（秒），默认 90 天后自动清理旧记录
SEEN_TTL_SECONDS = int(os.environ.get("SEEN_TTL_SECONDS", str(90 * 24 * 3600)))

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(TABLE_NAME)
_seen = _dynamodb.Table(SEEN_TABLE_NAME)

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_feeds() -> list:
    with open(os.path.join(_HERE, "feeds.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    feeds = data.get("feeds", [])
    if WATER_TIER_ONLY:
        feeds = [x for x in feeds if x.get("water_tier")]
    return feeds


def entry_key(entry) -> str:
    """用 guid 优先，否则 link，否则标题 —— 作为去重键。"""
    raw = getattr(entry, "id", None) or getattr(entry, "link", None) or getattr(entry, "title", "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def already_seen(key: str) -> bool:
    resp = _seen.get_item(Key={"entry_key": key})
    return "Item" in resp


def mark_seen(key: str):
    _seen.put_item(Item={"entry_key": key, "ttl": int(time.time()) + SEEN_TTL_SECONDS})


def build_prompt(post: dict) -> str:
    return (
        f"你是一个在相关领域有深度见解的从业者。请针对下面这条动态，"
        f"写一段{COMMENT_TONE}的评论草稿，不超过 {COMMENT_MAX_WORDS} 字。"
        f"要有具体观点，不要空泛客套，不要用明显的 AI 腔。\n\n"
        f"标题：{post['title']}\n"
        f"摘要：{post['summary']}\n"
        f"来源：{post['url']}\n\n"
        f"评论草稿："
    )


def generate_draft(post: dict) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": build_prompt(post)}],
    }
    resp = _bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"].strip()


def save_draft(source_name: str, entry, draft: str):
    _table.put_item(
        Item={
            "id": str(uuid.uuid4()),
            "created_at": int(time.time()),
            "author": source_name,
            "title": getattr(entry, "title", ""),
            "url": getattr(entry, "link", ""),
            "draft": draft,
            "status": "PENDING_REVIEW",  # 待真人审核 —— 永不自动发布
        }
    )


def lambda_handler(event, context):
    feeds = load_feeds()
    stats = {"feeds": len(feeds), "new_drafts": 0, "skipped": 0, "errors": []}

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

            post = {
                "author": name,
                "title": getattr(entry, "title", ""),
                "summary": getattr(entry, "summary", "")[:1000],
                "url": getattr(entry, "link", ""),
            }
            try:
                draft = generate_draft(post)
                save_draft(name, entry, draft)
                mark_seen(key)
                stats["new_drafts"] += 1
                count += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"{name}: draft error {type(e).__name__}: {e}")

    print(json.dumps(stats, ensure_ascii=False))
    return stats
