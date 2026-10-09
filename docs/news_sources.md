# Alter 关注对象新闻源清单

> 目标：抓取 LinkedIn 关注对象的**非 LinkedIn 官方开放渠道**新闻，供 AI 生成评论草稿。
> 所有源均已用 Python 脚本实测验证（HTTP 200 + 真实 feed + 抓到首条标题），验证日期 2026-10-08。
> LinkedIn 本身不开放个人开发者 API，自动抓取会违规封号，因此一律走官方 RSS 或 Google News 兜底。

## 生效中的新闻源（17 家）

| # | 关注对象 | 新闻源 URL | 类型 | 标签 |
|---|---|---|---|---|
| 1 | Inside Water Magazine | https://insidewater.com.au/feed/ | 自家 RSS | water |
| 2 | Water Environment Federation | https://www.wef.org/category/news/feed/ | 自家 RSS（仅新闻） | water |
| 3 | Grundfos | https://news.google.com/rss/search?q=Grundfos+water&hl=en-US | Google News 兜底 | water |
| 4 | Water Online | https://news.google.com/rss/search?q=%22Water+Online%22&hl=en-US | Google News 兜底 | water |
| 5 | Water Resilience Coalition | https://ceowatermandate.org/feed/ | 自家 RSS | water, sustainability |
| 6 | Alliance for Water Stewardship | https://a4ws.org/feed/ | 自家 RSS | water, sustainability |
| 7 | US Water Alliance | https://uswateralliance.org/feed/ | 自家 RSS | water |
| 8 | Trellis Group（原 GreenBiz） | https://trellis.net/feed/ | 自家 RSS | sustainability |
| 9 | AWS for Energy & Utilities | https://aws.amazon.com/blogs/industries/category/industries/energy/feed/ | 自家 RSS（已升级为更宽的 energy 分类） | tech, energy |
| 10 | TechCrunch | https://techcrunch.com/feed/ | 自家 RSS | tech |
| 11 | MIT Technology Review | https://www.technologyreview.com/feed/ | 自家 RSS | tech |
| 12 | Bloomberg (Green) | https://news.google.com/rss/search?q=Bloomberg+Green&hl=en-US | Google News 兜底 | sustainability |
| 13 | Sustainability Magazine | https://news.google.com/rss/search?q=%22Sustainability+Magazine%22+sustainabilitymag&hl=en-US | Google News 兜底 | sustainability |
| 14 | mHUB | https://news.google.com/rss/search?q=mHUB+Chicago&hl=en-US | Google News 兜底 | tech |
| 15 | AI Engineer | https://www.latent.space/feed | 自家 RSS（创始人 swyx 的 Latent Space，已升级替换 Google News） | ai, tech |
| 16 | The Climate Pledge | https://news.google.com/rss/search?q=%22The+Climate+Pledge%22+Amazon&hl=en-US | Google News 兜底（无自家 RSS，/feed/ 404；2026-10-09 实测可抓） | sustainability |
| 17 | CRN | https://news.google.com/rss/search?q=CRN+channel+technology+news&hl=en-US | Google News 兜底（科技渠道媒体，无可发现公开 RSS；2026-10-09 实测可抓） | tech |

## 已放弃

| 关注对象 | 原因 |
|---|---|
| Mr. Data Centers | 纯 LinkedIn 个人品牌，无官网/博客/Medium/newsletter，Google News 用其名字搜零结果。需要时由用户手动贴链接喂入。 |

## 两类源的差异

- **自家 RSS（10 家）**：机构官网自带订阅源，内容最干净、最及时，优先用。
- **Google News 兜底（5 家）**：机构屏蔽爬虫或不提供 RSS，用 Google News 的 RSS 搜索接口按机构名聚合全网新闻。合规免费，但内容是全网聚合（可能混入第三方报道）。

## 水务领域子集（第一批先跑通的）

搭流程时先只配这 6 家验证链路，确认无误再加全部 15 家：
1. Inside Water Magazine
2. Water Environment Federation
3. Water Resilience Coalition
4. Alliance for Water Stewardship
5. US Water Alliance
6. Grundfos
