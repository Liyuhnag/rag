<script setup>
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="logo">
        <div class="logo-icon">R</div>
        <span class="logo-text">RAG System</span>
      </div>

      <nav class="nav">
        <router-link
          to="/files"
          class="nav-item"
          :class="{ active: route.path === '/files' }"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
            <polyline points="13 2 13 9 20 9" />
          </svg>
          <span>文件管理</span>
        </router-link>

        <router-link
          to="/chat"
          class="nav-item"
          :class="{ active: route.path === '/chat' }"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <span>知识检索</span>
        </router-link>
      </nav>
    </aside>

    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<style>
:root {
  /* 深色主题色彩系统 */
  --surface-0: #05070C;
  --surface-1: #0A0D12;
  --surface-2: #0F131C;
  --surface-3: #161D2B;
  --surface-4: #1E2636;

  /* 主题色 */
  --primary: #38BDF8;
  --primary-hover: #0EA5E9;
  --primary-active: #0284C7;

  /* 文字层级 */
  --text-primary: #E2E8F0;
  --text-secondary: #94A3B8;
  --text-tertiary: #64748B;
  --text-disabled: #475569;

  /* 边框与分割线 */
  --border: rgba(255, 255, 255, 0.1);
  --divider: rgba(255, 255, 255, 0.05);

  /* 状态色 */
  --success: #10B981;
  --warning: #F59E0B;
  --error: #EF4444;
  --info: #3B82F6;

  /* 间距系统 */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;

  /* 圆角系统 */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-full: 999px;
  --radius-circle: 50%;

  /* 阴影系统 */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
  --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.4);
  --shadow-xl: 0 20px 25px rgba(0, 0, 0, 0.5);

  /* 字体系统 */
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
  --font-mono: 'SF Mono', 'Monaco', 'Cascadia Code', monospace;
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
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
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
  padding: var(--space-xl);
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: var(--space-2xl);
}

.logo-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: var(--primary);
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
  gap: var(--space-sm);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  text-decoration: none;
  transition: all 0.2s;
}

.nav-item:hover {
  background: var(--surface-2);
  color: var(--text-primary);
}

.nav-item.active {
  background: rgba(56, 189, 248, 0.15);
  color: var(--primary);
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.main-content {
  overflow-y: auto;
  background: var(--surface-0);
}

/* Scrollbar 样式 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--surface-1);
}

::-webkit-scrollbar-thumb {
  background: var(--surface-3);
  border-radius: var(--radius-sm);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--surface-4);
}
</style>
