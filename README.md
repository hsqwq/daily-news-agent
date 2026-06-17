# 📰 每日新闻智能摘要 Agent

**基于 DeepSeek API + 60+ 全球 RSS 源 + Function Calling 的智能新闻聚合助手**

## 项目简介

这是一个 AI Agent，能够根据用户的自然语言提示词，自主从 60+ 全球优质 RSS 新闻源获取最新新闻，进行智能分析整合，生成高质量的新闻摘要，并通过邮件发送给用户。

### 实验二选题：Bring Your Own Agent (BYOA)

本项目为「软件产品综合开发实践-实验二」的完整实现。

## 架构设计

```
User Input (自然语言)
    │
    ▼
┌──────────────────────────────┐
│   Agent Orchestrator          │
│   DeepSeek API                │
│   Function Calling Loop       │
│                                │
│   System Prompt: 新闻主编      │
│   ReAct: Plan → Call → Observe│
└──────┬───────────────────────┘
       │ Tool Calls
       ├── fetch_rss_feeds     (RSS 批量获取)
       ├── fetch_article_content (网页正文抓取)
       ├── search_news          (关键词搜索)
       └── send_email           (邮件发送)
              │
              ▼
┌──────────────────────────────┐
│   Data Layer (SQLite)        │
│   - 去重 / 缓存 / 状态管理    │
└──────────────────────────────┘
```

## 功能特性

- **🌐 60+ 全球 RSS 源**：覆盖 AI、科技、综合新闻、财经、科学、编程 6 大类
- **🤖 智能分析**：DeepSeek API 驱动的新闻筛选、排序、摘要、趋势提炼
- **🔗 多源交叉验证**：同一事件多源报道自动合并，标注信息来源
- **📊 深度阅读**：Readability 算法提取网页正文，8-15 篇重点文章深度分析
- **📧 邮件投递**：HTML 格式精美邮件，自动发送到指定邮箱
- **💾 持久化去重**：SQLite 存储，URL 哈希去重，7 天自动清理

## 技术栈

| 层 | 技术 |
|---|------|
| LLM | DeepSeek API (`deepseek-chat`) + Function Calling |
| Agent | 自建 ReAct 风格编排循环 |
| RSS | `feedparser` + `httpx` 异步并发 |
| 正文提取 | `readability-lxml` + `BeautifulSoup4` |
| 邮件 | `aiosmtplib` (SMTP SSL/TLS) |
| 存储 | `aiosqlite` (SQLite) |
| 配置 | YAML + .env 环境变量 |
| 桌面界面 | PyQt6 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，填入你的 DeepSeek API Key 和邮箱配置
```

**.env 配置项说明：**

```bash
# 必填：DeepSeek API Key
DEEPSEEK_API_KEY=sk-your-deepseek-api-key

# 可选：邮件发送（不配置则使用预览模式，HTML 保存到本地）
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=your-email@qq.com
SMTP_PASSWORD=your-smtp-auth-code
DEFAULT_RECIPIENT=your-email@qq.com
```

> DeepSeek API Key 申请: https://platform.deepseek.com/

### 3. 运行

```bash
# 图形化工作台（默认启动）
python main.py

# 终端交互模式
python main.py --cli

# 单次运行
python main.py --prompt "总结今天AI领域最重要的10条新闻"

# 预览模式（邮件保存为 HTML 文件）
python main.py --dry-run --prompt "今日科技新闻摘要"

# 真实发送邮件
python main.py --send --prompt "今日财经要闻" --email me@qq.com
```

### 4. 图形化界面

无参数运行 `python main.py` 会进入 PyQt6 桌面界面，包含两个主要子界面：

- **配置中心**：编辑 `.env` 中的 DeepSeek、模型、SMTP、默认收件人和 Agent 参数，并检查关键配置状态。
- **任务工作台**：输入提示词、选择 RSS 分类、设置预览/发送模式、运行 Agent、查看工具调用日志、数据库统计和最新 HTML 邮件预览。

如果只想验证外部工具链而不调用 LLM，可在任务工作台点击「RSS 工具测试」。

### 5. 交互模式命令

```
🧑 你: 总结今天最重要的科技新闻
🧑 你: categories          # 查看可用分类
🧑 你: stats               # 查看数据库统计
🧑 你: quit                # 退出
```

## 项目结构

```
ai_agent/
├── agent/                  # Agent 核心
│   ├── orchestrator.py     # ReAct Function Calling 循环
│   ├── system_prompt.py    # 系统提示词（核心 Prompt）
│   └── models.py           # Pydantic 数据模型
├── tools/                  # 工具实现
│   ├── rss_fetcher.py      # RSS 获取工具（60+ 源）
│   ├── content_fetcher.py  # 网页正文抓取工具
│   ├── news_search.py      # 新闻搜索工具
│   └── email_sender.py     # 邮件发送工具
├── config/
│   ├── feeds.yaml          # RSS 源配置（60+ 源，按分类组织）
│   └── settings.yaml       # 全局配置
├── utils/
│   ├── db.py               # SQLite 数据库操作
│   └── text_utils.py       # 文本处理工具
├── data/                   # 运行时数据（自动创建）
│   ├── news.db             # SQLite 数据库
│   └── email_previews/     # 邮件预览 HTML
├── main.py                 # 入口
├── requirements.txt
├── .env.example
└── README.md
```

## 工具定义

| 工具 | 功能 | 参数 |
|------|------|------|
| `fetch_rss_feeds` | 从 RSS 源批量获取新闻 | categories, specific_feeds, max_per_feed, since_hours |
| `fetch_article_content` | 抓取网页正文（readability 提取） | urls[], max_length |
| `search_news` | 关键词搜索已获取的新闻 | keywords[], categories, language |
| `send_email` | 发送 HTML 邮件摘要 | recipient, subject, body_html |

## RSS 源覆盖

| 分类 | 源数 | 代表源 |
|------|------|--------|
| 🤖 AI | 11 | OpenAI, Anthropic, Google AI, Hugging Face, 机器之心, 量子位 |
| 💻 科技 | 14 | TechCrunch, The Verge, WIRED, Hacker News, 36Kr, 少数派 |
| 📰 综合 | 11 | BBC, Reuters, CNN, Guardian, NYT, 澎湃新闻 |
| 💰 财经 | 9 | CNBC, Bloomberg, Yahoo Finance, WSJ, 华尔街见闻 |
| 🔬 科学 | 7 | Nature, ScienceDaily, MIT Tech Review, 果壳网 |
| 👨‍💻 编程 | 12 | GitHub, Stack Overflow, InfoQ, 掘金, CSDN |

## 实验报告对应

| 报告要求 | 本实现 |
|---------|--------|
| 工具/Skills（≥2） | 4 个工具：RSS 获取、正文抓取、新闻搜索、邮件发送 |
| 上下文集成技术 | DeepSeek Function Calling（标准 LLM function calling 协议） |
| Agent 主要功能 | 多源新闻聚合 → 智能分析 → 分类摘要 → 邮件投递 |
| Vibe Coding | 样板代码（RSS 解析、HTTP 请求、邮件发送、数据模型）由 AI 生成 |

## License

本项目为课程实验项目。
