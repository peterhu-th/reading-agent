# Reading Memory Agent

Reading Memory Agent 是一个面向个人中文 EPUB 书库的本地 RAG 阅读助理。项目把本地书籍解析为可检索的文本片段，使用本地 embedding、关键词检索、cross-encoder reranker、摘要索引和多轮会话，为大模型提供更合适的上下文，再由对话模型生成带引用的回答。

当前版本优先服务中文书库，默认通过 AIClient2API 接入 ChatGPT 兼容接口；书籍正文、向量索引、摘要索引和本地模型都保存在本机。

## 应用场景

- 针对个人 EPUB 书库做问答，例如“《荒原狼》主要讲了什么？”
- 查询具体情节、人物关系和细节，例如“宝玉和黛玉的关系是怎么发展的？”
- 比较多本书的主题，例如“比较《瓦尔登湖》和《荒原狼》对孤独的理解。”
- 根据情绪或处境寻找书中片段，例如“我最近很焦虑，有没有书里的片段能回应这种状态？”
- 多轮追问，例如先问一本书，再问“那后来怎么样了？”

项目不适合直接作为通用联网搜索工具，也不负责绕过书籍版权限制。请只导入你有权使用的 EPUB 文件。

## 核心能力

- EPUB 解析：从 `data/raw/epub` 读取 EPUB，抽取正文段落。
- 中文清洗：过滤目录、版权页、校注、空段、乱码等低质量内容。
- 分类型 chunking：小说、诗歌、哲学、史书使用不同切分策略。
- 本地 embedding：默认支持 `BAAI/bge-base-zh-v1.5`。
- 混合检索：向量检索 + BM25 关键词检索。
- Cross-encoder rerank：默认支持 `BAAI/bge-reranker-base`，不可用时可回退轻量 rerank。
- 摘要索引：离线生成章节摘要和全书摘要，用于总结、比较、人物关系等全局问题。
- 多轮会话：CLI 内维护短期会话状态，支持 `/history` 和 `/clear`。
- 按问题类型选择 prompt：总结、比较、细节、情绪推荐使用不同回答模板。

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
│  └─ services/              # AIClient2API 启动和健康检查
├─ scripts/
│  ├─ ingest_books.py        # EPUB -> books.jsonl / chunks.jsonl
│  ├─ rebuild_index.py       # 重建或增量更新 chunk 向量索引
│  ├─ build_summaries.py     # 离线生成或增量更新摘要
│  ├─ rebuild_summary_index.py
│  ├─ run_cli.py             # 启动命令行阅读助理
│  ├─ start_api.py           # 检查并启动 AIClient2API
│  ├─ check_models.py        # 检查本地模型目录
│  ├─ inspect_chunking.py    # 抽查 chunking 质量
│  └─ evaluate_retrieval.py  # 检索质量 smoke test
├─ tests/                    # 单元测试和检索流程测试
├─ data/                     # 本地书籍、处理结果、Chroma 索引；默认不提交
├─ models/                   # 本地 embedding/reranker 模型；默认不提交
├─ .env.example              # 可提交的配置模板
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

最低可运行方式是 CPU + 轻量 rerank，但中文检索质量和速度会下降。

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

如果你暂时没有 GPU，可以在 `.env` 中改为：

```text
EMBEDDING_DEVICE=cpu
RERANK_BACKEND=lightweight
RERANK_DEVICE=cpu
```

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

从空仓库复现完整索引：

```powershell
conda activate reading-agent
Copy-Item .env.example .env
python scripts/check_models.py
python scripts/ingest_books.py
python scripts/rebuild_index.py
python scripts/build_summaries.py
python scripts/rebuild_summary_index.py
python scripts/evaluate_retrieval.py
```

每一步作用：

- `check_models.py`：确认本地 embedding 和 reranker 模型存在。
- `ingest_books.py`：解析 EPUB，生成 `books.jsonl` 和 `chunks.jsonl`。
- `rebuild_index.py`：构建 Chroma chunk 向量索引；会跳过内容哈希一致的 chunk。
- `build_summaries.py`：生成章节摘要和全书摘要；相同 source hash 会跳过。
- `rebuild_summary_index.py`：构建摘要向量索引。
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

```powershell
python -m pytest -q
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
- RAG 的核心智能仍来自回答模型；embedding、retrieval、rerank 负责提供更合适的上下文，不能替代大模型推理。
