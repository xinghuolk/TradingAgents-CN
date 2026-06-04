/**
 * 缓存管理 API
 */
import request from '@/api/request'

/**
 * 缓存统计数据
 */
export interface CacheStats {
  totalFiles: number
  totalSize: number
  maxSize: number
  stockDataCount: number
  newsDataCount: number
  analysisDataCount: number
}

/**
 * 缓存详情项
 */
export interface CacheDetailItem {
  type: string
  symbol: string
  size: number
  created_at: string
  last_accessed: string
  hit_count: number
}

/**
 * 缓存详情响应
 */
export interface CacheDetailsResponse {
  items: CacheDetailItem[]
  total: number
  page: number
  page_size: number
}

/**
 * 缓存后端信息
 */
export interface CacheBackendInfo {
  system: string
  primary_backend: string
  fallback_enabled: boolean
  mongodb_available?: boolean
  redis_available?: boolean
}

/**
 * 获取缓存统计
 */
export function getCacheStats() {
  return request<CacheStats>({
    url: '/api/cache/stats',
    method: 'get'
  })
}

/**
 * 清理过期缓存
 * @param days 清理多少天前的缓存
 */
export function cleanupOldCache(days: number) {
  return request({
    url: '/api/cache/cleanup',
    method: 'delete',
    params: { days }
  })
}

/**
 * 清空所有缓存
 */
export function clearAllCache() {
  return request({
    url: '/api/cache/clear',
    method: 'delete'
  })
}

/**
 * 获取缓存详情列表
 * @param page 页码
 * @param pageSize 每页数量
 */
export function getCacheDetails(page: number = 1, pageSize: number = 20) {
  return request<CacheDetailsResponse>({
    url: '/api/cache/details',
    method: 'get',
    params: { page, page_size: pageSize }
  })
}

/**
 * 获取缓存后端信息
 */
export function getCacheBackendInfo() {
  return request<CacheBackendInfo>({
    url: '/api/cache/backend-info',
    method: 'get'
  })
}

/**
 * 财报缓存清理结果
 */
export interface FinancialReportPurgeResult {
  deleted_files: number
  freed_bytes: number
  root: string
}

/**
 * 财报提取器路径状态（仅取展示用的两个目录）
 */
export interface FinancialReportPaths {
  pdf_root: string
  extractor_cache_root: string
  pdf_container_root: string
  extractor_cache_container_root: string
}

/**
 * 读取财报提取器配置状态（用于在确认弹窗中展示真实目录路径）
 */
export function getFinancialReportPaths() {
  return request<FinancialReportPaths>({
    url: '/api/config/financial-report/status',
    method: 'get'
  })
}

/**
 * 清理财报 LLM extractor 抽取缓存
 */
export function clearFinancialReportExtractorCache() {
  return request<FinancialReportPurgeResult>({
    url: '/api/cache/financial-report/extractor',
    method: 'delete'
  })
}

/**
 * 清理已下载的财报 PDF
 */
export function clearFinancialReportPdfs() {
  return request<FinancialReportPurgeResult>({
    url: '/api/cache/financial-report/pdfs',
    method: 'delete'
  })
}

