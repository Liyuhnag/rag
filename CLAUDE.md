# RAG 项目技术文档

## 项目概述

RAG (Retrieval-Augmented Generation) 智能对话系统，通过向量检索技术增强大语言模型的回答准确性。

### 核心功能
1. **文档向量化** - 上传文档并转换为向量存储
2. **智能检索对话** - 基于向量检索的上下文增强对话

## 技术栈

### 前端技术
- **框架**：Vue 3 + Vite
- **构建工具**：Vite
- **样式方案**：原生 CSS + CSS Grid
- **主题风格**：淡色调、简洁风格
- **布局模式**：左侧固定导航（280px） + 右侧自适应内容区

### 后端技术（规划中）
- **语言**：Python 3.10+
- **向量数据库**：Chroma / Pinecone / Milvus（待选型）
- **LLM 接口**：Anthropic Claude API / OpenAI API
- **文档处理**：LangChain / LlamaIndex
- **向量模型**：sentence-transformers

## Git 工作流规范

### 分支策略

```
main (生产环境)
  ↑
  └─ feature (集成分支)
       ↑
       ├─ feat/file-upload
       ├─ feat/vector-search
       └─ feat/chat-interface
```

#### 分支说明
| 分支类型 | 用途 | 保护级别 |
|---------|------|---------|
| `main` | 生产部署分支 | 🔒 禁止直接推送 |
| `feature` | 开发主分支 | ⚠️ 需要 review |
| `feat/**` | 功能开发分支 | ✅ 自由提交 |

### 开发流程

**1. 开始新功能**
```bash
git checkout feature
git pull origin feature
git checkout -b feat/功能简称
```

**2. 开发与提交**
```bash
git add .
git commit -m "feat: 添加文件上传功能"
git push origin feat/功能简称
```

**3. 合并到 feature**
```bash
git checkout feature
git merge feat/功能简称
git push origin feature
```

**4. 发布到生产（仅负责人）**
```bash
git checkout main
git merge feature
git push origin main
```

### Commit Message 规范

格式：`<type>: <subject>`

| Type | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 添加文件拖拽上传` |
| `fix` | Bug 修复 | `fix: 修复上传进度计算错误` |
| `style` | 样式调整 | `style: 优化按钮圆角` |
| `refactor` | 代码重构 | `refactor: 抽取向量搜索逻辑` |
| `perf` | 性能优化 | `perf: 优化大文件上传性能` |
| `docs` | 文档更新 | `docs: 更新 API 接口文档` |
| `test` | 测试相关 | `test: 添加上传组件单元测试` |
| `chore` | 构建/工具 | `chore: 升级 Vite 到 5.0` |

## 飞书项目管理规范

### 资源链接

| 资源 | 链接 | 用途 |
|------|------|------|
| 多维表格 | [打开表格](https://ocn0mresg7ps.feishu.cn/base/JVnnbUcNEaSkvqs7XhTc8ZbKnjf?table=tblDay8wKWi3Vjpf&view=vewhWaSvDl) | 任务管理、状态跟踪 |
| 知识库 | [打开知识库](https://ocn0mresg7ps.feishu.cn/wiki/Glekw2U3giVrOukDOcIcVkCPndf) | 技术文档、设计方案 |

### 任务状态流转

```
待开发 → 开发中 → 开发完成 → 已上线
  ↓        ↓         ↓          ↓
创建任务  开始开发  提交PR    部署生产
```

| 状态 | 说明 | 操作要求 |
|------|------|---------|
| **待开发** | 任务已创建，等待认领 | 无需操作 |
| **开发中** | 正在开发 | 开始开发时更新状态 |
| **开发完成** | 已提交 PR，等待审查 | 填写 PR 链接 |
| **已上线** | 已部署到生产环境 | 由负责人操作 |

### Agent 自动化流程

收到任务标题后，自动执行：

1. ✅ 更新飞书表格状态 → **开发中**
2. ✅ 创建功能分支 `feat/任务名`
3. ✅ 实现功能并提交代码
4. ✅ 合并到 `feature` 分支
5. ✅ 创建 PR（feature → main）
6. ✅ 更新飞书表格：填写 PR 链接，状态 → **开发完成**
7. ✅ 如有必要，在知识库记录设计文档

## 项目结构

```
rag/                                # 仓库根目录
├── frontend/                       # 前端项目
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload.vue      # 文件上传组件
│   │   │   └── ChatInterface.vue   # 聊天界面组件
│   │   ├── App.vue                 # 主应用
│   │   ├── main.js                 # 入口文件
│   │   └── style.css               # 全局样式
│   ├── public/
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
│
├── backend/                        # 后端项目（规划中）
│   ├── api/                        # API 接口
│   ├── services/                   # 业务逻辑
│   ├── models/                     # 数据模型
│   ├── main.py                     # FastAPI 入口
│   └── requirements.txt
│
├── CLAUDE.md                       # 项目技术文档（本文件）
├── AGENT.md                        # Agent 工作规范
└── README.md                       # 项目说明
```

## UI 设计规范

### 设计系统 Token

#### 色彩系统（淡色调）

**主题色**
```css
--primary: #38BDF8;     /* 天蓝色 */
```

**表面层级**（浅灰色调）
```css
--surface-0: #FFFFFF;   /* 纯白背景 */
--surface-1: #F8FAFC;   /* 应用背景 */
--surface-2: #F1F5F9;   /* 卡片背景 */
--surface-3: #E2E8F0;   /* 次级背景 */
```

**文字层级**
```css
--text-primary: #1E293B;    /* 主要文字 */
--text-secondary: #64748B;  /* 次要文字 */
--text-tertiary: #94A3B8;   /* 辅助文字 */
```

**边框**
```css
--border: #E2E8F0;
```

**状态色**
```css
--success: #10B981;
--warning: #F59E0B;
--error: #EF4444;
--info: #3B82F6;
```

#### 间距系统

```css
--space-xs: 4px;
--space-sm: 8px;
--space-md: 16px;
--space-lg: 24px;
--space-xl: 32px;
```

#### 圆角系统

```css
--radius-sm: 4px;       /* 小组件 */
--radius-md: 8px;       /* 卡片、容器 */
--radius-lg: 12px;      /* 大面板 */
```

#### 字体系统

```css
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
--font-size-sm: 14px;
--font-size-base: 16px;
--font-size-lg: 18px;
```

### 组件设计原则

#### 基础组件
- **按钮**：圆角 8px，无 hover 效果
- **输入框**：圆角 8px，边框样式
- **卡片**：圆角 8px，浅灰背景
- **布局**：使用 CSS Grid，左侧导航 280px

#### Alert 提示组件（重要）

**强制规范：禁止使用浏览器原生 `alert()`、`confirm()`、`prompt()`**

所有提示信息必须使用自定义 Alert 组件，显示在页面中央，按严重程度使用不同样式：

| 类型 | 颜色 | 用途 | 示例 |
|------|------|------|------|
| `success` | `#10B981` 绿色 | 操作成功 | "文件上传成功" |
| `error` | `#EF4444` 红色 | 错误、失败 | "文件上传失败" |
| `warning` | `#F59E0B` 橙色 | 警告、确认 | "确定要删除这个文件吗？" |
| `info` | `#3B82F6` 蓝色 | 提示信息 | "正在处理中..." |

**Alert 组件规范：**
```vue
<Alert
  :type="'success' | 'error' | 'warning' | 'info'"
  :message="提示文字"
  :show="布尔值"
  @close="关闭回调"
  :duration="自动关闭时间(ms)，默认3000"
/>
```

**样式要求：**
- 固定定位在页面中央（垂直水平居中）
- 圆角 8px
- 左侧色条（4px 宽）+ 图标 + 文字
- 半透明背景遮罩
- 支持手动关闭（右上角关闭按钮）
- 支持自动关闭（可配置时长）

### 交互原则

- 不使用 hover 效果
- 不使用过渡动画
- 简洁直接的交互反馈

#### 图标管理
所有图标统一使用 SVG 组件，存放在 `frontend/src/icons/` 目录：

**通用图标**
- `IconSuccess.vue` - 成功图标（打勾圆圈）
- `IconError.vue` - 错误图标（叉号圆圈）
- `IconWarning.vue` - 警告图标（感叹号三角形）
- `IconInfo.vue` - 信息图标（感叹号圆圈）
- `IconClose.vue` - 关闭图标（叉号）

**文件类型图标**
- `IconFilePdf.vue` - PDF 文件图标（红色）
- `IconFileWord.vue` - Word 文档图标（蓝色）
- `IconFileExcel.vue` - Excel 表格图标（绿色）
- `IconFileMarkdown.vue` - Markdown 文件图标（紫色）
- `IconFileCsv.vue` - CSV 文件图标（橙色）
- `IconFileText.vue` - 纯文本文件图标（灰色）

#### Toast 提示框（页面顶部）
- 位置：页面顶部居中，距离顶部 20px
- 用途：成功、失败、警告、信息提示
- 交互：自动消失（默认 3 秒），可手动关闭
- 场景：上传成功、操作失败、信息通知等
- 样式特征：
  - 彩色图标（左侧）
  - 文字内容
  - 关闭按钮（右侧）
  - 从顶部滑入动画
  - **不使用左侧色条**

**Toast 使用规范**
```vue
<Toast
  type="success"      <!-- success | error | warning | info -->
  message="操作成功"
  :show="true"
  :duration="3000"    <!-- 自动关闭时间，毫秒 -->
  @close="handleClose"
/>
```

#### Confirm 确认框（页面正中）
- 位置：页面正中，遮罩背景
- 用途：需要用户确认的重要操作
- 交互：必须用户选择"确认"或"取消"
- 场景：删除文件、取消上传、退出登录等
- 样式特征：
  - 顶部彩色图标（圆形背景）
  - 标题 + 描述文字
  - 取消 + 确认按钮
  - 缩放进入动画
  - 半透明遮罩
  - **不使用边框色条**

**Confirm 使用规范**
```vue
<Confirm
  title="确认删除"
  message="确定要删除该文件吗？此操作不可恢复。"
  type="danger"       <!-- warning | danger | info -->
  :show="true"
  confirmText="确认"
  cancelText="取消"
  @confirm="handleConfirm"
  @cancel="handleCancel"
/>
```

**重要规范**
- ❌ **禁止使用浏览器原生弹窗**：`alert()`、`confirm()`、`prompt()`
- ✅ **提示类消息使用 Toast**：成功/失败提示、信息通知
- ✅ **确认类操作使用 Confirm**：删除、取消、退出等重要操作
- ✅ **位置严格区分**：Toast 在顶部，Confirm 在中央
- ✅ **类型选择**：
  - Toast: `success`（成功）、`error`（失败）、`warning`（警告）、`info`（信息）
  - Confirm: `warning`（警告操作）、`danger`（危险操作）、`info`（信息确认）

#### Pagination 分页组件
用于文件列表、搜索结果等长列表的分页展示。

**分页组件规范**
```vue
<Pagination
  :currentPage="1"
  :totalPages="10"
  :totalItems="95"
  @page-change="handlePageChange"
/>
```

**功能特性：**
- 上一页/下一页按钮
- 页码快速跳转（首页、末页、当前页周围）
- 总数统计显示
- 自动禁用边界按钮
- 页码省略显示（超过 7 页时）

**显示逻辑：**
- 总页数 ≤ 7：显示所有页码
- 总页数 > 7：显示首页、末页、当前页及其周围页码，其他用 "..." 省略
- 当前页高亮显示（主题色背景）

#### 文件管理列表
文件管理界面展示用户上传的文档，支持分页浏览和文件操作。

**列表结构：**
```
文件名 | 类型 | 大小 | 分块数 | 上传时间 | 操作人 | 操作
```

**列宽分配（Grid）：**
```css
grid-template-columns: 2fr 100px 100px 100px 140px 150px 100px;
```

**文件类型徽章样式：**
- PDF - 红色边框 `#EF4444`
- Word - 蓝色边框 `#3B82F6`
- Excel - 绿色边框 `#10B981`
- Markdown - 紫色边框 `#A855F7`
- CSV - 橙色边框 `#F59E0B`
- Text - 灰色边框 `#6B7280`

**操作人显示：**
- 圆形头像（28px）：显示用户名首字母
- 头像颜色：主题色背景 `#38BDF8`
- 旁边显示完整用户名

**操作按钮：**
- 删除按钮：hover 时红色边框和背景
- 按钮尺寸：32x32px
- 图标尺寸：16x16px

## 开发指南

### 环境要求

**前端**
- Node.js >= 18.0.0
- npm >= 9.0.0

**后端（规划中）**
- Python >= 3.10
- pip >= 23.0

### 快速开始

#### 前端开发

```bash
# 安装依赖
cd frontend
npm install

# 启动开发服务器
npm run dev
# 访问 http://localhost:5173

# 构建生产版本
npm run build
```

### 代码规范

#### 前端

**Vue 组件结构**
```vue
<script setup>
// 1. imports
// 2. props/emits
// 3. reactive state
// 4. computed
// 5. methods
// 6. lifecycle hooks
</script>

<template>
  <!-- 模板内容 -->
</template>

<style scoped>
/* 组件样式 */
</style>
```

**命名规范**
- 组件文件：PascalCase（`FileUpload.vue`）
- 组件名：PascalCase（`<FileUpload />`）
- 变量/函数：camelCase（`handleUpload`）
- CSS 类名：kebab-case（`.file-upload-button`）
- 常量：UPPER_SNAKE_CASE（`MAX_FILE_SIZE`）

#### 后端（规划中）

**Python 代码风格**
- 遵循 PEP 8
- 使用 type hints
- 最大行长度：88（Black 默认）

**命名规范**
- 文件/模块：snake_case（`text_splitter.py`）
- 类名：PascalCase（`VectorStore`）
- 函数/变量：snake_case（`get_embeddings`）
- 常量：UPPER_SNAKE_CASE（`MAX_CHUNK_SIZE`）
