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

const iconMap = {
  success: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
  error: 'M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z',
  warning: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z',
  info: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
}
</script>

<template>
  <Transition name="alert">
    <div v-if="show" class="alert-overlay" @click="handleClose">
      <div class="alert-container" :class="`alert-${type}`" @click.stop>
        <div class="alert-indicator" :class="`indicator-${type}`"></div>

        <div class="alert-icon" :class="`icon-${type}`">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path :d="iconMap[type]" />
          </svg>
        </div>

        <div class="alert-content">
          <p class="alert-message">{{ message }}</p>
        </div>

        <button class="alert-close" @click="handleClose">
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
.alert-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.alert-container {
  min-width: 320px;
  max-width: 480px;
  background: var(--surface-1);
  border-radius: 8px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
  display: grid;
  grid-template-columns: 4px 40px 1fr auto;
  gap: 12px;
  padding: 16px;
  padding-left: 0;
}

.alert-indicator {
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

.alert-icon {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  align-self: center;
}

.alert-icon svg {
  width: 24px;
  height: 24px;
}

.icon-success {
  color: var(--success);
}

.icon-error {
  color: var(--error);
}

.icon-warning {
  color: var(--warning);
}

.icon-info {
  color: var(--info);
}

.alert-content {
  display: flex;
  align-items: center;
  min-height: 24px;
}

.alert-message {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.5;
  margin: 0;
}

.alert-close {
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

.alert-close svg {
  width: 16px;
  height: 16px;
}

.alert-enter-active,
.alert-leave-active {
  transition: opacity 0.2s;
}

.alert-enter-from,
.alert-leave-to {
  opacity: 0;
}
</style>
