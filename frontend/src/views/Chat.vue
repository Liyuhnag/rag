<script setup>
import { ref, nextTick } from 'vue'

const messages = ref([])
const inputMessage = ref('')
const isLoading = ref(false)
const chatContainer = ref(null)

const sendMessage = async () => {
  if (!inputMessage.value.trim() || isLoading.value) return

  const userMessage = {
    id: Date.now(),
    type: 'user',
    content: inputMessage.value,
    timestamp: new Date()
  }

  messages.value.push(userMessage)
  const question = inputMessage.value
  inputMessage.value = ''

  await nextTick()
  scrollToBottom()

  isLoading.value = true

  // 模拟AI响应
  setTimeout(() => {
    const aiMessage = {
      id: Date.now() + 1,
      type: 'assistant',
      content: `这是对 "${question}" 的回答。在实际系统中，这里会显示基于向量检索的结果。系统会从知识库中找到最相关的文档片段，并基于这些内容生成回答。`,
      sources: [
        { name: '文档A.pdf', page: 3 },
        { name: '文档B.txt', page: 1 }
      ],
      timestamp: new Date()
    }
    messages.value.push(aiMessage)
    isLoading.value = false
    nextTick(() => scrollToBottom())
  }, 1500)
}

const scrollToBottom = () => {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

const formatTime = (date) => {
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="chat-page">
    <!-- 头部 -->
    <div class="header">
      <div class="header-info">
        <h2 class="title">知识检索</h2>
        <p class="subtitle">基于向量数据库的智能问答</p>
      </div>
    </div>

    <!-- 对话内容区 -->
    <div class="chat-container" ref="chatContainer">
      <div v-if="messages.length === 0" class="empty-state">
        <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        <p class="empty-text">开始提问吧</p>
        <p class="empty-hint">系统会从已上传的文档中检索相关内容并回答</p>
      </div>

      <div v-else class="messages">
        <div
          v-for="message in messages"
          :key="message.id"
          class="message"
          :class="message.type"
        >
          <div class="message-content">
            <div class="message-text">{{ message.content }}</div>

            <div v-if="message.sources" class="message-sources">
              <div class="sources-title">参考来源</div>
              <div class="sources-list">
                <div
                  v-for="(source, index) in message.sources"
                  :key="index"
                  class="source-item"
                >
                  <svg class="source-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                    <polyline points="13 2 13 9 20 9" />
                  </svg>
                  <span>{{ source.name }}</span>
                </div>
              </div>
            </div>

            <div class="message-time">{{ formatTime(message.timestamp) }}</div>
          </div>
        </div>

        <div v-if="isLoading" class="message assistant">
          <div class="message-content">
            <div class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="input-section">
      <form class="input-form" @submit.prevent="sendMessage">
        <input
          v-model="inputMessage"
          type="text"
          class="message-input"
          placeholder="输入你的问题..."
          :disabled="isLoading"
        />
        <button
          type="submit"
          class="send-btn"
          :disabled="!inputMessage.trim() || isLoading"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 32px;
  gap: 24px;
}

/* 头部 */
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-info {
  display: grid;
  gap: 4px;
}

.title {
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

/* 对话容器 */
.chat-container {
  flex: 1;
  overflow-y: auto;
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 24px;
}
/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 16px;
}

.empty-icon {
  width: 64px;
  height: 64px;
  color: var(--text-secondary);
  opacity: 0.4;
}

.empty-text {
  font-size: 18px;
  font-weight: 500;
  color: var(--text-primary);
  margin: 0;
}

.empty-hint {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

/* 消息列表 */
.messages {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.message {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 用户消息：右对齐 */
.message.user {
  align-items: flex-end;
}

/* AI 消息：左对齐 */
.message.assistant {
  align-items: flex-start;
}

.message-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 75%;
}

.message-text {
  padding: 14px 16px;
  border-radius: 8px;
  font-size: 15px;
  line-height: 1.6;
  word-wrap: break-word;
}

.message.user .message-text {
  background: var(--accent);
  color: #FFFFFF;
  border: none;
}

.message.assistant .message-text {
  background: var(--surface-2);
  color: var(--text-primary);
  border: 1px solid var(--border);
}

/* 参考来源 */
.message-sources {
  display: grid;
  gap: 8px;
}

.sources-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.sources-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.source-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
}

.source-icon {
  width: 14px;
  height: 14px;
  color: var(--accent);
}

.message-time {
  font-size: 12px;
  color: var(--text-secondary);
}

/* 输入中动画 */
.typing-indicator {
  display: flex;
  gap: 6px;
  padding: 14px 16px;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-secondary);
  animation: typing 1.4s infinite;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 60%, 100% {
    opacity: 0.3;
  }
  30% {
    opacity: 1;
  }
}

/* 输入区域 */
.input-section {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}

.input-form {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  align-items: center;
}

.message-input {
  padding: 12px 16px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 15px;
  font-family: var(--font-sans);
  outline: none;
  transition: all 0.2s;
}

.message-input::placeholder {
  color: var(--text-secondary);
}

.message-input:focus {
  border-color: var(--accent);
  background: var(--surface-1);
}

.message-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-btn {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  background: var(--accent);
  border: none;
  color: #FFFFFF;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: opacity 0.2s;
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-btn svg {
  width: 20px;
  height: 20px;
}
</style>
