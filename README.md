# Alter — AI 社交评论助手（个人 AWS 练习项目）

> 个人 side project，**不盈利**。主要目的是练 AWS（Bedrock、Lambda、DynamoDB、EventBridge、SAM）。
> 这份 README 是重建说明：在一台新电脑上照着做，就能从零把项目搭回来、接上现有的 AWS 部署。

---

## 1. 产品用途

**一句话：AI 帮我读新闻、起草评论，我来挑、改、手动发。**

我在 LinkedIn 上关注了一批公司、媒体和行业组织（水务、可持续、科技、AI、能源）。Alter 做这些事：

1. **抓新闻**：每周一自动抓取这些关注对象在 **LinkedIn 以外**的公开新闻源（自家 RSS，没有就用 Google News RSS），只保留过去 7 天的内容。
2. **待生成列表**：抓到的新闻先存起来，**不自动写评论**（省 token）。
3. **逐篇生成**：我在本地网页上挑一篇，选 **口吻（Voice）+ 长度 + 模型**，点生成，Bedrock 写出一段 **英文 + 中文** 的评论草稿。
4. **人工终审**：我复制草稿，自己去 LinkedIn 发，然后在网页上标记"已发"或"忽略"。

用途：练英文写作、积累行业观点、提升行业影响力。口吻（persona）功能让 AI 模仿几种高管写作风格（few-shot，**不是 fine-tune**）。

### 设计原则（合规）

| 原则 | 原因 |
|---|---|
| 永不自动发帖 | LinkedIn 禁止未披露的自动化发布 |
| 不爬 LinkedIn、不用 LinkedIn 自动化 API | 违反其用户协议 |
| 只用公开 RSS / Google News RSS | 合规、免费 |
| 人类终审 | AI 只出草稿 |
| 新闻发布日期以原文正文为准 | RSS/搜索引擎给的时间戳常常不准 |

---

## 2. 架构

```
 ┌────────────── AWS 个人账号 (us-east-1) ─────────────────────────┐
 │                                                                  │
 │  EventBridge 定时器 ──每周一 UTC 18:00──▶ Lambda: alter-fetcher   │
 │                                            │  读 feeds.json        │
 │                                            │  抓 RSS → 过滤近7天    │
 │                                            │  去重 (alter-seen-entries) │
 │                                            ▼                      │
 │                                      DynamoDB: alter-drafts        │
 │                                      status = NEW（待生成）         │
 │                                            ▲                      │
 │          action=generate（逐篇生成）        │                      │
 │  Lambda: alter-fetcher ──▶ Bedrock (Claude) ┘ status = PENDING_REVIEW │
 │                                                                  │
 └───────────────────────────▲──────────────────────────────────────┘
                             │ boto3（本机 AWS profile）
 ┌───────────── 本地电脑 ─────┴───────────────┐
 │ webapp/server.py  (Python 标准库 HTTP 服务) │
 │ webapp/index.html (单文件，原生 JS)         │
 │ 浏览器打开 http://127.0.0.1:8765（只本机可访问）│
 └─────────────────────────────────────────────┘
```

### AWS 资源（SAM 栈名 `alter-poc`）

| 资源 | 名称 | 作用 |
|---|---|---|
| Lambda | `alter-fetcher` | 主力：抓取模式 + 单篇生成模式（`action=generate`） |
| Lambda | `alter-draft-generator` | 早期 POC 的单次生成函数，保留 |
| DynamoDB | `alter-drafts` | 新闻条目 + 草稿 + 状态 + token/成本 |
| DynamoDB | `alter-seen-entries` | 去重，TTL 90 天自动清理 |
| EventBridge | `cron(0 18 ? * MON *)` | 每周一 UTC 18:00（冬令时太平洋 10am，夏令时 11am） |
| IAM Role | `alter-poc-*Role` | SAM 自动创建，Lambda 执行角色 |
| S3 | SAM 自动建的部署桶 | 存放打包代码 |

### `alter-drafts` 表字段

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `status` | `NEW`（待生成）/ `PENDING_REVIEW`（已生成待审）/ `POSTED` / `IGNORED` |
| `author` / `title` / `url` / `summary` / `topics` | 新闻来源、标题、原文链接、清洗后的摘要、标签 |
| `draft` / `persona` | 评论草稿、所用口吻 |
| `model_id` / `input_tokens` / `output_tokens` / `cost_usd` | 模型和花费记录 |
| `created_at` / `generated_at` | Unix 时间戳 |

### Lambda `alter-fetcher` 的调用参数

```jsonc
// 抓取（默认只抓不生成）
{ "feeds": ["TechCrunch", "CRN"], "topics": ["water"] }   // 两者都可省略 = 全部
// 单篇生成
{ "action": "generate", "draft_id": "...", "persona": "director",
  "word_limit": 100, "model_id": "us.anthropic.claude-sonnet-4-5-..." }
```

---

## 3. 目录结构

```
Alter/
├── README.md                     # 本文件
├── template.yaml                 # SAM 模板：所有 AWS 资源定义
├── samconfig.toml                # SAM 部署默认参数（profile、区域、定时表达式）
├── config.example.env            # 早期本地 POC 的配置模板
├── iam/alter-dev-policy.json     # 开发用 IAM 子用户的最小权限策略
├── docs/news_sources.md          # 17 个信息源清单（人看）
├── functions/
│   ├── fetcher/                  # 主 Lambda
│   │   ├── app.py
│   │   ├── feeds.json            # 信息源配置（代码读）
│   │   ├── personas.json         # 口吻：风格说明 + 范文
│   │   ├── models.json           # 可选 Bedrock 模型 + 单价
│   │   └── requirements.txt      # feedparser
│   └── draft_generator/          # 早期 POC Lambda
├── webapp/
│   ├── server.py                 # 本地 API + 托管网页
│   ├── index.html                # 待审网页界面（全英文）
│   ├── run.ps1 / start_webapp.bat
└── src/draft_poc.py              # 最早的本地 mock 示例
```

### 三个配置文件

**`feeds.json`**：每个信息源一条。
```json
{ "name": "TechCrunch", "url": "https://techcrunch.com/feed/",
  "topics": ["tech"], "water_tier": false }
```
`last_scanned` 字段由本地网页抓取后自动写入（云端定时抓取不会更新它）。

**`personas.json`**：口吻列表。`default` 放第一个，其余按职位高低排。
```json
{ "id": "director", "name": "Director",
  "style": "风格说明（给 AI 看的提示）",
  "samples": ["范文1全文", "范文2全文"] }
```

**`models.json`**：网页里的 Model 下拉。单价单位是每百万 token 美元，用来估算成本。
```json
{ "id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "name": "Claude Haiku 4.5",
  "price_in": 1, "price_out": 5, "default": true }
```

### 本地网页功能

- 两个视图：**To Generate**（NEW）和 **Generated · Pending**（PENDING_REVIEW）
- **Select Sources**：勾选要抓的信息源，显示标签和 Last scanned 时间
- **Fetch**：手动抓取（只抓不生成）
- 生成面板：Voice / Length（Concise 50 · Medium 100 · Long 180 词）/ Model
- 草稿操作：复制、标记已发、忽略、换口吻重新生成
- **Manage Voices** / **Manage Sources**：增删口吻和信息源
- 右上角 💰：累计 token 和估算花费

---

## 4. 在新电脑上从零搭建

> 以下命令都在 **PowerShell** 里运行（Windows）。Mac 用户把路径换成 Mac 格式即可。

### 4.1 安装工具

| 工具 | 用途 | 下载 |
|---|---|---|
| Git | 版本管理 | https://git-scm.com/download/win |
| Python 3.13 | Lambda 运行时 + 本地网页 | https://www.python.org/downloads/ （安装时勾选 "Add python.exe to PATH"） |
| AWS CLI v2 | 连 AWS | https://awscli.amazonaws.com/AWSCLIV2.msi |
| AWS SAM CLI | 构建和部署 | https://github.com/aws/aws-sam-cli/releases/latest/download/AWS_SAM_CLI_64_PY3.msi |
| Antigravity IDE | 开发 | 官网下载 |

装完后**关掉并重新打开 PowerShell**，检查：

```powershell
git --version; python --version; aws --version; sam --version
```

四行都有版本号就对了。

### 4.2 准备代码

两种方式，按你的情况选：

- **A. 从零重建（最干净）**：在新电脑新建空文件夹，按本 README 的结构和配置格式，在 IDE 里让 AI 重新生成各个文件。
- **B. 搬运现有代码**：从旧电脑拷贝项目文件。如果旧电脑是公司设备，把代码搬到个人设备前先确认公司的数据外发政策。

新建项目文件夹并初始化 git：

```powershell
mkdir $HOME\Projects\Alter; cd $HOME\Projects\Alter; git init -b main; git config core.autocrlf false; git config core.safecrlf false
```

### 4.3 配置 AWS 账号连接（重点）

**原则：每台电脑用自己的一把钥匙，不要从旧电脑复制 `~/.aws/credentials`。** 这样哪台电脑丢了，只停用那一把。

**第一步：在 AWS 控制台给 `alter-dev` 子用户新建一把 Access Key**

1. 浏览器登录个人 AWS 账号 → 右上角区域选 **US East (N. Virginia)**
2. 顶部搜索 **IAM** → 左侧 **Users** → 点 **alter-dev**
3. **Security credentials** 标签页 → **Access keys** → **Create access key**
4. 用途选 **Command Line Interface (CLI)**，勾选确认 → Next → Create
5. 复制 **Access key ID** 和 **Secret access key**（Secret 只显示这一次）

**第二步：在新电脑上配置 profile**

```powershell
aws configure --profile alter-dev
```

依次粘贴：
```
AWS Access Key ID:     （粘贴 Access key ID）
AWS Secret Access Key: （粘贴 Secret access key）
Default region name:   us-east-1
Default output format: json
```

**第三步：验证**

```powershell
aws sts get-caller-identity --profile alter-dev
```

返回的 `Arn` 末尾应该是 `user/alter-dev`。

**第四步：收尾安全项**

- 新电脑能用了以后，回 IAM 控制台把**旧电脑那把 key 设为 Inactive**，确认没问题再 Delete。
- root 账号的 Access Key 如果还在，删掉；root 只用来登录控制台，并开启 **MFA**。
- 建议在 **Billing → Budgets** 建一个 $5/月 的预算提醒。

> 名字必须是 `alter-dev`：`samconfig.toml` 和 `webapp/server.py` 默认都用这个 profile 名。要换名字，改 `samconfig.toml` 的 `profile`，并在启动网页前设置环境变量 `$env:ALTER_PROFILE = "新名字"`。

### 4.4 部署到 AWS

```powershell
cd $HOME\Projects\Alter; sam build; sam deploy
```

最后出现 `Successfully created/updated stack - alter-poc` 就成功了。

说明：

- 栈名 `alter-poc` 已经存在于账号里，所以这次部署是**原地更新**现有资源，数据库里的草稿不会丢。
- **同一时间只从一台电脑部署。** 两台电脑代码不一致时，最后一次部署会覆盖前一次。
- `sam build` 最后打印的提示菜单不是报错。
- 改定时时间要改 `samconfig.toml` 里的 `FetchSchedule`（带内层引号），**不要**在命令行用 `--parameter-overrides` 传 cron，PowerShell 会把括号拆坏导致栈回滚。
- 只改 `template.yaml` 的 Default 不会生效，因为栈会沿用已存参数；参数改动统一写进 `samconfig.toml`。

验证云端定时器：

```powershell
aws events list-rules --profile alter-dev --region us-east-1 --query "Rules[].{Name:Name,Schedule:ScheduleExpression,State:State}" --output table
```

### 4.5 启动本地网页

```powershell
cd $HOME\Projects\Alter\webapp; python -m pip install boto3; python server.py
```

浏览器会自动打开 http://127.0.0.1:8765 。关掉 PowerShell 窗口即停止。网页只绑定本机地址，别人访问不到。

---

## 5. 推送到个人 GitHub

### 5.1 推荐用**私有仓库**

原因：

- `functions/fetcher/personas.json` 里的范文是**真实他人发布的 LinkedIn 帖子**，公开放到 GitHub 不合适。
- `iam/alter-dev-policy.json` 里含 AWS 账号 ID。账号 ID 本身不是密钥，但没必要公开。

如果以后想公开，先做两件事：把 `personas.json` 加进 `.gitignore`，另提交一个只含占位范文的 `personas.example.json`；把策略文件里的账号 ID 换成 `<ACCOUNT_ID>`。

### 5.2 绝不能提交的东西

- `~/.aws/credentials` 或任何 Access Key（`.gitignore` 已挡 `.env`、`credentials`、`*.pem`、`*.key`）
- `.aws-sam/` 构建产物（已挡）
- 从 DynamoDB 导出的真实数据

`samconfig.toml` 里的 `profile = "alter-dev"` 只是名字，不是密钥，可以提交。

### 5.3 首次推送

1. 在 GitHub 网页上 **New repository** → 名字 `alter` → 选 **Private** → 不勾选 "Add a README" → Create
2. 安装 GitHub CLI 并登录（会打开浏览器授权）：

```powershell
winget install --id GitHub.cli; gh auth login
```

3. 推送（把 `你的GitHub用户名` 换成真实用户名）：

```powershell
cd $HOME\Projects\Alter; git add -A; git status; git commit -m "Initial commit"; git remote add origin https://github.com/你的GitHub用户名/alter.git; git push -u origin main
```

`git push` 前看一眼 `git status` 列出的文件，确认没有密钥。

---

## 6. 日常操作速查

| 想做的事 | 命令（在项目根目录） |
|---|---|
| 改了 Lambda / 配置后部署 | `sam build; sam deploy` |
| 手动抓全部源 | `aws lambda invoke --function-name alter-fetcher --profile alter-dev --region us-east-1 --cli-read-timeout 300 out.json` |
| 看最近日志 | `aws logs tail /aws/lambda/alter-fetcher --since 1h --profile alter-dev --region us-east-1` |
| 存快照 | `git add -A; git commit -m "说明"` |
| 撤销未提交的改动 | `git restore .` |

---

## 7. 已知问题和注意事项

- **boto3 调 Lambda 默认 60 秒超时**：长档双语生成可能超过 60 秒。`server.py` 已设 `read_timeout=300` 并关闭重试，自己写脚本调用时也要设。
- **Google News 兜底源**：内容是全网聚合，可能混入第三方报道；部分原文 403 / 付费墙，AI 只看到摘要，容易脑补，发帖前要自己核对原文。
- **日期**：RSS 和搜索引擎给的时间常不准，晚于今天的日期一律当错误。以原文正文的 "Published" 为准。
- **夏令时**：定时按 UTC 固定，夏令时期间是太平洋 11am。
- **成本是估算**：按 `models.json` 单价计算，对账以 AWS Billing 为准。
- **Windows `.bat` 文件**：里面只用英文，中文会因 cmd 编码乱码报错。
- **旧数据**：早期抓的约 134 条草稿 summary 为空，原始 RSS 已过期无法回填，可在网页里忽略。
- `alter-dev` 用户没有 CloudWatch 指标读取权限（有意不给），查运行情况看 DynamoDB 或 Lambda 日志。

---

## 8. 后续可做

- 云端定时抓取也回写 `last_scanned`（写进 DynamoDB）
- 定时器改用 EventBridge Scheduler 带时区，全年固定当地 10am
- 抓取时检测原文能否打开，打不开的条目打标记
- 路线 B：网页部署到云端（API Gateway + Lambda + S3 静态托管 + 访问密码）
