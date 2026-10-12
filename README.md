# Reading Agent

Reading Agent 是一个面向个人中文 EPUB 书库的本地 RAG 阅读与编辑工具。项目一方面把书籍解析为可检索的文本片段，通过 embedding、关键词检索、reranker、摘要索引和多轮会话为大模型提供上下文；另一方面提供可直接修改原文、批注、编辑目录并写回 EPUB 的 Web 阅读器。

当前版本优先服务中文书库，默认通过 AIClient2API 接入 ChatGPT 兼容接口；书籍正文、向量索引、摘要索引和本地模型都保存在本机。

## 应用场景

- 针对个人 EPUB 书库做问答，例如“《荒原狼》主要讲了什么？”
- 查询具体情节、人物关系和细节，例如“宝玉和黛玉的关系是怎么发展的？”
- 比较多本书的主题，例如“比较《瓦尔登湖》和《荒原狼》对孤独的理解。”
- 根据情绪或处境寻找书中片段，例如“我最近很焦虑，有没有书里的片段能回应这种状态？”
- 多轮追问，例如先问一本书，再问“那后来怎么样了？”

项目不适合直接作为通用联网搜索工具，也不负责绕过书籍版权限制。请只导入你有权使用的 EPUB 文件。

## 核心能力

- 双数据管线共用 EPUB 来源解析：阅读器尽量无损保留短诗行、篇名和注释，RAG 在相同的章、段位置上清洗低质量内容，过滤和长段拆分均不重新编号。
- EPUB 解析：从 `data/raw/epub` 读取 EPUB，生成检索数据和完整阅读数据。
- 中文清洗：为检索库过滤目录、版权页、校注、空段、乱码等低质量内容。
- 分类型 chunking：小说、诗歌、哲学、史书使用不同切分策略。
- 本地 embedding：默认支持 `BAAI/bge-base-zh-v1.5`。
- 混合检索：向量检索 + BM25 关键词检索。
- Cross-encoder rerank：默认支持 `BAAI/bge-reranker-base`，不可用时可回退轻量 rerank。
- 摘要索引：离线生成章节摘要和全书摘要，用于总结、比较、人物关系等全局问题。
- 多轮会话：CLI 和 Web 后端维护短期会话状态；CLI 支持 `/history` 和 `/clear`。
- 按问题类型选择 prompt：总结、比较、细节、情绪推荐使用不同回答模板。
- Web 阅读器：章节目录、正文分页、阅读进度、字体/行距/版心/主题设置和章内搜索。
- 原文编辑：正文可像文字处理器一样直接选中、输入、长按删除，也支持跨段批量删除。
- 批注与高亮：选中文字后可添加四色高亮和批注文字。
- 目录编辑：可在目录中删除整章或修改章节名称，并统一纳入撤销历史。
- 持久化保存：正文、目录和批注可写回原 EPUB、阅读器缓存与本地批注文件；保存和更新数据库互相独立。
- 侧边阅读助理：可隐藏、可调宽度，支持当前书、当前章、全书库和选中文字四种上下文。
- 引用回读：回答引用可以直接定位到书籍章节和原文段落。

## 项目结构

```text
reading-memory-agent/
├─ app/
│  ├─ agent/                 # 意图分析、回答生成、回答策略
│  ├─ ingestion/             # EPUB 加载、文本清洗、chunking、索引构建
│  ├─ memory/                # CLI 多轮会话状态
│  ├─ models/                # Pydantic 数据结构
│  ├─ prompts/               # 不同问题类型的回答 prompt
│  ├─ retrieval/             # 向量检索、BM25、混合检索、rerank、摘要检索
│  ├─ services/              # 阅读、编辑、EPUB 持久化、数据库更新和 RAG 服务
│  └─ web/                   # FastAPI 接口与前端静态文件托管
├─ frontend/                 # React + TypeScript + Vite 阅读器
├─ scripts/
│  ├─ ingest_books.py        # EPUB -> books.jsonl / chunks.jsonl
│  ├─ build_reader_library.py # EPUB -> 无损 reader.jsonl
│  ├─ rebuild_index.py       # 重建或增量更新 chunk 向量索引
│  ├─ build_summaries.py     # 离线生成或增量更新摘要
│  ├─ rebuild_summary_index.py
│  ├─ update_database.py     # 单进程增量更新阅读数据、摘要与向量索引
│  ├─ run_cli.py             # 启动命令行阅读助理
│  ├─ run_web.py             # 只启动 Web 阅读器
│  ├─ run_all.py             # 启动模型网关后再启动阅读器
│  ├─ start_api.py           # 检查并启动 AIClient2API
│  ├─ check_models.py        # 检查本地模型目录
│  ├─ inspect_chunking.py    # 抽查 chunking 质量
│  └─ evaluate_retrieval.py  # 检索质量 smoke test
├─ tests/                    # 单元测试和检索流程测试
├─ data/                     # 本地书籍、处理结果、Chroma 索引
├─ models/                   # 本地 embedding/reranker 模型
├─ .env.example
├─ requirements.txt
└─ requirements-embedding-gpu-cu128.txt
```

## 环境要求

推荐环境：

- Windows 10/11
- Python `3.12`
- Conda 环境名建议：`reading-agent`
- NVIDIA GPU 可选。8GB 显存建议使用：
  - embedding：`BAAI/bge-base-zh-v1.5`
  - reranker：`BAAI/bge-reranker-base`

`.env.example` 面向本地 BGE + CUDA。没有 GPU 或模型文件时，也可使用代码内置的 `local-hash` embedding 和轻量 rerank 启动；这种模式无需下载模型，但中文语义检索质量会明显下降。

## 安装依赖

创建环境：

```powershell
conda create -n reading-agent python=3.12
conda activate reading-agent
pip install -r requirements.txt
```

如果使用 RTX 50 系列或 CUDA 12.8 GPU，再安装 GPU 版 PyTorch：

```powershell
pip install -r requirements-embedding-gpu-cu128.txt
```

## 本地模型准备

默认 `.env.example` 使用本地模型路径：

```text
EMBEDDING_MODEL=./models/bge-base-zh-v1.5
RERANK_MODEL=./models/bge-reranker-base
```

你需要把模型下载到：

```text
models/
├─ bge-base-zh-v1.5/
└─ bge-reranker-base/
```

可用方式之一：

```powershell
git lfs install
git clone https://huggingface.co/BAAI/bge-base-zh-v1.5 models/bge-base-zh-v1.5
git clone https://huggingface.co/BAAI/bge-reranker-base models/bge-reranker-base
```

检查模型：

```powershell
python scripts/check_models.py
```

如果只需要快速启动、运行测试，或暂时没有本地模型，可以在 `.env` 中改为：

```text
EMBEDDING_BACKEND=local-hash
EMBEDDING_DEVICE=cpu
RERANK_BACKEND=lightweight
RERANK_DEVICE=cpu
```

`local-hash` 是确定性的轻量 embedding，不等价于 BGE 语义模型。仅需把 BGE 放在 CPU 上运行时，请保留 `EMBEDDING_BACKEND=sentence-transformers`，只把 `EMBEDDING_DEVICE` 和 `RERANK_DEVICE` 改为 `cpu`。

本地 embedding 和 cross-encoder 权重在首次使用时加载，并在同一进程内按模型类别、模型路径、设备和本地加载选项复用。正文检索与摘要检索共享相同配置的 embedding，多轮重排复用 cross-encoder；并发首次请求也只构造一次模型。不同进程仍独立加载，配置变更或原路径下替换模型文件后应重启服务以释放旧模型并载入新权重。

## 配置

复制配置模板：

```powershell
Copy-Item .env.example .env
```

必须配置：

```text
OPENAI_API_KEY=replace-with-your-local-api-key
OPENAI_BASE_URL=http://127.0.0.1:3000/openai-codex-oauth/v1
CHAT_MODEL=gpt-5.4
ANSWER_MODEL=gpt-5.4
INTENT_MODEL=gpt-5.4
```

当前项目默认通过 AIClient2API 提供 OpenAI-compatible Chat API。`OPENAI_API_KEY` 对 AIClient2API 本地服务通常只是占位值，但必须非空；真实登录状态由 AIClient2API 自己维护。

如果希望 CLI 自动启动 AIClient2API：

```text
AUTO_START_API=true
AICLIENT2API_DIR=D:/gitstore/AIClient2API
API_START_TIMEOUT_SECONDS=30
```

如果你的 AIClient2API 不在这个目录，必须修改 `AICLIENT2API_DIR`。

主要检索参数：

```text
VECTOR_INITIAL_K=40
KEYWORD_INITIAL_K=40
RERANK_CANDIDATE_K=60
RERANK_TOP_K=15
FINAL_TOP_K=10
CONTEXT_MAX_CHARS=9000
CHUNK_SIZE=900
CHUNK_OVERLAP=180
```

摘要默认使用本地抽取式生成，不消耗 ChatGPT token：

```text
SUMMARY_BACKEND=extractive
```

如果改成 LLM 摘要，需要自行确认 AIClient2API 可用，并注意 token 消耗。

## 数据准备

把 EPUB 文件放入：

```text
data/raw/epub/
```

示例：

```text
data/raw/epub/
├─ Der Steppenwolf.epub
├─ Walden.epub
└─ The Republic.epub
```

`data/` 默认被 `.gitignore` 忽略，不会提交到 GitHub。

## 复现流程

从空仓库复现完整索引（使用 BGE 时先执行模型检查）：

```powershell
conda activate reading-agent
Copy-Item .env.example .env
python scripts/check_models.py
python scripts/ingest_books.py
python scripts/build_reader_library.py
python scripts/rebuild_index.py
python scripts/build_summaries.py
python scripts/rebuild_summary_index.py
python scripts/evaluate_retrieval.py
```

每一步作用：

- `check_models.py`：确认本地 embedding 和 reranker 模型存在；轻量模式可跳过。
- `ingest_books.py`：解析 EPUB，生成 `books.jsonl` 和 `chunks.jsonl`。
- `build_reader_library.py`：按 EPUB 自带目录生成无损阅读数据，保留短诗行、篇名、注释等全部可读文本。
- `rebuild_index.py`：增量更新 Chroma chunk 向量索引。集合保存 embedding 后端、模型和归一化配置；配置相同且内容与元数据未变化的 chunk 会跳过，只为新增或变化的 chunk 计算向量，并删除已经消失的旧条目。切换模型或旧集合缺少模型配置时会全量重建，即使新旧模型维度相同。空 JSONL 会清空对应集合；缺少输入文件会报错。
- `build_summaries.py`：生成章节摘要和全书摘要；相同 source hash 会跳过。
- `rebuild_summary_index.py`：增量同步摘要向量索引，正文和元数据相同的摘要复用已有向量；保留模型迁移和过期条目清理流程。
- `evaluate_retrieval.py`：运行固定问题集，检查检索是否能返回合理来源。

## 运行 CLI

先确认 AIClient2API 可用：

```powershell
python scripts/start_api.py
```

启动阅读助理：

```powershell
python scripts/run_cli.py
```

## 运行 Web 前端

首次安装或前端依赖变化后，在项目根目录运行：

```powershell
cd frontend
npm.cmd install
npm.cmd run build
cd ..
```

React 依赖安装在 `frontend/node_modules`。当前 PowerShell 环境如果限制执行 `npm.ps1`，请使用 `npm.cmd`。

只启动 FastAPI 和已经构建的阅读器：

```powershell
python scripts/run_web.py
```

`run_web.py` 会等待服务开始监听，然后自动打开 `http://127.0.0.1:8000`（修改 `WEB_PORT` 后会打开对应端口），但不会启动 Client2API。即使模型节点不可用，阅读器、目录、搜索、编辑和阅读进度仍可使用，聊天区会显示模型服务未就绪。

需要同时尝试启动 Client2API 和 Web 阅读器时使用：

```powershell
python scripts/run_all.py
```

也可以在另一个终端先运行 `python scripts/start_api.py`，再运行 `python scripts/run_web.py`。浏览器访问 `http://127.0.0.1:8000`。

Web 首屏是阅读器：左侧书库/目录可隐藏，中间显示完整正文，右侧聊天可隐藏并可调宽度。正文始终可以像文字处理器一样直接选中、输入和删除；跨段选择后删除会作为一次批量操作记录。输入期间不会因后台同步抢走当前选区或把光标移到章节开头。

选中文字会弹出工具条，可添加四色高亮和批注，也可以直接要求 AI 解释或分析选文。阅读工具面板提供批注列表、书签和关键词搜索：可修改批注备注与颜色、删除批注，或为当前阅读段落添加书签；点击列表条目会跳转并短暂高亮原文。搜索默认覆盖整本书，也可限定当前章，以包含关键词的段落为一条结果，支持分页。

工具面板可将当前书籍全部批注和书签导出为 Markdown 或 JSON，包含书名、作者、章节、完整引文、备注、时间和位置。导出前会同步正在输入的正文，导出文件标明是否有未保存的编辑；导出不会替代“保存”。书签位置和批注范围按段落标识解析，原文失效时明确标记，避免同文段落误跳。

目录中的删除按钮可以整章删除；单击铅笔或双击章节名称可进入重命名，按 `Enter` 或勾选按钮确认，按 `Esc` 取消。章节名称保存时会写入 EPUB 3 Nav 和 EPUB 2 NCX 目录；正文中原有的章节标题文字不会自动跟随更名。

顶部的“撤销”“保存”“更新数据库”是三个独立操作：

- “撤销”覆盖本次服务启动后、上次成功保存之后的全部正文、章节删除、章节名称、批注和书签操作。刷新浏览器不会清空历史；保存成功或重启服务时清空。
- “保存”先提交当前未同步的输入，暂存修改后的 EPUB 并重读节点定位，迁移批注和书签后统一替换 EPUB、`reader.jsonl`、批注和书签文件。替换发生 I/O 错误时恢复已替换文件；保存成功后刷新目录、正文与编辑位置并清空此前撤销历史，失败时保留未保存状态和撤销历史。删除正文或章节会同时删除对应批注和书签，保存前可撤销恢复。
- “更新数据库”运行完整的数据更新流程，不会自动保存当前编辑。存在未保存修改时，界面会明确提示本次更新不包含这些内容；任务完成后重启 Web 服务即可载入新书目。

阅读位置和显示偏好保存在浏览器本地，会话与编辑撤销历史保存在后端内存中，后端重启后会清空。点击“保存”后，批注持久化到 `data/user/annotations.json`，书签持久化到 `data/user/bookmarks.json`（可通过 `BOOKMARKS_JSON_PATH` 配置）。回答引用可以跳回对应章节。

阅读器按书籍缓存目录元数据、段落数量及前后章位置，分页复用目录计算结果。正文修改、章节删除或改名、撤销和保存只使受影响书籍的缓存失效，其他书籍继续复用已有缓存。批注按章节和原始段落位置建立索引，分页直接取得覆盖该段的批注；编辑、撤销和保存后的定位迁移同步更新索引。保存与撤销仍作用于整个编辑会话，持久化文件格式不变。

新增、替换或删除 EPUB 后，可点击“更新数据库”，或运行 `python scripts/update_database.py`。总脚本在同一进程内增量更新阅读数据、摘要和两个向量索引，共享已加载的 embedding。一本 EPUB 只解析一次，同时生成完整阅读文本与 RAG 数据；未改书籍复用已有记录，分块参数变化时只重新分块。默认输出阶段汇总，不逐书、逐批打印；完全无变化时只检查文件和配置，跳过 JSONL 读写、摘要分析与模型加载。

首次运行会在阅读数据目录生成 `library_update_state.json` 和 `database_update_state.json`，记录文件大小、纳秒修改时间和相关配置。之后只处理新增、变化及删除的书籍；摘要无待更新内容时不创建 LLM 客户端，不变的正文与摘要向量均复用。缺失产物、模型或摘要配置变化会重新执行必要阶段；阶段失败后可直接重试。更新记录不包含 API 密钥。Web 服务启动时从当前 `reader.jsonl` 载入书库，更新完成后仍需重启服务。

需要排查时运行 `python scripts/update_database.py --verbose` 查看详情。外部工具同时保留 EPUB 的大小与修改时间，或修改了解析规则时，运行 `python scripts/update_database.py --force` 重新解析并检查全部阶段；相同向量仍然复用。摘要生成规则变更时同时更新 `SUMMARY_PROMPT_VERSION`。同一书库应只运行一个数据库更新进程。

从使用独立 RAG 章节编号的旧版本升级时，需要完整运行一次“更新数据库”并重启服务，使已有 chunk、摘要和阅读数据采用同一套位置。更新前先保存编辑；首次迁移旧向量集合会重新计算 embedding。

注意：更新数据库读取的是磁盘上的 EPUB，不会包含尚未点击“保存”的正文或目录修改。需要让本次编辑进入检索库时，应先保存，再更新数据库。

前端开发模式：

```powershell
cd frontend
npm.cmd run dev
```

Vite 开发服务器使用 `http://127.0.0.1:5173`，并把 `/api` 转发到 `http://127.0.0.1:8000`。

CLI 命令：

```text
/debug on    显示意图、query 改写、过滤条件、rerank 和最终证据
/debug off   关闭调试输出
/history     查看当前多轮会话摘要
/clear       清空当前会话上下文
/exit        退出
```

示例问题：

```text
《荒原狼》主要讲了什么？
红楼梦里宝玉和黛玉的关系是怎么发展的？
比较《瓦尔登湖》和《荒原狼》对孤独的理解。
我最近很焦虑，有没有书里的片段能回应这种状态？
只根据《百年孤独》回答，布恩迪亚家族的循环体现在哪里？
```

## 阅读工具 API

接口按书籍组织，基路径为 `/api/reader/books/{book_id}`，OpenAPI 文档见运行中服务的 `/docs`。列表和导出读取当前编辑状态。路由负责参数与响应校验，笔记服务负责视图与导出，编辑会话负责可撤销修改和事务保存；沿用现有段落标识，避免再按引文文本猜测位置。

| 方法 | 路径（相对基路径） | 用途 |
| --- | --- | --- |
| GET / POST | `/annotations` | 分页列出 / 创建批注 |
| PATCH / DELETE | `/annotations/{annotation_id}` | 更新备注或颜色 / 删除批注 |
| GET | `/annotations/{annotation_id}/location` | 解析批注当前原文位置 |
| GET / POST | `/bookmarks` | 分页列出 / 创建书签 |
| DELETE | `/bookmarks/{bookmark_id}` | 删除书签 |
| GET | `/bookmarks/{bookmark_id}/location` | 解析书签当前原文位置 |
| GET | `/search?q=关键词` | 全书关键词搜索 |
| GET | `/paragraphs/{edit_id}/location` | 点击搜索结果前重新解析段落位置 |
| GET | `/notes/export?format=markdown` | 下载全部笔记，格式可选 `markdown` / `json` |

列表和搜索使用 `offset`（默认 0）、`limit`（默认 50，范围 1–100）、可选 `chapter_index`（从 0 开始），返回 `{items, total, offset, limit, next_offset}`。搜索关键词去除首尾空白、长度 1–300，不区分大小写，按字面子串匹配，按章节与段落排序；空结果返回成功的空列表。搜索接口已从原数组响应改为分页对象。

创建批注的请求体为 `{chapter_index, start_edit_id, end_edit_id, start_offset, end_offset, selected_text?, color?, comment?}`；偏移从 0 开始，以 Unicode 码点计数，终点不包含在选区内。创建书签为 `{chapter_index, edit_id, label?}`。书籍归属由 URL 决定，请求体不接受 `book_id` 或未知字段；批注 PATCH 仅接受 `comment`、`color`，清空备注传空字符串，拒绝空 PATCH 与 `null`。

创建成功返回 201 和 `{annotation, state}` / `{bookmark, state}`；更新批注返回 200 和 `{annotation, state}`；删除返回 200 和 `EditorState`，便于更新保存与撤销状态。不存在或不属于该书的资源返回 404；原文定位失效返回 409；字段或范围无效返回 422。错误保留 FastAPI 的 `detail` 字段：业务错误为字符串，字段校验错误为结构化列表。

列表条目提供 `chapter_title`、`excerpt`、完整 `quote` 与 `reader_location`；失效定位为 `null`。导出包含当前书籍全部笔记，不受分页限制，JSON 为 `{book, exported_at, has_unsaved_changes, annotations, bookmarks}`，时间为 UTC ISO 8601。`has_unsaved_changes` 表示共用编辑会话是否有未保存修改，与顶部保存状态一致；导出内容仍仅包含指定书籍。文件通过 `Content-Disposition` 的 UTF-8 文件名下载，不暴露 EPUB 本地路径。旧 `/api/editor/annotations` 创建与删除接口保留并在文档中标记弃用。

## 检查 chunking 质量

```powershell
python scripts/inspect_chunking.py --sample 5 --chars 240
```

默认只预览前若干字符，不代表 chunk 被截断。查看完整 chunk：

```powershell
python scripts/inspect_chunking.py --sample 1 --full
```

当前 chunking 会根据书名推断类型：

- `poetry`：诗经、海子的诗、张枣的诗
- `history`：史记
- `philosophy`：理想国、查拉图斯特拉、西西弗、疯癫与文明、瓦尔登湖
- 其他默认 `fiction`

如果你的书库中有更多类型，需要在 `app/ingestion/chunking.py` 的 `BOOK_TYPE_KEYWORDS` 和 `POLICIES` 中补充。

## 测试

后端测试：

```powershell
python -m pytest -q
```

前端单元测试和生产构建：

```powershell
cd frontend
npm.cmd test -- --run
npm.cmd run build
```

当前测试覆盖：

- EPUB 文本清洗和 chunking
- overlap、章节边界、chunk id 稳定性
- 意图分析 fallback
- query planner metadata filter
- BM25 tokenizer
- 混合检索去重
- comparison 结果平衡
- 相邻 chunk 扩展
- 摘要缓存和摘要索引
- 多轮会话状态
- 阅读器正文过滤、章节分页和段落定位
- EPUB 正文写回、批量删除、批注持久化和保存后基线更新
- 章节删除、章节重命名及 EPUB 目录更新
- 编辑期间的选区、光标和连续退格稳定性
- Web API 不泄露 EPUB 本地路径
- 前端 SSE 解析、编辑状态反馈、书库目录交互和生产构建

## 隐私和开源注意事项

默认不提交以下内容：

- `.env`
- `data/`
- `models/`
- Chroma 索引
- 日志和缓存

开源前请确认：

```powershell
git status --short
```

不要提交：

- 真实账号 token
- AIClient2API 登录缓存
- 有版权限制的 EPUB
- 本地模型大文件
- 生成的索引数据

## 当前限制

- 主要针对中文书库优化，英文书库没有重点调参。
- 书籍类型识别目前基于书名规则，不是自动分类器。
- AIClient2API 只负责本地测试接入；生产环境应改为正式 API 网关或官方 API。
- 摘要默认是抽取式摘要，成本低但表达能力弱于 LLM 摘要。
- 章节重命名只修改 EPUB 目录项，不自动改写正文中的标题节点。
- 数据库更新完成后需要重启 Web 服务，当前进程不会热加载新的阅读器书库。
- RAG 的核心智能仍来自回答模型；embedding、retrieval、rerank 负责提供更合适的上下文，不能替代大模型推理。
