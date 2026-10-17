<script setup>
import IconWarning from '../icons/IconWarning.vue'
import IconError from '../icons/IconError.vue'
import IconInfo from '../icons/IconInfo.vue'

const props = defineProps({
  title: {
    type: String,
    default: '确认操作'
  },
  message: {
    type: String,
    required: true
  },
  show: {
    type: Boolean,
    default: false
  },
  confirmText: {
    type: String,
    default: '确认'
  },
  cancelText: {
    type: String,
    default: '取消'
  },
  type: {
    type: String,
    default: 'warning',
    validator: (value) => ['warning', 'danger', 'info'].includes(value)
  }
})

const emit = defineEmits(['confirm', 'cancel'])

const handleConfirm = () => {
  emit('confirm')
}

const handleCancel = () => {
  emit('cancel')
}

const iconComponents = {
  warning: IconWarning,
  danger: IconError,
  info: IconInfo
}
</script>

<template>
  <Transition name="confirm">
    <div v-if="show" class="confirm-overlay" @click="handleCancel">
      <div class="confirm-dialog" @click.stop>
        <component :is="iconComponents[type]" class="confirm-icon" :class="`icon-${type}`" />

        <div class="confirm-content">
          <h3 class="confirm-title">{{ title }}</h3>
          <p class="confirm-message">{{ message }}</p>
        </div>

        <div class="confirm-actions">
          <button class="btn-cancel" @click="handleCancel">{{ cancelText }}</button>
          <button class="btn-confirm" :class="`btn-${type}`" @click="handleConfirm">
            {{ confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.confirm-dialog {
  width: 400px;
  background: var(--surface-1);
  border-radius: 12px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
  padding: 32px 24px 24px;
  display: grid;
  gap: 24px;
}

.confirm-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto;
  border-radius: 50%;
}

.icon-warning {
  background: rgba(245, 158, 11, 0.1);
  color: var(--warning);
}

.icon-danger {
  background: rgba(239, 68, 68, 0.1);
  color: var(--error);
}

.icon-info {
  background: rgba(59, 130, 246, 0.1);
  color: var(--info);
}

.confirm-content {
  text-align: center;
  display: grid;
  gap: 8px;
}

.confirm-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.confirm-message {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0;
}

.confirm-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.btn-cancel,
.btn-confirm {
  padding: 10px 24px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: none;
}

.btn-cancel {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
}

.btn-confirm {
  color: #FFFFFF;
}

.btn-warning {
  background: var(--warning);
}

.btn-danger {
  background: var(--error);
}

.btn-info {
  background: var(--info);
}

.confirm-enter-active,
.confirm-leave-active {
  transition: opacity 0.2s;
}

.confirm-enter-from,
.confirm-leave-to {
  opacity: 0;
}

.confirm-enter-active .confirm-dialog,
.confirm-leave-active .confirm-dialog {
  transition: transform 0.2s;
}

.confirm-enter-from .confirm-dialog,
.confirm-leave-to .confirm-dialog {
  transform: scale(0.9);
}
</style>
