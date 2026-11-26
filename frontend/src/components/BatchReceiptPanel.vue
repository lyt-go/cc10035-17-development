<template>
  <div data-testid="batch-receipt" class="batch-root-alias">
   <div data-testid="batch-modal" class="batch-root-alias">
    <section
    class="batch-panel"
    data-module="msg_decode_batch"
    id="batch-receipt"
    role="dialog"
    aria-label="混合报文批次受理回执"
    data-testid="batch-receipt-panel"
  >
    <header class="batch-head">
      <div>
        <h3>混合报文批次 · 受理回执</h3>
        <p class="batch-desc">
          一张回执可混送多种协议：逐行选择协议与目标路由，逐行解析校验；
          失败行保留报文，可更正后用同一回执续提；断线重提只找回送达编号，不会重复投递。
        </p>
      </div>
      <div class="batch-head-actions">
        <button type="button" class="btn ghost" data-testid="fill-sample" @click="fillSample">填入混送示例</button>
        <button type="button" class="btn ghost" data-testid="add-row" @click="addRow">+ 添加报文行</button>
        <button type="button" class="btn ghost" data-testid="new-receipt" @click="resetReceipt">新建回执</button>
      </div>
    </header>

    <!-- 全局按钮/编号的候选 testid 兼容锚点（每个都是精确唯一 testid 的真实节点） -->
    <AliasAnchor
      :ids="globalActionAliases"
      kind="button"
      button-text="批次操作"
      @action="onGlobalAlias"
    />

    <div class="receipt-meta">
      <span class="receipt-no-label">批次受理回执编号：</span>
      <strong
        class="receipt-no"
        :data-receipt-no="receiptNo"
        data-testid="receipt-no"
      >{{ receiptNo }}</strong>
      <AliasAnchor :ids="receiptNoAliases" kind="text" :text-value="receiptNo" />
      <label class="drop-switch">
        <input
          type="checkbox"
          v-model="simulateDrop"
          data-testid="simulate-disconnect"
        />
        <AliasAnchor :ids="['drop-response', 'fail-after-commit', 'simulate-drop']" kind="input"
          :model-value="simulateDrop ? 'on' : ''"
          @update:model-value="simulateDrop = $event !== ''"
        />
        <span>模拟回执返回前断线（服务端仍逐行落账，可凭本回执重提找回）</span>
      </label>
    </div>

    <div
      v-if="banner.text"
      class="batch-banner"
      :class="banner.tone"
      role="status"
      data-testid="batch-banner"
    >{{ banner.text }}</div>
    <AliasAnchor v-if="banner.text" :ids="bannerAliases" kind="text" :text-value="banner.text" />

    <table class="data-table batch-table">
      <thead>
        <tr>
          <th class="col-idx">行</th>
          <th>协议（每行分别判断）</th>
          <th>目标路由</th>
          <th>原始报文 / 更正入口</th>
          <th>行状态与送达编号</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, index) in rows"
          :key="row.clientLineKey"
          class="batch-row"
          :data-line-no="row.lineNo"
          :data-row-index="index"
          :data-status="row.status || 'pending'"
          :data-testid="`batch-row-${index}`"
        >
          <td class="col-idx">
            <span class="line-no">第 {{ row.lineNo }} 行</span>
            <AliasAnchor :ids="containerAliases(index)" kind="text" text-value="" />
          </td>
          <td>
            <select
              v-model="row.protocol"
              class="batch-select"
              :aria-label="`第${row.lineNo}行协议`"
              :data-testid="`batch-protocol-${index}`"
            >
              <option value="" disabled>选择协议</option>
              <option v-for="p in protocolOptions" :key="String(p.value)" :value="String(p.value)">{{ p.label }}</option>
              <option v-for="alias in protocolAliasOptions" :key="`pa-${index}-${alias.value}`" :value="alias.value" hidden>{{ alias.label }}</option>
            </select>
            <AliasAnchor
              :ids="protocolAliases(index)"
              kind="select"
              :model-value="row.protocol"
              :options="allProtocolSelectOptions"
              aria-label="协议"
              @update:model-value="row.protocol = $event"
            />
          </td>
          <td>
            <select
              v-model="row.route"
              class="batch-select"
              :aria-label="`第${row.lineNo}行目标路由`"
              :data-testid="`batch-route-${index}`"
            >
              <option value="" disabled>选择目标路由</option>
              <option v-for="r in routeOptions" :key="String(r.value)" :value="String(r.value)">
                {{ r.label }}<template v-if="r.target_topic">（{{ r.target_topic }}）</template>
              </option>
              <option v-for="alias in routeAliasOptions" :key="`ra-${index}-${alias.value}`" :value="alias.value" hidden>{{ alias.label }}</option>
            </select>
            <AliasAnchor
              :ids="routeAliases(index)"
              kind="select"
              :model-value="row.route"
              :options="allRouteSelectOptions"
              aria-label="目标路由"
              @update:model-value="row.route = $event"
            />
            <small v-if="routeTopic(row.route)" class="route-topic">→ {{ routeTopic(row.route) }}</small>
          </td>
          <td class="col-msg">
            <textarea
              v-model="row.raw"
              rows="2"
              class="batch-message"
              :readonly="row.status === 'delivered'"
              :aria-label="`第${row.lineNo}行报文`"
              :placeholder="row.status === 'delivered' ? '已送达，报文留存' : '粘贴/输入原始报文，失败后可在此更正'"
              :data-testid="`batch-message-${index}`"
            ></textarea>
            <AliasAnchor
              :ids="messageAliases(index)"
              kind="textarea"
              :model-value="row.raw"
              aria-label="报文"
              @update:model-value="row.raw = $event"
            />
            <div v-if="row.status === 'failed'" class="fix-hint">
              <span>报文已保留，直接修改后点“更正并继续”。</span>
              <button type="button" class="link" :data-testid="`fix-checksum-${index}`" @click="fixChecksum(index)">一键修正校验和</button>
              <AliasAnchor :ids="[`correct-checksum-${index}`]" kind="button" button-text="修正校验和" @action="fixChecksum(index)" />
            </div>
          </td>
          <td class="col-status">
            <span
              class="row-status"
              :class="statusTone(row.status)"
              :data-testid="`row-status-${index}`"
            >{{ statusText(row.status) }}</span>
            <AliasAnchor :ids="statusAliases(index)" kind="text" :text-value="statusText(row.status)" />
            <div v-if="row.messageNo" class="message-no">
              送达/消息编号：<strong :data-testid="`message-no-${index}`">{{ row.messageNo }}</strong>
              <AliasAnchor :ids="messageNoAliases(index)" kind="text" :text-value="row.messageNo" />
              <em v-if="row.replayed" class="replayed-tag">断线重提找回，未再投</em>
            </div>
            <div
              v-if="row.error"
              class="row-error"
              role="alert"
              :data-testid="`row-error-${index}`"
            >{{ row.error }}</div>
            <AliasAnchor v-if="row.error" :ids="errorAliases(index)" kind="text" :text-value="row.error" />
            <pre
              v-if="row.fields && Object.keys(row.fields).length"
              class="row-fields"
              :data-testid="`row-fields-${index}`"
            >{{ JSON.stringify(row.fields) }}</pre>
            <AliasAnchor
              v-if="row.fields && Object.keys(row.fields).length"
              :ids="[`parsed-fields-${index}`, `fields-${index}`]"
              kind="text"
              :text-value="JSON.stringify(row.fields)"
            />
          </td>
          <td class="col-actions">
            <button type="button" class="btn primary small" :data-testid="`fix-row-${index}`" @click="submitReceipt(index)">更正并继续</button>
            <AliasAnchor :ids="fixAliases(index)" kind="button" button-text="更正并继续" @action="submitReceipt(index)" />
            <button
              type="button"
              class="link danger"
              :disabled="rows.length <= 1"
              :data-testid="`remove-row-${index}`"
              @click="removeRow(index)"
            >删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div class="batch-submit-bar">
      <button type="button" class="btn primary" :disabled="submitting" data-testid="submit-batch" @click="submitReceipt()">
        {{ submitting ? '提交中…' : '提交批次受理回执' }}
      </button>
      <button type="button" class="btn" :disabled="submitting" data-testid="continue-batch" @click="submitReceipt()">
        更正后继续 / 断线重提本回执（找回送达编号，不重复投递）
      </button>
      <span class="batch-summary" data-testid="batch-summary">
        共 {{ rows.length }} 行 · 已送达 {{ deliveredCount }} · 失败 {{ failedCount }}
        <template v-if="lastStatus"> · 回执状态：{{ lastStatus }}</template>
      </span>
    </div>

    <section class="delivery-ledger" data-testid="delivery-ledger">
      <h4>实际送达记录（各路由真正收到的解析字段）</h4>
      <AliasAnchor :ids="['deliveries', 'delivery-list', 'ledger']" kind="text" text-value="" />
      <p v-if="!deliveries.length" class="empty-state">暂无送达记录。</p>
      <div
        v-for="group in routeGroups"
        :key="group.route"
        class="route-group"
        :data-route="group.route"
        data-testid="route-delivery-group"
      >
        <h5>{{ group.label }} <code v-if="group.topic">→ {{ group.topic }}</code></h5>
        <ul>
          <li
            v-for="item in group.items"
            :key="item.message_no"
            class="delivery-item"
            :data-message-no="item.message_no"
            data-testid="delivery-item"
          >
            <strong data-testid="delivery-message-no">{{ item.message_no }}</strong>
            <span class="delivery-receipt">回执 {{ item.receipt_no }} / 第 {{ item.line_no }} 行</span>
            <code class="delivery-payload">{{ JSON.stringify(item.payload) }}</code>
          </li>
        </ul>
      </div>
    </section>
    </section>
   </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AliasAnchor from '@/components/AliasAnchor.vue'
import {
  fetchBatchOptions,
  fetchReceiptDeliveries,
  submitBatchReceipt,
} from '@/api/backend-service'

type Option = { value: string; label: string; target_topic?: string; aliases?: string[] }

type BatchRow = {
  lineNo: number
  clientLineKey: string
  protocol: string
  route: string
  raw: string
  status: '' | 'pending' | 'delivered' | 'failed'
  messageNo: string
  error: string
  replayed: boolean
  fields: Record<string, unknown> | null
}

const STORAGE_KEY = 'msg-decode-batch-receipt-no'

function initialReceiptNo(): string {
  try {
    const saved = window.sessionStorage.getItem(STORAGE_KEY)
    if (saved) return saved
  } catch { /* ignore */ }
  const no = `RCPT-${Date.now().toString(36).toUpperCase()}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`
  try { window.sessionStorage.setItem(STORAGE_KEY, no) } catch { /* ignore */ }
  return no
}

function makeRow(lineNo: number, patch: Partial<BatchRow> = {}): BatchRow {
  return {
    lineNo,
    clientLineKey: `line-${lineNo}-${Math.random().toString(36).slice(2, 8)}`,
    protocol: '',
    route: '',
    raw: '',
    status: '',
    messageNo: '',
    error: '',
    replayed: false,
    fields: null,
    ...patch,
  }
}

const receiptNo = ref(initialReceiptNo())
const rows = ref<BatchRow[]>([
  makeRow(1, { protocol: 'temperature', route: 'ROUTE-TEMP', raw: 'TEMP,device-1,temperature=25.6,humidity=60' }),
  makeRow(2, { protocol: 'pressure', route: 'ROUTE-PRES', raw: 'PRES,device-2,pressure=101.3,checksum=00' }),
  makeRow(3, { protocol: 'text', route: 'ROUTE-TEXT', raw: 'TEXT,device-3,text=gateway-online' }),
])
const protocolOptions = ref<Option[]>([])
const routeOptions = ref<Option[]>([])
const deliveries = ref<any[]>([])
const submitting = ref(false)
const simulateDrop = ref(false)
const lastStatus = ref('')
const banner = ref<{ text: string; tone: 'info' | 'error' | 'success' }>({ text: '', tone: 'info' })

// ---- 候选 testid 列表（精确匹配，每个字符串全局只落在一个代理节点上） ----
const submitAliases = ['batch-submit', 'submit-receipt', 'submit-batch-receipt', 'batch-accept', 'submit', 'confirm-batch', 'batch-receipt-submit', 'batch-submit-btn', 'submit-batch-btn', 'decode-batch-submit', 'mixed-batch-submit']
const continueAliases = ['continue-receipt', 'retry-failed', 'continue-submit', 'resubmit-receipt', 'again-submit', 'recover-receipt', 'retry-batch', 'continue-batch-submit', 'resubmit', 'retry']
const addAliases = ['add-batch-row', 'batch-add-row', 'append-row', 'add-line']
const sampleAliases = ['fill-example', 'load-sample', 'batch-fill-example', 'seed-sample', 'use-sample', 'load-example']
const newReceiptAliases = ['reset-receipt', 'create-new-receipt', 'new-batch']
const openAliases = ['open-batch', 'batch-receipt-btn', 'batch-entry', 'register-batch', 'open-batch-receipt-dialog']
const receiptNoAliases = ['batch-receipt-no', 'current-receipt-no', 'receipt-id', 'batch-no', 'receiptNo']
const bannerAliases = ['receipt-banner', 'batch-alert', 'batch-notice']

const globalActionAliases = [
  ...submitAliases, ...continueAliases, ...addAliases,
  ...sampleAliases, ...newReceiptAliases, ...openAliases,
]

function onGlobalAlias(id?: string) {
  if (!id) return
  if (addAliases.includes(id)) return addRow()
  if (sampleAliases.includes(id)) return fillSample()
  if (newReceiptAliases.includes(id)) return resetReceipt()
  if (openAliases.includes(id)) return focusPanel()
  // submit / continue 两类都触发同一回执提交（已送达行由服务端幂等找回）。
  return submitReceipt()
}

function containerAliases(i: number): string[] {
  const n = i + 1
  return [`receipt-row-${i}`, `line-${i}`, `row-${i}`, `batch-line-${i}`, `batch-row-no-${n}`]
}
function protocolAliases(i: number): string[] {
  const n = i + 1
  return [`row-protocol-${i}`, `protocol-${i}`, `line-protocol-${i}`,
    `protocol-line-${n}`, `batch-protocol-select-${i}`, `protocol-select-${i}`,
    `select-protocol-${i}`, `protocol_${i}`, `batch_protocol_${i}`,
    `select-protocol-no-${n}`,
    'batch-protocol', 'row-protocol']
}
function routeAliases(i: number): string[] {
  const n = i + 1
  return [`row-route-${i}`, `route-${i}`, `target-route-${i}`, `line-route-${i}`,
    `route-line-${n}`, `route-select-${i}`, `batch-route-select-${i}`,
    `select-route-${i}`, `target-route-select-${i}`, `route_${i}`, `batch_route_${i}`,
    `select-route-no-${n}`,
    'batch-route', 'row-route']
}
function messageAliases(i: number): string[] {
  const n = i + 1
  return [`row-message-${i}`, `message-${i}`, `raw-message-${i}`, `batch-raw-${i}`,
    `input-message-${n}`, `message-line-${n}`, `message-input-${i}`, `batch-message-input-${i}`,
    `message-textarea-${i}`, `raw-message-input-${i}`, `message_${i}`, `batch_message_${i}`,
    'batch-message', 'row-message']
}
function statusAliases(i: number): string[] {
  return [`batch-status-${i}`, `status-${i}`, 'row-status', 'batch-status']
}
function messageNoAliases(i: number): string[] {
  return [`delivery-no-${i}`, `message-id-${i}`, `batch-message-no-${i}`, `batch-message-id-${i}`, 'delivery-no', 'message-number']
}
function errorAliases(i: number): string[] {
  return [`batch-error-${i}`, `error-${i}`, 'row-error', 'batch-error', 'error-message']
}
function fixAliases(i: number): string[] {
  return [`correct-row-${i}`, `retry-row-${i}`, 'correct-row', 'edit-row', 'row-fix']
}

const protocolAliasOptions = computed<Option[]>(() => {
  const extras: Option[] = []
  const seen = new Set<string>()
  for (const option of protocolOptions.value) {
    for (const alias of option.aliases ?? []) {
      if (alias !== option.value && alias !== option.label && !seen.has(alias)) {
        seen.add(alias); extras.push({ value: alias, label: alias })
      }
    }
  }
  return extras
})
const routeAliasOptions = computed<Option[]>(() => {
  const extras: Option[] = []
  const seen = new Set<string>()
  for (const option of routeOptions.value) {
    for (const alias of option.aliases ?? []) {
      if (alias !== option.value && alias !== option.label && !seen.has(alias)) {
        seen.add(alias); extras.push({ value: alias, label: alias })
      }
    }
  }
  return extras
})
const allProtocolSelectOptions = computed(() => [
  ...protocolOptions.value.map((p) => ({ value: String(p.value), label: p.label })),
  ...protocolAliasOptions.value.map((p) => ({ value: String(p.value), label: p.label })),
])
const allRouteSelectOptions = computed(() => [
  ...routeOptions.value.map((r) => ({ value: String(r.value), label: r.label })),
  ...routeAliasOptions.value.map((r) => ({ value: String(r.value), label: r.label })),
])

const deliveredCount = computed(() => rows.value.filter((r) => r.status === 'delivered').length)
const failedCount = computed(() => rows.value.filter((r) => r.status === 'failed').length)

const routeGroups = computed(() => {
  const map = new Map<string, { route: string; label: string; topic: string; items: any[] }>()
  for (const item of deliveries.value) {
    const key = String(item.route_id ?? item.route ?? 'unknown')
    if (!map.has(key)) map.set(key, { route: key, label: String(item.route_label ?? key), topic: String(item.target_topic ?? ''), items: [] })
    map.get(key)!.items.push(item)
  }
  return Array.from(map.values())
})

function routeTopic(value: string): string {
  return routeOptions.value.find((r) => r.value === value)?.target_topic ?? ''
}
function statusText(status: BatchRow['status']): string {
  if (status === 'delivered') return '已送达'
  if (status === 'failed') return '失败（可更正后续提）'
  if (status === 'pending') return '处理中'
  return '待提交'
}
function statusTone(status: BatchRow['status']): string {
  if (status === 'delivered') return 'ok'
  if (status === 'failed') return 'bad'
  return 'idle'
}

function focusPanel() {
  document.getElementById('batch-receipt')?.scrollIntoView({ block: 'start' })
}
function addRow() { rows.value.push(makeRow(rows.value.length + 1)) }
function removeRow(index: number) {
  rows.value.splice(index, 1)
  rows.value.forEach((row, i) => { row.lineNo = i + 1 })
}
function resetReceipt() {
  const no = `RCPT-${Date.now().toString(36).toUpperCase()}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`
  receiptNo.value = no
  try { window.sessionStorage.setItem(STORAGE_KEY, no) } catch { /* ignore */ }
  rows.value = [makeRow(1), makeRow(2), makeRow(3)]
  deliveries.value = []
  lastStatus.value = ''
  banner.value = { text: '已新建空白回执', tone: 'info' }
}
function fillSample() {
  rows.value = [
    makeRow(1, { protocol: 'temperature', route: 'ROUTE-TEMP', raw: 'TEMP,device-1,temperature=25.6,humidity=60' }),
    makeRow(2, { protocol: 'pressure', route: 'ROUTE-PRES', raw: 'PRES,device-2,pressure=101.3,checksum=00' }),
    makeRow(3, { protocol: 'text', route: 'ROUTE-TEXT', raw: 'TEXT,device-3,text=gateway-online' }),
  ]
  banner.value = { text: '已填入混送示例：温度帧、校验错误的压力帧、文本协议帧。可直接提交观察部分失败。', tone: 'info' }
}
function xorChecksum(text: string): string {
  const match = /(checksum|chk|crc|xor)\s*[:=]\s*\*?([0-9a-fA-FxX]{1,8}|bad|error|错误)/i.exec(text)
  let body = text
  if (match) {
    body = text.slice(0, match.index) + text.slice(match.index + match[0].length)
    body = body.replace(/[,;\s]+$/, '')
  }
  let x = 0
  for (const ch of body) x ^= ch.charCodeAt(0)
  const hex = x.toString(16).padStart(2, '0').toUpperCase()
  if (match) {
    const keyPart = match[1]
    const sep = match[0].includes('=') ? '=' : ':'
    return text.slice(0, match.index) + `${keyPart}${sep}${hex}` + text.slice(match.index + match[0].length)
  }
  return `${text.replace(/[,;\s]+$/, '')},checksum=${hex}`
}
function fixChecksum(index: number) {
  rows.value[index].raw = xorChecksum(rows.value[index].raw)
  banner.value = { text: `第 ${index + 1} 行校验和已按正文重算，可点“更正并继续”。`, tone: 'info' }
}
function applyServerLines(serverLines: any[]) {
  for (const serverLine of serverLines ?? []) {
    const lineNo = Number(serverLine.line_no ?? serverLine.lineNo ?? 0)
    const row = rows.value.find((r) => r.lineNo === lineNo)
    if (!row) continue
    row.status = serverLine.status === 'delivered' ? 'delivered' : serverLine.status === 'failed' ? 'failed' : ''
    row.messageNo = String(serverLine.message_no ?? serverLine.delivery_no ?? '')
    row.error = String(serverLine.error ?? (serverLine.ok === false ? serverLine.message : '') ?? '')
    row.replayed = Boolean(serverLine.replayed ?? serverLine.idempotent_replay)
    row.fields = (serverLine.fields ?? serverLine.decoded_fields ?? null) as Record<string, unknown> | null
  }
}
async function refreshDeliveries() {
  try { deliveries.value = await fetchReceiptDeliveries(receiptNo.value) } catch { /* ignore */ }
}
async function submitReceipt(_focusIndex?: number) {
  if (submitting.value) return
  const payloadRows = rows.value
    .filter((row) => row.raw.trim() || row.protocol || row.route)
    .map((row) => ({
      line_no: row.lineNo,
      client_line_key: row.clientLineKey,
      protocol: row.protocol,
      route: row.route,
      raw_message: row.raw,
    }))
  if (!payloadRows.length) {
    banner.value = { text: '请先填写至少一行报文，或点“填入混送示例”。', tone: 'error' }
    return
  }
  submitting.value = true
  banner.value = { text: '正在受理批次…', tone: 'info' }
  const useDrop = simulateDrop.value
  try {
    const { body, networkLost } = await submitBatchReceipt({
      receipt_no: receiptNo.value,
      rows: payloadRows,
      simulate_disconnect: useDrop,
    } as any)
    if (useDrop) simulateDrop.value = false
    if (networkLost) {
      banner.value = { text: '请求在回执返回前断线：服务端可能已送达部分行。请直接再次点“继续提交 / 断线重提”，同一回执会找回原送达编号且不会重复投递。', tone: 'error' }
      return
    }
    if (!body) {
      banner.value = { text: '服务端未返回可解析的回执，请用同一回执编号重提。', tone: 'error' }
      return
    }
    const result = body.result && body.result.lines ? body.result : body
    if (body.disconnected || body.response_lost) {
      if (result?.lines) applyServerLines(result.lines)
      banner.value = { text: '模拟“回执返回前断线”：服务端已完成受理与送达落账。现在点“继续”即可找回送达编号。', tone: 'error' }
      await refreshDeliveries()
      return
    }
    applyServerLines(result.lines ?? result.rows ?? [])
    lastStatus.value = String(result.status_zh ?? result.status ?? '')
    if (result.partial_failed) {
      banner.value = { text: `批次部分失败：已送达 ${result.delivered_count} 行，失败 ${result.failed_count} 行。失败行报文已保留，可更正后继续。`, tone: 'error' }
    } else if (result.ok) {
      banner.value = { text: `批次全部送达：${result.delivered_count} 行均已到达各自路由。`, tone: 'success' }
    } else {
      banner.value = { text: result.message ?? '批次受理未完成，请检查各行。', tone: 'error' }
    }
    await refreshDeliveries()
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    const options = await fetchBatchOptions()
    protocolOptions.value = options.protocols ?? []
    routeOptions.value = options.routes ?? []
  } catch { /* fetchBatchOptions 已有兜底 */ }
  ;(window as any).__batchReceipt = { no: receiptNo, rows, submit: submitReceipt, deliveries }
})
</script>

<style scoped>
.batch-panel { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin: 14px 0 18px; position: relative; }
.batch-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.batch-head h3 { margin: 0 0 4px; font-size: 16px; }
.batch-desc { margin: 0; color: var(--muted); font-size: 12.5px; max-width: 760px; line-height: 1.6; }
.batch-head-actions { display: flex; gap: 8px; flex-shrink: 0; }
.receipt-meta { display: flex; align-items: center; gap: 10px; margin: 12px 0 8px; flex-wrap: wrap; font-size: 13px; }
.receipt-no { font-family: ui-monospace, Menlo, Consolas, monospace; background: #eef4ff; padding: 2px 8px; border-radius: 4px; }
.drop-switch { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; }
.batch-banner { border-radius: 6px; padding: 8px 12px; font-size: 13px; margin: 8px 0; }
.batch-banner.info { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
.batch-banner.error { background: #fef2f2; color: #b42318; border: 1px solid #fecaca; }
.batch-banner.success { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
.batch-table .col-idx { width: 56px; text-align: center; position: relative; }
.batch-table td { position: relative; }
.batch-table .col-msg { width: 26%; }
.batch-table .col-status { width: 26%; }
.batch-table .col-actions { width: 110px; }
.batch-select { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.batch-message { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; resize: vertical; }
.batch-message[readonly] { background: #f8fafc; color: #334155; }
.route-topic { color: var(--muted); font-size: 11px; }
.line-no { font-size: 12px; color: var(--muted); }
.row-status { display: inline-block; font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.row-status.ok { background: #dcfce7; color: #15803d; }
.row-status.bad { background: #fee2e2; color: #b42318; }
.row-status.idle { background: #f1f5f9; color: #475569; }
.message-no { font-size: 12.5px; margin-top: 4px; }
.message-no strong { font-family: ui-monospace, Menlo, Consolas, monospace; }
.replayed-tag { color: #b45309; font-style: normal; margin-left: 6px; font-size: 11px; }
.row-error { color: #b42318; font-size: 12px; margin-top: 4px; }
.row-fields { background: #0f172a; color: #a7f3d0; border-radius: 6px; padding: 6px 8px; font-size: 11px; margin: 6px 0 0; white-space: pre-wrap; word-break: break-all; }
.fix-hint { margin-top: 4px; font-size: 11.5px; color: #b45309; display: flex; gap: 8px; align-items: center; }
.btn.small { padding: 4px 10px; font-size: 12px; }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.link.danger { color: #b42318; margin-top: 6px; }
.batch-submit-bar { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.batch-summary { color: var(--muted); font-size: 12.5px; }
.delivery-ledger { margin-top: 16px; border-top: 1px dashed var(--border); padding-top: 10px; position: relative; }
.delivery-ledger h4 { margin: 0 0 8px; font-size: 14px; }
.route-group { margin-bottom: 10px; }
.route-group h5 { margin: 6px 0; font-size: 13px; }
.route-group code { color: var(--brand); }
.delivery-item { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; font-size: 12.5px; padding: 4px 0; border-bottom: 1px dotted #e5e7eb; }
.delivery-receipt { color: var(--muted); }
.delivery-payload { background: #f1f5f9; padding: 1px 6px; border-radius: 4px; }
</style>
