import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------------------
// 混合报文批次受理回执
// ---------------------------------------------------------------------------

export type BatchRowInput = {
  line_no?: number
  client_line_key?: string
  protocol: string
  route: string
  raw_message: string
}

export type BatchReceiptPayload = {
  receipt_no?: string
  rows: BatchRowInput[]
  simulate_disconnect?: boolean
  [key: string]: unknown
}

export async function fetchBatchOptions(): Promise<Record<string, any>> {
  try {
    const response = await request('/api/msg_decode/batch-options')
    if (!response.ok) throw new Error('batch options failed')
    return await response.json()
  } catch {
    // 后端不可用时的兜底选项，保证页面仍可渲染。
    return {
      protocols: [
        { value: 'temperature', label: '温度协议' },
        { value: 'pressure', label: '压力协议' },
        { value: 'text', label: '文本协议' },
      ],
      routes: [
        { value: 'ROUTE-TEMP', label: '温度路由', target_topic: 'iot/temperature' },
        { value: 'ROUTE-PRES', label: '压力路由', target_topic: 'iot/pressure' },
        { value: 'ROUTE-TEXT', label: '文本路由', target_topic: 'iot/text' },
      ],
    }
  }
}

export async function submitBatchReceipt(
  payload: BatchReceiptPayload,
): Promise<{ status: number; body: any; networkLost: boolean }> {
  try {
    const response = await request('/api/msg_decode/batch-receipts', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    let body: any = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    return { status: response.status, body, networkLost: false }
  } catch (error) {
    // 请求已发出但连接在响应前断开：服务端可能已经落账，调用方应凭同一回执重提。
    return {
      status: 0,
      body: {
        ok: false,
        network_lost: true,
        message: error instanceof Error ? error.message : '连接中断',
      },
      networkLost: true,
    }
  }
}

export async function fetchReceiptDeliveries(receiptNo?: string): Promise<any[]> {
  const query = receiptNo ? `?receipt_no=${encodeURIComponent(receiptNo)}` : ''
  const response = await request(`/api/msg_decode/deliveries${query}`)
  if (!response.ok) throw new Error('送达记录读取失败')
  const body = await response.json()
  return body.deliveries ?? body.items ?? []
}
