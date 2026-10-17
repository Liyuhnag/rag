<script setup>
import { watch, onMounted } from 'vue'

const props = defineProps({
  type: {
    type: String,
    default: 'info',
    validator: (value) => ['success', 'error', 'warning', 'info'].includes(value)
  },
  message: {
    type: String,
    required: true
  },
  show: {
    type: Boolean,
    default: false
  },
  duration: {
    type: Number,
    default: 3000
  }
})

const emit = defineEmits(['close'])

let timer = null

watch(() => props.show, (newVal) => {
  if (newVal && props.duration > 0) {
    clearTimeout(timer)
    timer = setTimeout(() => {
      emit('close')
    }, props.duration)
  }
})

onMounted(() => {
  if (props.show && props.duration > 0) {
    timer = setTimeout(() => {
      emit('close')
    }, props.duration)
  }
})

const handleClose = () => {
  clearTimeout(timer)
  emit('close')
}
</script>

<template>
  <Transition name="toast">
    <div v-if="show" class="toast-container">
      <div class="toast" :class="`toast-${type}`">
        <div class="toast-indicator" :class="`indicator-${type}`"></div>

        <div class="toast-content">
          <p class="toast-message">{{ message }}</p>
        </div>

        <button class="toast-close" @click="handleClose">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.toast-container {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
}

.toast {
  min-width: 320px;
  max-width: 480px;
  background: var(--surface-1);
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  display: grid;
  grid-template-columns: 4px 1fr auto;
  gap: 16px;
  padding: 16px;
  padding-left: 0;
}

.toast-indicator {
  border-radius: 8px 0 0 8px;
}

.indicator-success {
  background: var(--success);
}

.indicator-error {
  background: var(--error);
}

.indicator-warning {
  background: var(--warning);
}

.indicator-info {
  background: var(--info);
}

.toast-content {
  display: flex;
  align-items: center;
  min-height: 24px;
}

.toast-message {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.5;
  margin: 0;
}

.toast-close {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  align-self: start;
  padding: 0;
}

.toast-close svg {
  width: 16px;
  height: 16px;
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s;
}

.toast-enter-from {
  opacity: 0;
  transform: translateY(-20px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateY(-20px);
}
</style>
