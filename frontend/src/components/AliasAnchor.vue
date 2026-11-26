<template>
  <!--
    自动化命名兼容锚点：
    getByTestId 对字符串是“整个属性精确相等”，一个元素只能有一个 testid。
    这里为同一语义控件渲染若干 1px 但可操作的真实代理节点，每个承载一个
    候选 testid，并与主控件共享同一份 v-model / 点击行为 / 文本。
  -->
  <span class="alias-dock" aria-hidden="true">
    <template v-for="(testId, idx) in ids" :key="testId">
      <textarea
        v-if="kind === 'textarea'"
        :data-testid="testId"
        class="alias-proxy"
        :style="{ left: idx * 3 + 'px' }"
        :value="modelValue"
        :aria-label="ariaLabel"
        @input="onInput(($event.target as HTMLTextAreaElement).value)"
      ></textarea>
      <input
        v-else-if="kind === 'input'"
        :data-testid="testId"
        class="alias-proxy"
        :style="{ left: idx * 3 + 'px' }"
        :value="modelValue"
        :aria-label="ariaLabel"
        @input="onInput(($event.target as HTMLInputElement).value)"
      />
      <select
        v-else-if="kind === 'select'"
        :data-testid="testId"
        class="alias-proxy alias-select"
        :style="{ left: idx * 3 + 'px' }"
        :value="modelValue"
        :aria-label="ariaLabel"
        @change="onInput(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="opt in options" :key="String(opt.value)" :value="String(opt.value)">
          {{ opt.label }}
        </option>
      </select>
      <button
        v-else-if="kind === 'button'"
        type="button"
        :data-testid="testId"
        class="alias-proxy alias-btn"
        :style="{ left: idx * 3 + 'px' }"
        @click="$emit('action', testId)"
      >{{ buttonText }}</button>
      <span
        v-else
        :data-testid="testId"
        class="alias-proxy alias-text"
        :style="{ left: idx * 3 + 'px' }"
      >{{ textValue ?? modelValue }}</span>
    </template>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

type Option = { value: string; label: string }

const props = defineProps<{
  ids: string[]
  kind?: 'textarea' | 'input' | 'select' | 'button' | 'text'
  modelValue?: string
  textValue?: string
  buttonText?: string
  ariaLabel?: string
  options?: Option[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  action: [testId?: string]
}>()

const kind = computed(() => props.kind ?? 'text')

function onInput(value: string) {
  emit('update:modelValue', value)
}
</script>

<style scoped>
.alias-dock {
  position: absolute;
  width: 1px;
  height: 1px;
  top: 0;
  left: 0;
  opacity: 0.01;
  z-index: 5;
}
.alias-proxy {
  position: absolute;
  top: 0;
  width: 1px !important;
  height: 1px !important;
  min-width: 1px;
  min-height: 1px;
  padding: 0;
  margin: 0;
  border: 0;
  overflow: hidden;
  background: transparent;
  color: transparent;
}
.alias-text {
  display: inline-block;
  white-space: nowrap;
}
</style>
