# Agent 工作指南

## Git 分支管理规范

### 分支保护规则

**绝对禁止：**
- ❌ 直接在 `main` 分支上进行任何提交
- ❌ 直接推送到 `main` 分支
- ❌ 在 `main` 分支上进行开发工作

**必须遵守：**
- ✅ 所有开发工作在 `feature` 或 `feat/**` 分支上进行
- ✅ 功能开发从 `feature` 分支切出 `feat/**` 分支
- ✅ 完成后先合并回 `feature` 分支
- ✅ 只有经过充分测试的代码才能合并到 `main`

### 标准工作流程

#### 1. 开始新功能开发

```bash
# 确保在 feature 分支
git checkout feature

# 拉取最新代码
git pull origin feature

# 创建功能分支
git checkout -b feat/功能描述

# 例如：
git checkout -b feat/file-upload-api
git checkout -b feat/chat-streaming
git checkout -b feat/vector-search
```

#### 2. 开发过程中

```bash
# 频繁提交，保持提交粒度适中
git add [文件]
git commit -m "feat: 功能描述"

# 例如：
git commit -m "feat: 实现文件上传接口"
git commit -m "fix: 修复上传进度计算错误"
git commit -m "style: 优化上传按钮样式"
```

#### 3. 完成功能开发

```bash
# 切换到 feature 分支
git checkout feature

# 拉取最新代码
git pull origin feature

# 合并功能分支
git merge feat/功能描述

# 推送到远程
git push origin feature

# 删除本地功能分支（可选）
git branch -d feat/功能描述
```

#### 4. 部署到生产环境（仅负责人操作）

```bash
# 切换到 main 分支
git checkout main

# 合并 feature 分支
git merge feature

# 推送到远程触发部署
git push origin main
```

## Git 提交规范

### Commit Message 格式

```
<type>: <subject>

[optional body]
```

### Type 类型

- `feat`: 新功能
- `fix`: Bug 修复
- `style`: 样式调整（不影响代码逻辑）
- `refactor`: 代码重构（既不是新功能也不是修复）
- `perf`: 性能优化
- `test`: 测试相关
- `docs`: 文档更新
- `chore`: 构建过程或辅助工具的变动
- `revert`: 回滚之前的提交

### 示例

```bash
# 好的提交信息
git commit -m "feat: 添加文件拖拽上传功能"
git commit -m "fix: 修复聊天消息滚动问题"
git commit -m "style: 调整输入框边框颜色"
git commit -m "refactor: 重构向量搜索逻辑"
git commit -m "perf: 优化大文件上传性能"
git commit -m "docs: 更新 API 文档"

# 不好的提交信息
git commit -m "update"
git commit -m "fix bug"
git commit -m "修改了一些东西"
```

## 代码审查要点

### 提交代码前检查清单

- [ ] 代码符合项目编码规范
- [ ] 所有功能都经过测试
- [ ] 没有遗留的 console.log 或调试代码
- [ ] 提交信息清晰准确
- [ ] 没有包含敏感信息（密钥、密码等）
- [ ] 代码已经格式化
- [ ] 分支选择正确（在 feat/** 分支上，不在 main）

### 合并前检查清单

- [ ] 功能完整实现
- [ ] 通过所有测试
- [ ] 代码已经过审查
- [ ] 文档已更新
- [ ] feature 分支是最新的
- [ ] 解决了所有冲突

## 常见场景处理

### 场景 1：意外在 main 分支上修改了代码

```bash
# 暂存当前修改
git stash

# 切换到 feature 分支
git checkout feature

# 创建功能分支
git checkout -b feat/紧急修复

# 恢复修改
git stash pop

# 继续正常开发流程
```

### 场景 2：需要回滚某个提交

```bash
# 查看提交历史
git log --oneline

# 回滚到指定提交（保留修改）
git reset --soft <commit-hash>

# 或者创建一个反向提交
git revert <commit-hash>
```

### 场景 3：功能分支需要同步 feature 最新代码

```bash
# 在功能分支上
git checkout feat/功能名

# 拉取 feature 最新代码并合并
git fetch origin feature
git merge origin/feature

# 或者使用 rebase（保持提交历史线性）
git rebase origin/feature
```

## Agent 行为准则

1. **永远不要在 main 分支上执行 git commit 或 git push**
2. 开始任何开发工作前，确认当前在 `feature` 或 `feat/**` 分支
3. 如果发现自己在 main 分支，立即停止并切换分支
4. 提交信息必须遵守规范格式
5. 每个功能开发使用独立的 `feat/**` 分支
6. 频繁提交，保持每次提交专注于一个改动
7. 合并前确保代码通过测试
8. 删除已合并的功能分支，保持分支列表整洁

## 分支命名建议

```
feat/file-upload           # 文件上传功能
feat/vector-store          # 向量存储
feat/chat-stream           # 聊天流式响应
feat/search-optimization   # 搜索优化
feat/ui-improvement        # UI 改进
fix/upload-progress        # 修复上传进度
fix/memory-leak            # 修复内存泄漏
```

## 紧急情况处理

如果不小心在 main 分支上提交了代码：

```bash
# 1. 不要推送到远程
# 2. 回滚提交
git reset --hard HEAD~1

# 3. 切换到正确的分支
git checkout feature
git checkout -b feat/功能名

# 4. 使用 git reflog 找回丢失的提交
git reflog
git cherry-pick <commit-hash>
```

## 飞书项目管理规范

### 项目管理多维表格

**表格地址：** https://ocn0mresg7ps.feishu.cn/base/JVnnbUcNEaSkvqs7XhTc8ZbKnjf?table=tblDay8wKWi3Vjpf&view=vewhWaSvDl

### 开发流程

#### 1. 开始任务开发

1. 在飞书多维表格中找到对应任务
2. 将任务状态更新为 **"开发中"**
3. 从 `feature` 分支创建对应的功能分支
4. 开始开发工作

```bash
# 示例
git checkout feature
git pull origin feature
git checkout -b feat/file-list-upload-merge
```

#### 2. 开发过程中

- 按照 Git 提交规范进行提交
- 及时更新任务进度（如有需要）
- 将设计文档写入飞书知识库

**知识库地址：** https://ocn0mresg7ps.feishu.cn/wiki/Glekw2U3giVrOukDOcIcVkCPndf

#### 3. 完成开发

1. 确保代码通过测试
2. 将功能分支合并回 `feature` 分支
3. 推送到远程仓库
4. 创建 Pull Request (PR)
5. **将 PR 链接更新到飞书表格对应任务的 PR 字段**
6. **将任务状态更新为 "开发完成"**

```bash
# 合并到 feature 分支
git checkout feature
git merge feat/file-list-upload-merge
git push origin feature

# 创建 PR（使用 GitHub CLI）
gh pr create --base main --head feature --title "feat: 文件列表与上传合并" --body "具体的功能描述"
```

#### 4. 任务状态说明

- **待开发**：任务已创建，等待开始
- **开发中**：正在进行开发工作
- **开发完成**：代码已提交并创建 PR，等待审查
- **已上线**：代码已合并到 main 并部署（由负责人操作）

### 设计文档规范

所有设计文档都应该写入飞书知识库，包括但不限于：

- API 接口设计
- 数据库设计
- 架构设计
- 技术方案
- 性能优化方案

**文档命名规范：** `[模块名] 功能名称 - 文档类型`

示例：
- `[前端] 文件上传 - 技术方案`
- `[后端] 向量检索 - API 设计`
- `[架构] RAG 系统 - 整体设计`

### Agent 自动化工作流程

当收到任务标题时，Agent 应自动执行以下步骤：

1. **更新飞书表格** - 将任务状态改为"开发中"
2. **创建功能分支** - 从 feature 切出对应的 feat 分支
3. **执行开发工作** - 实现功能
4. **合并到 feature** - 完成后合并回 feature 分支
5. **创建 PR** - 创建从 feature 到 main 的 PR
6. **更新飞书表格** - 将 PR 链接填入表格，状态改为"开发完成"
7. **记录设计文档**（如有需要）- 将技术方案写入知识库

## 项目结构

```
rag/                                # 仓库根目录
├── frontend/                       # 前端项目
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload.vue      # 文件上传组件
│   │   │   ├── ChatInterface.vue   # 聊天界面组件
│   │   │   ├── Toast.vue           # Toast 提示组件
│   │   │   ├── Confirm.vue         # 确认对话框组件
│   │   │   └── Pagination.vue      # 分页组件
│   │   ├── icons/                  # 图标组件目录
│   │   │   ├── IconSuccess.vue
│   │   │   ├── IconError.vue
│   │   │   ├── IconWarning.vue
│   │   │   ├── IconInfo.vue
│   │   │   ├── IconClose.vue
│   │   │   ├── IconFilePdf.vue
│   │   │   ├── IconFileMarkdown.vue
│   │   │   ├── IconFileWord.vue
│   │   │   ├── IconFileText.vue
│   │   │   ├── IconFileExcel.vue
│   │   │   └── IconFileCsv.vue
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
│   │   ├── upload.py               # 文件上传接口
│   │   ├── vectorize.py            # 向量化接口
│   │   └── chat.py                 # 对话接口
│   ├── services/                   # 业务逻辑
│   │   ├── vectorization/          # 向量化服务
│   │   │   ├── text_splitter.py
│   │   │   └── embeddings.py
│   │   ├── retrieval/              # 检索服务
│   │   │   ├── vector_search.py
│   │   │   └── reranker.py
│   │   └── llm/                    # LLM 集成
│   │       └── claude_client.py
│   ├── models/                     # 数据模型
│   ├── main.py                     # FastAPI 入口
│   └── requirements.txt
│
├── docs/                           # 文档
│   ├── api/                        # API 文档
│   ├── architecture/               # 架构设计
│   └── deployment/                 # 部署文档
│
├── CLAUDE.md                       # 项目技术文档
├── AGENT.md                        # Agent 工作规范（本文件）
├── README.md                       # 项目说明
└── .gitignore
```

## UI 设计规范

### 设计系统 Token

#### 色彩系统（淡色调）

**主题色**
- `--primary`: `#38BDF8` (天蓝色)
- `--primary-hover`: `#0EA5E9`
- `--primary-active`: `#0284C7`

**表面层级**（浅灰色调）
```css
--surface-0: #F8FAFC;  /* 浅灰背景 */
--surface-1: #FFFFFF;  /* 纯白背景 */
--surface-2: #F1F5F9;  /* 卡片背景 */
--surface-3: #E2E8F0;  /* 悬停状态 */
--surface-4: #CBD5E1;  /* 激活状态 */
```

**文字层级**
```css
--text-primary: #1E293B;    /* 主要文字 */
--text-secondary: #64748B;  /* 次要文字 */
--text-tertiary: #94A3B8;   /* 辅助文字 */
--text-disabled: #CBD5E1;   /* 禁用状态 */
```

**边框与分割线**
```css
--border: #E2E8F0;
--divider: #F1F5F9;
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
--space-2xl: 48px;
```

#### 圆角系统

```css
--radius-sm: 8px;       /* 小组件 */
--radius-md: 12px;      /* 卡片、容器 */
--radius-lg: 16px;      /* 大面板 */
--radius-full: 999px;   /* 按钮、输入框（胶囊形） */
--radius-circle: 50%;   /* 头像、图标 */
```

#### 阴影系统

```css
--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
--shadow-md: 0 4px 6px rgba(0, 0, 0, 0.07);
--shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1);
--shadow-xl: 0 20px 25px rgba(0, 0, 0, 0.15);
```

#### 字体系统

**字体族**
```css
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
--font-mono: "SF Mono", Monaco, "Cascadia Code", monospace;
```

**字体大小**（使用 clamp 实现流式排版）
```css
--text-xs: clamp(0.75rem, 0.7rem + 0.2vw, 0.875rem);
--text-sm: clamp(0.875rem, 0.8rem + 0.3vw, 1rem);
--text-base: clamp(1rem, 0.9rem + 0.4vw, 1.125rem);
--text-lg: clamp(1.125rem, 1rem + 0.5vw, 1.25rem);
--text-xl: clamp(1.25rem, 1.1rem + 0.6vw, 1.5rem);
--text-2xl: clamp(1.5rem, 1.3rem + 0.8vw, 1.875rem);
--text-3xl: clamp(1.875rem, 1.6rem + 1vw, 2.25rem);
```

**字重**
```css
--font-normal: 400;
--font-medium: 500;
--font-semibold: 600;
--font-bold: 700;
```

**行高**
```css
--leading-tight: 1.25;
--leading-normal: 1.5;
--leading-relaxed: 1.75;
```

### 布局原则

#### Grid 布局优先
- 使用 CSS Grid 构建页面框架和复杂布局
- Flexbox 仅用于组件内部的单轴布局
- 避免使用 float 和绝对定位

#### 响应式断点
```css
--breakpoint-sm: 640px;
--breakpoint-md: 768px;
--breakpoint-lg: 1024px;
--breakpoint-xl: 1280px;
--breakpoint-2xl: 1536px;
```

#### 布局约束
- 左侧导航固定宽度：280px
- 内容区域最大宽度：1440px
- 容器内边距：var(--space-lg)
- 栅格间距：var(--space-md)

### 组件设计原则

#### 按钮
- 圆角：`var(--radius-full)` (胶囊形)
- 最小高度：44px（移动端触摸友好）
- 内边距：`var(--space-md) var(--space-xl)`
- 悬停效果：背景色加深 10%

#### 输入框
- 圆角：`var(--radius-full)` (胶囊形)
- 高度：48px
- 内边距：`0 var(--space-lg)`
- 聚焦：主题色边框 + 外发光

#### 卡片
- 圆角：`var(--radius-md)` (12px)
- 背景：`var(--surface-2)`
- 边框：`1px solid var(--border)`
- 内边距：`var(--space-lg)`

#### 头像与图标
- 圆角：`var(--radius-circle)` (50%)
- 图标尺寸：16px / 20px / 24px
- 头像尺寸：32px / 40px / 48px

### 交互组件规范

#### Toast 提示框（页面顶部）
**禁止使用浏览器原生 `alert()`，必须使用自定义 Toast 组件**

- 位置：页面顶部居中，距离顶部 20px
- 用途：成功、失败、警告、信息提示
- 交互：自动消失（默认 3 秒），可手动关闭
- 场景：上传成功、操作失败、信息通知等

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

**样式特征：**
- 彩色图标（左侧）
- 文字内容
- 关闭按钮（右侧）
- 从顶部滑入动画
- **不使用左侧色条**

#### Confirm 确认框（页面正中）
**禁止使用浏览器原生 `confirm()`，必须使用自定义 Confirm 组件**

- 位置：页面正中，遮罩背景
- 用途：需要用户确认的重要操作
- 交互：必须用户选择"确认"或"取消"
- 场景：删除文件、取消上传、退出登录等

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

**样式特征：**
- 顶部彩色图标（圆形背景）
- 标题 + 描述文字
- 取消 + 确认按钮
- 缩放进入动画
- 半透明遮罩
- **不使用边框色条**

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
  :pageSize="20"
  @pageChange="handlePageChange"
/>
```

**功能特性：**
- 上一页/下一页按钮
- 页码快速跳转（首页、末页、当前页周围）
- 总数统计显示（显示第 X-Y 项，共 Z 项）
- 每页数量选择器（10/20/50/100）
- 自动禁用边界按钮
- 页码省略显示（超过 7 页时）

**显示逻辑：**
- 总页数 ≤ 7：显示所有页码
- 总页数 > 7：显示首页、末页、当前页及其周围页码，其他用 "..." 省略
- 当前页高亮显示（主题色背景）

### 文件管理组件

#### 文件类型图标
为每种文件类型设计专属图标，使用 SVG 组件实现：

| 文件类型 | 图标组件 | 颜色 |
|---------|---------|------|
| PDF | `IconFilePdf.vue` | `#EF4444` (红色) |
| Markdown | `IconFileMarkdown.vue` | `#3B82F6` (蓝色) |
| Word | `IconFileWord.vue` | `#2563EB` (深蓝) |
| Text | `IconFileText.vue` | `#64748B` (灰色) |
| Excel | `IconFileExcel.vue` | `#10B981` (绿色) |
| CSV | `IconFileCsv.vue` | `#10B981` (绿色) |

**图标设计规范：**
- 统一使用 24x24 viewBox
- 半透明彩色背景圆圈 (opacity: 0.2)
- 轮廓线条 stroke-width: 2
- 文件类型标识（如 "PDF"、"MD" 等）

#### 文件列表表格
使用 CSS Grid 布局，支持可拖拽列宽调整。

**列宽分配（基于 fr 弹性单位）：**
```css
grid-template-columns: 
  3fr      /* 文件名（最宽） */
  0.8fr    /* 类型 */
  0.8fr    /* 大小 */
  0.8fr    /* 分块数 */
  1.5fr    /* 上传时间 */
  0.8fr;   /* 操作 */
```

**可拖拽列宽特性：**
- 鼠标悬停列边界显示拖拽手柄
- 拖拽时鼠标样式变为 `col-resize`
- 实时计算并调整列宽比例
- 双向调整机制（当前列 + 右侧相邻列）
- 最小列宽限制 0.5fr

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

# 预览生产构建
npm run preview
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
