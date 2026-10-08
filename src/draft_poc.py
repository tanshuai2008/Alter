"""
Alter POC —— 最小可跑示例：一条内容 → 生成评论草稿

两种运行模式：
  1) 本地 mock 模式（默认）：不连 AWS、不花钱，用规则模板生成草稿，
     用来验证整条流程跑得通。
  2) Bedrock 模式：加 --bedrock 参数，真正调用 Amazon Bedrock 生成草稿，
     需要先配好 AWS 账号（见 config.example.env）。

运行方法（在 Alter 目录下）：
  python src/draft_poc.py              # 本地 mock，马上能看到效果
  python src/draft_poc.py --bedrock    # 调用真实 Bedrock（需配好账号）
"""

import argparse
import json
import sys

# ── 一条示例内容（真实版里由 RSS 抓取填充）──────────────
SAMPLE_POST = {
    "author": "某水务科技公司",
    "title": "新一代膜过滤技术将能耗降低 30%",
    "summary": (
        "该公司发布了基于新型陶瓷膜材料的过滤系统，在同等处理量下"
        "将运行能耗较传统反渗透方案降低约 30%，并延长了膜组件寿命。"
    ),
    "url": "https://example.com/news/membrane-30pct",
}


def build_prompt(post: dict, tone: str, max_words: int) -> str:
    """把一条内容拼成给大模型的提示。"""
    return (
        f"你是一个在相关领域有深度见解的从业者。请针对下面这条动态，"
        f"写一段{tone}的评论草稿，不超过 {max_words} 字。"
        f"要有具体观点，不要空泛客套，不要用明显的 AI 腔。\n\n"
        f"标题：{post['title']}\n"
        f"摘要：{post['summary']}\n"
        f"来源：{post['url']}\n\n"
        f"评论草稿："
    )


def generate_mock(prompt: str) -> str:
    """本地模式：不调模型，返回一个占位草稿，用于验证流程。"""
    return (
        "能耗降低 30% 若在规模化部署中稳定复现，意义不止于成本——它会直接"
        "影响偏远地区分布式水处理的可行性。真正值得追的是陶瓷膜的全生命周期"
        "成本和抗污染表现，而不只是单点能耗数字。期待看到第三方中试数据。"
        "\n\n【本地 mock 草稿｜加 --bedrock 可调用真实模型】"
    )


def generate_bedrock(prompt: str, model_id: str, region: str) -> str:
    """Bedrock 模式：真正调用 Amazon Bedrock。需要配好 AWS 账号。"""
    import boto3  # 仅在该模式下才需要安装

    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = client.invoke_model(modelId=model_id, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"].strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Alter 草稿生成 POC")
    parser.add_argument("--bedrock", action="store_true", help="调用真实 Bedrock")
    parser.add_argument("--model", default="anthropic.claude-3-haiku-20240307-v1:0")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--tone", default="专业、有见地、简洁")
    parser.add_argument("--max-words", type=int, default=80)
    args = parser.parse_args()

    post = SAMPLE_POST
    prompt = build_prompt(post, args.tone, args.max_words)

    print("=" * 60)
    print(f"原文：{post['title']}")
    print(f"来源：{post['url']}")
    print("-" * 60)

    if args.bedrock:
        try:
            draft = generate_bedrock(prompt, args.model, args.region)
        except Exception as e:  # noqa: BLE001
            print(f"[Bedrock 调用失败] {e}", file=sys.stderr)
            print("提示：检查 AWS 账号是否配好、Bedrock 模型是否已申请访问。")
            sys.exit(1)
    else:
        draft = generate_mock(prompt)

    print("生成的评论草稿：\n")
    print(draft)
    print("=" * 60)
    print("\n下一步：你阅读/编辑后，手动复制到 LinkedIn 发布。")


if __name__ == "__main__":
    main()
