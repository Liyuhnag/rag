import { createRouter, createWebHistory } from 'vue-router'
import FileManagement from '../views/FileManagement.vue'
import Chat from '../views/Chat.vue'

const routes = [
  {
    path: '/',
    redirect: '/files'
  },
  {
    path: '/files',
    name: 'FileManagement',
    component: FileManagement,
    meta: {
      title: '文件管理'
    }
  },
  {
    path: '/chat',
    name: 'Chat',
    component: Chat,
    meta: {
      title: '知识检索'
    }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 更新页面标题
router.beforeEach((to, from, next) => {
  document.title = to.meta.title ? `${to.meta.title} - RAG 系统` : 'RAG 系统'
  next()
})

export default router
