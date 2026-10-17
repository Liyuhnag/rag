<script setup>
import { ref } from 'vue'
import FileUpload from './components/FileUpload.vue'
import ChatInterface from './components/ChatInterface.vue'

const currentView = ref('upload')
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="logo">
        <div class="logo-icon">R</div>
        <span class="logo-text">RAG System</span>
      </div>

      <nav class="nav">
        <button
          class="nav-item"
          :class="{ active: currentView === 'upload' }"
          @click="currentView = 'upload'"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <span>文件上传</span>
        </button>

        <button
          class="nav-item"
          :class="{ active: currentView === 'chat' }"
          @click="currentView = 'chat'"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <span>聊天检索</span>
        </button>
      </nav>
    </aside>

    <main class="main-content">
      <FileUpload v-if="currentView === 'upload'" />
      <ChatInterface v-else />
    </main>
  </div>
</template>

<style>
:root {
  --surface-0: #F8FAFC;
  --surface-1: #FFFFFF;
  --surface-2: #F1F5F9;
  --surface-3: #E2E8F0;

  --accent: #38BDF8;
  --accent-light: #E0F2FE;

  --text-primary: #1E293B;
  --text-secondary: #64748B;
  --text-dim: #94A3B8;

  --border: #E2E8F0;

  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: var(--font-sans);
  background: var(--surface-0);
  color: var(--text-primary);
  line-height: 1.6;
}

.app {
  display: grid;
  grid-template-columns: 280px 1fr;
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  background: var(--surface-1);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 24px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 32px;
}

.logo-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 18px;
  color: #FFFFFF;
}

.logo-text {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: transparent;
  border: none;
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}

.nav-item.active {
  background: var(--accent-light);
  color: var(--accent);
}

.nav-icon {
  width: 20px;
  height: 20px;
}

.main-content {
  overflow-y: auto;
  background: var(--surface-0);
}
</style>
