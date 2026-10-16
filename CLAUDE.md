# RAG 项目文档

## 项目概述

这是一个 RAG (Retrieval-Augmented Generation) 项目，主要包含两个核心功能：
1. 文本向量化（文件上传）
2. 检索对话（聊天界面）

## 技术栈

### 前端
- Vue 3 + Vite
- 深色主题，天蓝色 (#38BDF8) 作为主题色
- 左侧固定导航，右侧内容区域
- CSS Grid 布局

### 后端（待开发）
- Python（推荐）
- 向量数据库（如 Chroma、Pinecone）
- LLM API（如 OpenAI、Anthropic Claude）

## Git 分支规范

**重要：严格遵守以下分支规范**

### 分支结构
- `main` - **生产部署分支，禁止直接提交或推送**
- `feature` - 开发主分支，所有功能开发的基础分支
- `feat/**` - 具体功能开发分支，从 feature 分支切出

### 工作流程
1. 从 `feature` 分支切出功能分支：`git checkout -b feat/功能名称`
2. 在功能分支上开发和提交
3. 完成后合并回 `feature` 分支
4. 经过测试后，由负责人将 `feature` 合并到 `main` 进行部署

### 提交规范
- `feat: 新功能描述` - 新功能
- `fix: 修复描述` - Bug 修复
- `style: 样式调整描述` - 样式修改
- `refactor: 重构描述` - 代码重构
- `docs: 文档更新描述` - 文档更新
- `chore: 构建/工具链更新` - 构建配置或工具更新

## 项目结构

**⚠️ 重要：前后端共用一个 Git 仓库，所有代码都在 /Applications/rag/ 下**

```
rag/                          # 仓库根目录
├── frontend/                 # 前端项目
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload.vue     # 文件上传组件
│   │   │   └── ChatInterface.vue  # 聊天界面组件
│   │   ├── App.vue                # 主应用组件
│   │   ├── main.js                # 入口文件
│   │   └── style.css              # 全局样式
│   ├── public/
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── backend/                  # 后端项目（待开发）
│   ├── api/                  # API 接口
│   ├── services/             # 业务逻辑
│   │   ├── vectorization/    # 向量化服务
│   │   └── retrieval/        # 检索服务
│   └── requirements.txt      # Python 依赖
├── docs/                     # 项目文档
├── CLAUDE.md                 # 本文件 - 技术文档
├── AGENT.md                  # Agent 工作规范
├── README.md                 # 项目说明
└── .gitignore                # Git 忽略规则
```

## 设计规范

### 配色方案（深色主题）
- **主题色**：天蓝色 `#38BDF8`
- **表面层**（深色梯度）：
  - `--surface-0`: `#05070C` (最深)
  - `--surface-1`: `#0A0D12`
  - `--surface-2`: `#0F131C`
  - `--surface-3`: `#161D2B`
  - `--surface-4`: `#1E2636` (最浅)
- **文字**：
  - 主要：`#E2E8F0`
  - 次要：`#94A3B8`
  - 弱化：`#64748B`
- **边框**：`rgba(255, 255, 255, 0.1)`

### 布局原则
- 左侧导航固定宽度 280px
- 内容区域自适应
- 无悬浮元素
- 使用 CSS Grid 作为主要布局方式
- 圆角使用：
  - 按钮：`999px` (胶囊形)
  - 卡片/容器：`12px`
  - 输入框：`999px` (胶囊形)

## 开发说明

### 前端开发

启动开发服务器：
```bash
cd frontend
npm run dev
```

构建生产版本：
```bash
cd frontend
npm run build
```

### 后端开发（待补充）

```bash
cd backend
# 待添加启动命令
```

## 待开发功能

### 后端
- [ ] 文件上传 API
- [ ] 文本向量化处理
- [ ] 向量存储
- [ ] 检索 API
- [ ] 对话生成 API

### 前端
- [ ] 文件上传功能实现
- [ ] 上传进度显示
- [ ] 聊天消息发送
- [ ] 流式响应显示
- [ ] 来源引用展示
