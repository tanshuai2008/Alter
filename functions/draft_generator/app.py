"""
Alter —— 草稿生成 Lambda（云端版）

职责（最小闭环）：
  1. 接收一条内容（author/title/summary/url）
  2. 调用 Amazon Bedrock（Claude Haiku）生成评论草稿
  3. 把「原文 + 草稿 + 状态=待审」写进 DynamoDB

触发方式（POC 第一步）：手动调用 / 测试事件。
后续（第二步）：由 EventBridge 定时触发 + RSS 抓取 Lambda 喂数据。
"""

import json
import os
import time
import uuid

import boto3

# ── 环境变量（由 SAM template.yaml 注入，换账号/区域/模型都不改代码）──
TABLE_NAME = os.environ["TABLE_NAME"]
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")
COMMENT_TONE = os.environ.get("COMMENT_TONE", "专业、有见地、简洁")
COMMENT_MAX_WORDS = int(os.environ.get("COMMENT_MAX_WORDS", "80"))

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# POC 阶段若事件里没带内容，用这条示例，保证手动触发就能看到效果
SAMPLE_POST = {
    "author": "某水务科技公司",
    "title": "新一代膜过滤技术将能耗降低 30%",
    "summary": (
        "该公司发布了基于新型陶瓷膜材料的过滤系统，在同等处理量下"
        "将运行能耗较传统反渗透方案降低约 30%，并延长了膜组件寿命。"
    ),
    "url": "https://example.com/news/membrane-30pct",
}


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


def lambda_handler(event, context):
    # 事件里可带一条 post；没带就用示例
    post = event.get("post") if isinstance(event, dict) else None
    if not post:
        post = SAMPLE_POST

    draft = generate_draft(post)

    item = {
        "id": str(uuid.uuid4()),
        "created_at": int(time.time()),
        "author": post.get("author", ""),
        "title": post.get("title", ""),
        "url": post.get("url", ""),
        "draft": draft,
        "status": "PENDING_REVIEW",  # 待真人审核 —— 永不自动发布
    }
    _table.put_item(Item=item)

    result = {
        "message": "草稿已生成并存入 DynamoDB，状态=待审核（不会自动发布）",
        "id": item["id"],
        "title": item["title"],
        "draft": draft,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result
