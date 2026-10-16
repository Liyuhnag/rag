# Backend - RAG 服务

## 待开发

后端服务将提供以下功能：

### 1. 文件处理 API
- 文件上传接口
- 文件格式解析（PDF、TXT、MD、DOCX）
- 文本提取和预处理

### 2. 向量化服务
- 文本分块
- 向量嵌入生成
- 向量存储

### 3. 检索服务
- 向量相似度搜索
- 上下文检索
- 结果排序和过滤

### 4. 对话生成 API
- LLM 集成
- 流式响应
- 上下文管理

## 技术栈（建议）

- **框架**: FastAPI / Flask
- **向量数据库**: Chroma / Pinecone / Weaviate
- **LLM**: OpenAI / Anthropic Claude
- **文本处理**: LangChain / LlamaIndex

## 目录结构

```
backend/
├── api/              # API 路由和端点
├── services/         # 业务逻辑
│   ├── vectorization/  # 向量化服务
│   └── retrieval/      # 检索服务
├── models/           # 数据模型
├── utils/            # 工具函数
├── config/           # 配置文件
├── requirements.txt  # Python 依赖
└── main.py          # 应用入口
```
