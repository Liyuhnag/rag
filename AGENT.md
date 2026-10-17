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
