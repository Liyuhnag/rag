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
  <div class="chat-container">
    <div class="chat-header">
      <h1 class="title">知识检索</h1>
      <p class="subtitle">基于向量数据库的智能问答</p>
    </div>

    <div class="chat-content" ref="chatContainer">
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
          <div class="message-avatar">
            <span v-if="message.type === 'user'">U</span>
            <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10" />
              <path d="M8 14s1.5 2 4 2 4-2 4-2" />
              <line x1="9" y1="9" x2="9.01" y2="9" />
              <line x1="15" y1="9" x2="15.01" y2="9" />
            </svg>
          </div>

          <div class="message-content">
            <div class="message-text">{{ message.content }}</div>

            <div v-if="message.sources" class="message-sources">
              <div class="sources-title">参考来源:</div>
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
          <div class="message-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10" />
              <path d="M8 14s1.5 2 4 2 4-2 4-2" />
              <line x1="9" y1="9" x2="9.01" y2="9" />
              <line x1="15" y1="9" x2="15.01" y2="9" />
            </svg>
          </div>
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

    <div class="chat-input-container">
      <form class="chat-input-form" @submit.prevent="sendMessage">
        <input
          v-model="inputMessage"
          type="text"
          class="chat-input"
          placeholder="输入你的问题..."
          :disabled="isLoading"
        />
        <button
          type="submit"
          class="send-button"
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
.chat-container {
  display: grid;
  grid-template-rows: auto 1fr auto;
  height: 100vh;
  max-width: 1000px;
  margin: 0 auto;
}

.chat-header {
  padding: var(--spacing-xl);
  border-bottom: 1px solid var(--border);
}

.title {
  font-size: clamp(24px, 4vw, 32px);
  font-weight: 600;
  margin-bottom: var(--spacing-sm);
  letter-spacing: -0.02em;
}

.subtitle {
  color: var(--text-secondary);
  font-size: 15px;
}

.chat-content {
  overflow-y: auto;
  padding: var(--spacing-xl);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: var(--spacing-md);
}

.empty-icon {
  width: 64px;
  height: 64px;
  color: var(--text-dim);
  opacity: 0.5;
}

.empty-text {
  font-size: 18px;
  color: var(--text-primary);
  font-weight: 500;
}

.empty-hint {
  font-size: 14px;
  color: var(--text-dim);
}

.messages {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
}

.message {
  display: grid;
  grid-template-columns: 40px 1fr;
  gap: var(--spacing-md);
}

.message-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}

.message.user .message-avatar {
  background: var(--accent);
  color: #FFFFFF;
}

.message.assistant .message-avatar {
  background: var(--surface-3);
  color: var(--accent-dim);
}

.message-avatar svg {
  width: 20px;
  height: 20px;
}

.message-content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.message-text {
  padding: var(--spacing-md);
  border-radius: var(--radius-md);
  font-size: 15px;
  line-height: 1.6;
}

.message.user .message-text {
  background: var(--accent-light);
  color: var(--text-primary);
}

.message.assistant .message-text {
  background: var(--surface-2);
  color: var(--text-primary);
  border: 1px solid var(--border);
}

.message-sources {
  margin-top: var(--spacing-sm);
}

.sources-title {
  font-size: 13px;
  color: var(--text-dim);
  margin-bottom: var(--spacing-xs);
}

.sources-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
}

.source-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  background: var(--surface-2);
  border-radius: var(--radius-full);
  font-size: 13px;
  color: var(--text-secondary);
}

.source-icon {
  width: 14px;
  height: 14px;
  color: var(--accent);
}

.message-time {
  font-size: 12px;
  color: var(--text-dim);
}

.typing-indicator {
  display: flex;
  gap: 6px;
  padding: var(--spacing-md);
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-dim);
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
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-8px);
  }
}

.chat-input-container {
  padding: var(--spacing-lg) var(--spacing-xl);
  background: transparent;
}

.chat-input-form {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--spacing-md);
  align-items: center;
}

.chat-input {
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--surface-1);
  border: 1.5px solid var(--border);
  border-radius: var(--radius-full);
  color: var(--text-primary);
  font-size: 15px;
  font-family: var(--font-sans);
  outline: none;
  transition: all 0.2s;
}

.chat-input::placeholder {
  color: var(--text-dim);
}

.chat-input:focus {
  border-color: var(--accent);
  background: var(--surface-1);
}

.chat-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-button {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--accent);
  border: none;
  color: #FFFFFF;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
}

.send-button:hover:not(:disabled) {
  background: var(--accent-dim);
  transform: scale(1.05);
}

.send-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-button svg {
  width: 20px;
  height: 20px;
}
</style>
