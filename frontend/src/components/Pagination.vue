<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  currentPage: {
    type: Number,
    required: true
  },
  totalPages: {
    type: Number,
    required: true
  },
  totalItems: {
    type: Number,
    required: true
  },
  pageSize: {
    type: Number,
    default: 20
  }
})

const emit = defineEmits(['update:currentPage', 'pageChange', 'pageSizeChange'])

const pageSizeOptions = [10, 20, 50, 100]
const showPageSizeMenu = ref(false)

// 计算显示的页码范围
const pageNumbers = computed(() => {
  const pages = []
  const total = props.totalPages
  const current = props.currentPage

  if (total <= 7) {
    // 总页数 <= 7，全部显示
    for (let i = 1; i <= total; i++) {
      pages.push(i)
    }
  } else {
    // 总页数 > 7，使用省略号
    if (current <= 4) {
      // 当前页在前面
      for (let i = 1; i <= 5; i++) {
        pages.push(i)
      }
      pages.push('...')
      pages.push(total)
    } else if (current >= total - 3) {
      // 当前页在后面
      pages.push(1)
      pages.push('...')
      for (let i = total - 4; i <= total; i++) {
        pages.push(i)
      }
    } else {
      // 当前页在中间
      pages.push(1)
      pages.push('...')
      for (let i = current - 1; i <= current + 1; i++) {
        pages.push(i)
      }
      pages.push('...')
      pages.push(total)
    }
  }

  return pages
})

const goToPage = (page) => {
  if (page === '...' || page === props.currentPage) return
  if (page < 1 || page > props.totalPages) return

  emit('update:currentPage', page)
  emit('pageChange', page)
}

const prevPage = () => {
  if (props.currentPage > 1) {
    goToPage(props.currentPage - 1)
  }
}

const nextPage = () => {
  if (props.currentPage < props.totalPages) {
    goToPage(props.currentPage + 1)
  }
}

// 计算当前显示的数据范围
const startItem = computed(() => {
  return (props.currentPage - 1) * props.pageSize + 1
})

const endItem = computed(() => {
  return Math.min(props.currentPage * props.pageSize, props.totalItems)
})

const changePageSize = (size) => {
  emit('pageSizeChange', size)
  showPageSizeMenu.value = false
}

const togglePageSizeMenu = () => {
  showPageSizeMenu.value = !showPageSizeMenu.value
}
</script>

<template>
  <div class="pagination">
    <!-- 左侧信息区域 -->
    <div class="pagination-info">
      <span class="info-text">
        显示 {{ startItem }}-{{ endItem }} 条，共 {{ totalItems }} 条
      </span>

      <!-- 每页数量选择器 -->
      <div class="page-size-selector">
        <button class="page-size-btn" @click="togglePageSizeMenu">
          {{ pageSize }} 条/页
          <svg class="dropdown-icon" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" />
          </svg>
        </button>

        <div v-if="showPageSizeMenu" class="page-size-menu">
          <button
            v-for="size in pageSizeOptions"
            :key="size"
            class="page-size-option"
            :class="{ active: size === pageSize }"
            @click="changePageSize(size)"
          >
            {{ size }} 条/页
          </button>
        </div>
      </div>
    </div>

    <!-- 右侧分页控制 -->
    <div class="pagination-controls">
      <button
        class="page-btn page-arrow"
        :disabled="currentPage === 1"
        @click="prevPage"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>

      <button
        v-for="(page, index) in pageNumbers"
        :key="index"
        class="page-btn"
        :class="{ active: page === currentPage, ellipsis: page === '...' }"
        :disabled="page === '...'"
        @click="goToPage(page)"
      >
        {{ page }}
      </button>

      <button
        class="page-btn page-arrow"
        :disabled="currentPage === totalPages"
        @click="nextPage"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: var(--surface-1);
  border-top: 1px solid var(--border);
}

.pagination-info {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 14px;
  color: var(--text-secondary);
}

.info-text {
  white-space: nowrap;
}

.page-size-selector {
  position: relative;
}

.page-size-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-secondary);
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.page-size-btn:hover {
  background: var(--surface-3);
  border-color: var(--primary);
  color: var(--text-primary);
}

.dropdown-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.page-size-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  z-index: 100;
  min-width: 120px;
}

.page-size-option {
  display: block;
  width: 100%;
  padding: 10px 16px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: all 0.2s;
}

.page-size-option:hover {
  background: var(--surface-3);
  color: var(--text-primary);
}

.page-size-option.active {
  background: var(--primary);
  color: #FFFFFF;
}

.page-size-option.active:hover {
  background: var(--primary-hover);
}


.pagination-controls {
  display: flex;
  gap: 8px;
  align-items: center;
}

.page-btn {
  min-width: 36px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-secondary);
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.page-btn:hover:not(:disabled):not(.ellipsis) {
  border-color: var(--primary);
  color: var(--primary);
  background: rgba(56, 189, 248, 0.1);
}

.page-btn.active {
  border-color: var(--primary);
  background: var(--primary);
  color: #FFFFFF;
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-btn.ellipsis {
  border: none;
  background: transparent;
  cursor: default;
}

.page-arrow svg {
  width: 16px;
  height: 16px;
}
</style>
