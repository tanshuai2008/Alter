# Alter — 社交内容助手（AWS 练习 Side Project）

> 个人练习项目，**不盈利**，核心目的：熟悉 AWS（重点 Amazon Bedrock）。
> POC 阶段只针对 **LinkedIn**，暂不接入 X / Twitter。

---

## 这个 App 做什么

自动追踪我重点关注的人 / 公司 / community 的最新动态，用大模型（Amazon Bedrock）
生成一段**有见地的评论草稿**，推送给我审阅。**最终发布完全由我手动完成**
（复制粘贴到 LinkedIn），App 本身不自动发帖。

一句话：**AI 帮我读 + 起草，人来定稿 + 发布。**

---

## 为什么这样设计（合规说明）

| 决策 | 原因 |
|---|---|
| **不自动发帖** | LinkedIn / X 都禁止未经披露的自动化发布，属封号高风险行为。 |
| **不用 LinkedIn 自动化 API** | LinkedIn 不向个人开发者开放自动发帖/评论 API；模拟登录、爬取均违反其用户协议。 |
| **内容靠合规途径获取** | POC 用公开 RSS / 新闻源，或我手动贴链接；不做自动爬取 LinkedIn。 |
| **人类终审** | AI 只产出草稿，我读过、认可后才手动发布。规避"inauthentic behavior"风险。 |
| **用个人 AWS 账号** | 这是个人 side project，用个人账号（非公司内部 burner/developer）。 |

> ⚠️ **待确认**：是否可用公司设备/网络开发个人项目，属公司 AUP（可接受使用政策）范畴，
> 需向 IT / 经理确认，不属技术问题。

---

## 架构（人类终审版）

```
数据源                    AWS 云端（练习主场）                  我来终审
┌──────────┐      ┌───────────────────────────────┐      ┌──────────────┐
│ 公开 RSS  │──┐   │ EventBridge（定时触发）           │      │ 待审列表      │
│ 新闻源    │  ├──▶│   └▶ Lambda: 抓取内容             │      │（邮件/网页）  │
│ 手动贴链接 │  │   │        └▶ DynamoDB（内容+草稿+状态）│──▶  │   └▶ 我阅读/编辑│
└──────────┘  │   │             └▶ Lambda: 生成草稿    │      │        └▶ 手动 │
              │   │                  └▶ Bedrock(Claude) │      │         复制粘贴│
              │   │ Secrets Manager（存 API Key）        │      │         发到    │
              │   └───────────────────────────────┘      │       LinkedIn │
              └──────────────────────────────────────────┴──────────────┘
```

---

## MVP 范围（POC 第一阶段）

**先在本地跑通整条逻辑，暂不连 AWS、不花钱。**

1. 配置关注对象清单（RSS 源 / 手动链接）
2. 抓取最新内容
3. 去重（只处理没见过的）
4. 调用大模型生成评论草稿
5. 把「原文 + 草稿」展示给我审阅
6. 我手动复制草稿去发布

验证 OK 后，再逐步搬到 AWS（Lambda + DynamoDB + EventBridge + Bedrock）。

---

## 技术选型

| 用途 | 选型 | 说明 |
|---|---|---|
| 大模型 | Amazon Bedrock（Claude Haiku） | 核心练习目标；Haiku 便宜 |
| 定时 | EventBridge Scheduler | 无服务器定时 |
| 计算 | AWS Lambda | 练 serverless |
| 存储 | DynamoDB | 免费额度大 |
| 密钥 | Secrets Manager / Parameter Store | 练安全最佳实践 |
| 审核界面(MVP) | 先用邮件，后期可换 S3 静态网页 | 先跑通 |
| 部署 | AWS SAM 或 CDK | 练基础设施即代码(IaC) |

---

## 账号配置（账号无关设计）

项目用 `AWS_PROFILE` 区分账号，**切换账号只改配置、不改代码**。
见 `config.example.env`。个人账号与公司内部账号的凭证获取方式不同，
但对本项目只是"换一个 profile 名"。

---

## 目录结构

```
Alter/
├── README.md              # 本文件
├── config.example.env     # 配置模板（账号无关）
├── docs/
│   └── compliance.md      # 合规风险详细说明
└── src/
    └── draft_poc.py        # 本地可跑的最小示例：内容 → 生成草稿
```
