import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js'
import { createModelAnimationClip } from './model-animation.js'

const PAGE_SIZE = 100
const MANIFEST_VIRTUAL_DIR = '__manifest_assets__'
const AUDIO_DIALOG_SCOPE = 'audioDialog'
const WWISE_SCOPE = 'wwise'
const ROUTE_SELECTION_PARAMS = [
  'fileId',
  'previewUrl',
  'selectionKey',
  'modelManifestId',
  'modelAssetIndex',
  'animationAssetIndex',
  'avatarPlanManifestId',
  'avatarPlanAssetIndex',
  'lod',
]

const state = {
  scope: 'effective',
  path: '',
  page: 1,
  pageInfo: { page: 1, pages: 1, total: 0 },
  audioLanguage: 'chinese',
  selectedFileId: null,
  selectedFileKey: null,
  routeSelection: null,
  availableScopes: new Set(),
  disposeModelViewer: null,
  modelTaskId: null,
  modelTaskSerial: 0,
}

const scopeNames = {
  effective: 'Effective',
  Persistent: 'Persistent',
  StreamingAssets: 'StreamingAssets',
  all: 'All Sources',
  audioDialog: 'AudioDialog',
  wwise: 'Wwise Audio',
}

const $ = (id) => document.getElementById(id)

function normalizeDirectoryPath(value) {
  return String(value || '').replaceAll('\\', '/').replace(/^\/+|\/+$/g, '')
}

function normalizePreviewUrl(value) {
  if (!value) return null
  const url = new URL(value, window.location.origin)
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) return null
  return `${url.pathname}${url.search}`
}

function parsePositiveInteger(value, fallback = 1) {
  const parsed = Number.parseInt(value, 10)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function clearRouteSelectionParams(params) {
  ROUTE_SELECTION_PARAMS.forEach((key) => params.delete(key))
}

function writeRoute({ replace = false } = {}) {
  const url = new URL(window.location.href)
  url.searchParams.set('scope', state.scope)
  if (state.path) url.searchParams.set('path', state.path)
  else url.searchParams.delete('path')
  if (state.page > 1) url.searchParams.set('page', String(state.page))
  else url.searchParams.delete('page')
  if (state.scope === AUDIO_DIALOG_SCOPE) {
    url.searchParams.set('audioLanguage', state.audioLanguage)
  } else {
    url.searchParams.delete('audioLanguage')
  }

  clearRouteSelectionParams(url.searchParams)
  const selection = state.routeSelection
  if (selection) {
    if (
      selection.fileKey
      && (selection.kind === 'model' || selection.kind === 'avatarPlan')
    ) {
      url.searchParams.set('selectionKey', selection.fileKey)
    }
    if (selection.kind === 'file') {
      url.searchParams.set('fileId', String(selection.fileId))
    } else if (selection.kind === 'virtual') {
      url.searchParams.set('previewUrl', selection.previewUrl)
    } else if (selection.kind === 'model') {
      url.searchParams.set('modelManifestId', selection.manifestId)
      url.searchParams.set('modelAssetIndex', selection.assetIndex)
      if (selection.lod != null) url.searchParams.set('lod', selection.lod)
      if (selection.animationAssetIndex != null) {
        url.searchParams.set('animationAssetIndex', String(selection.animationAssetIndex))
      }
    } else if (selection.kind === 'avatarPlan') {
      url.searchParams.set('avatarPlanManifestId', selection.manifestId)
      url.searchParams.set('avatarPlanAssetIndex', selection.assetIndex)
      url.searchParams.set('lod', selection.lod)
    }
  }

  const method = replace ? 'replaceState' : 'pushState'
  window.history[method](null, '', url)
}

async function navigateDirectory({ scope, path, page, audioLanguage } = {}) {
  if (scope != null) state.scope = scope
  if (path != null) state.path = normalizeDirectoryPath(path)
  if (page != null) state.page = page
  if (audioLanguage != null) state.audioLanguage = audioLanguage
  $('scopeSelect').value = state.scope
  $('audioLanguageSelect').value = state.audioLanguage
  syncScopeControls()
  clearPreviewSelection()
  writeRoute()
  await loadDirectory()
}

function formatBytes(value) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = Number(value || 0)
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`
}

function formatInt(value) {
  return Number(value || 0).toLocaleString('zh-CN')
}

function audioMatchStatusText(status) {
  return {
    matched: '唯一匹配',
    missing: '缺失媒体',
    ambiguous: '匹配歧义',
    collision: '哈希冲突',
    ready: '可播放',
  }[status] || status
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

function renderAssetSummary(asset) {
  if (!asset) return ''
  const container = asset.Container || asset.container || ''
  const source = asset.Source || asset.source || asset.sourceOriginalPath || ''
  const pathId = asset.PathID ?? asset.pathId ?? ''
  const type = asset.Type || asset.type || ''
  const name = asset.Name || asset.name || ''
  return `
    <div class="asset-meta">
      ${container ? `<span title="${escapeHtml(container)}">container: ${escapeHtml(container)}</span>` : ''}
      ${source ? `<span title="${escapeHtml(source)}">source: ${escapeHtml(source)}</span>` : ''}
      ${type || name ? `<span>${escapeHtml(type)} ${escapeHtml(name)}</span>` : ''}
      ${pathId !== '' && pathId != null ? `<span class="mono">id ${escapeHtml(pathId)}</span>` : ''}
    </div>
  `
}

async function getJson(url) {
  const response = await fetch(url)
  if (!response.ok) {
    let detail = ''
    try {
      detail = (await response.json()).error || ''
    } catch {
      // 非 JSON 错误响应仍使用 HTTP 状态作为兜底。
    }
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json()
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    let detail = ''
    try {
      detail = (await response.json()).error || ''
    } catch {
      // 非 JSON 错误响应仍使用 HTTP 状态作为兜底。
    }
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json()
}

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))

class TaskCancelledError extends Error {
  constructor() {
    super('后台任务已取消')
    this.name = 'TaskCancelledError'
  }
}

async function waitForTask(taskId, onProgress = null) {
  for (;;) {
    const snapshot = await getJson(`/api/task?taskId=${encodeURIComponent(taskId)}`)
    if (snapshot.state === 'succeeded') return snapshot.result
    if (snapshot.state === 'failed') {
      throw new Error(snapshot.error?.message || '后台任务失败')
    }
    if (snapshot.state === 'cancelled') throw new TaskCancelledError()
    if (onProgress && snapshot.progress) onProgress(snapshot.progress)
    await wait(250)
  }
}

const modelProgressLabels = {
  avatarPlan: '解析 AvatarMesh 资源计划',
  cabMap: '建立资源依赖映射',
  objects: '导出模型对象',
  textures: '导出模型纹理',
  publish: '发布模型缓存',
  cache: '读取模型缓存',
  glb: '生成 GLB 预览',
  ready: '模型预览已就绪',
}

function renderModelTaskProgress(progress) {
  const label = modelProgressLabels[progress.stage] || progress.stage || '处理模型'
  const completed = Number(progress.completed)
  const total = Number(progress.total)
  const detail = Number.isFinite(completed) && Number.isFinite(total) && total > 0
    ? `（${completed}/${total}）`
    : ''
  $('previewContent').innerHTML = `<div class="empty">${escapeHtml(label)}${escapeHtml(detail)}</div>`
}

function cancelActiveModelTask() {
  state.modelTaskSerial += 1
  const taskId = state.modelTaskId
  state.modelTaskId = null
  if (taskId) {
    fetch(`/api/task?taskId=${encodeURIComponent(taskId)}`, { method: 'DELETE' }).catch(() => {})
  }
}

function renderScopes(scopes) {
  const select = $('scopeSelect')
  const virtualScopes = [AUDIO_DIALOG_SCOPE, WWISE_SCOPE]
    .filter((scope) => !scopes.some((item) => item.scope === scope))
    .map((scope) => ({ scope }))
  const options = [...scopes, ...virtualScopes]
  select.innerHTML = options
    .map((scope) => `<option value="${escapeHtml(scope.scope)}">${scopeNames[scope.scope] || scope.scope}</option>`)
    .join('')
  select.value = state.scope
}

function renderSummary(scopes) {
  $('summary').innerHTML = scopes
    .map((scope) => `
      <button class="summary-card" data-scope="${escapeHtml(scope.scope)}">
        <strong>${scopeNames[scope.scope] || scope.scope}</strong>
        <span>${formatInt(scope.file_count)} files</span>
        <span>${formatBytes(scope.total_bytes)}</span>
      </button>
    `)
    .join('')
  document.querySelectorAll('.summary-card').forEach((card) => {
    card.addEventListener('click', () => {
      navigateDirectory({ scope: card.dataset.scope, path: '', page: 1 })
    })
  })
}

function renderBreadcrumbs() {
  const parts = state.path ? state.path.split('/') : []
  const rootLabel = state.scope === AUDIO_DIALOG_SCOPE
    ? `${scopeNames[state.scope]} · ${$('audioLanguageSelect').selectedOptions[0].textContent}`
    : scopeNames[state.scope] || state.scope
  const crumbs = [{ label: rootLabel, path: '' }]
  let current = ''
  for (const part of parts) {
    current = current ? `${current}/${part}` : part
    crumbs.push({ label: part === MANIFEST_VIRTUAL_DIR ? 'Manifest 资源' : part, path: current })
  }
  $('breadcrumbs').innerHTML = crumbs
    .map((crumb, index) => `
      <button class="crumb" data-path="${escapeHtml(crumb.path)}">${escapeHtml(crumb.label)}</button>
      ${index < crumbs.length - 1 ? '<span>/</span>' : ''}
    `)
    .join('')
  document.querySelectorAll('.crumb').forEach((button) => {
    button.addEventListener('click', () => {
      navigateDirectory({ path: button.dataset.path, page: 1 })
    })
  })
}

function renderStats(directory) {
  const cards = [
    ['文件数', formatInt(directory.file_count)],
    ['总大小', formatBytes(directory.total_bytes)],
    ['加密文件', formatInt(directory.encrypted_count)],
    ['缺失 Chunk', formatInt(directory.missing_chunk_count)],
  ]
  $('dirStats').innerHTML = cards
    .map(([label, value]) => `
      <div class="stat-card">
        <span>${label}</span>
        <strong>${value}</strong>
      </div>
    `)
    .join('')
}

function renderAudioStats(summary) {
  const cards = [
    ['语音条目', formatInt(summary.file_count)],
    ['唯一匹配', formatInt(summary.matched_count)],
    ['缺失媒体', formatInt(summary.missing_count)],
    ['歧义 / 冲突', formatInt(summary.ambiguous_count + summary.collision_count)],
  ]
  $('dirStats').innerHTML = cards
    .map(([label, value]) => `
      <div class="stat-card">
        <span>${label}</span>
        <strong>${value}</strong>
      </div>
    `)
    .join('')
}

function renderDirs(dirs) {
  $('dirCount').textContent = `${formatInt(dirs.length)} 个子目录`
  $('dirList').innerHTML = dirs.length
    ? dirs
        .map((dir) => `
          <button class="dir-card ${dir.virtualKind ? 'virtual' : ''}" data-path="${escapeHtml(dir.path)}">
            <strong>${escapeHtml(dir.name)}</strong>
            <span>${formatInt(dir.file_count)} files · ${dir.virtualKind ? '虚拟目录' : formatBytes(dir.total_bytes)}</span>
            ${dir.virtualKind ? `<span class="tag">${escapeHtml(dir.virtualKind)}</span>` : ''}
            ${dir.missing_chunk_count ? `<em>${formatInt(dir.missing_chunk_count)} missing chunks</em>` : ''}
          </button>
        `)
        .join('')
    : '<div class="empty">没有子目录</div>'
  document.querySelectorAll('.dir-card').forEach((card) => {
    card.addEventListener('click', () => {
      navigateDirectory({ path: card.dataset.path, page: 1 })
    })
  })
}

function renderFiles(files, filePage) {
  state.pageInfo = filePage
  $('pageInfo').textContent = `${filePage.page} / ${filePage.pages} · ${formatInt(filePage.total)} files`
  $('prevPage').disabled = filePage.page <= 1
  $('nextPage').disabled = filePage.page >= filePage.pages
  $('fileRows').innerHTML = files.length
    ? files
        .map((file) => {
          const rowKey = file.previewUrl ? `virtual:${file.previewUrl}` : `id:${file.id}`
          const isAudioDialog = file.virtualKind === AUDIO_DIALOG_SCOPE
          const range = isAudioDialog
            ? `
              <div class="mono">dialog ${escapeHtml(file.dialogKey)}</div>
              <div class="mono">${formatInt(file.mediaMatchCount)} 个物理媒体</div>
            `
            : `
              <div class="mono">offset ${formatInt(file.offset)}</div>
              <div class="mono">len ${formatInt(file.length)}</div>
            `
          const status = isAudioDialog
            ? `
              <span class="tag ${file.audioStatus === 'matched' ? '' : file.audioStatus === 'ambiguous' ? 'warn' : 'danger'}">
                ${escapeHtml(audioMatchStatusText(file.audioStatus))}
              </span>
            `
            : `
              <span class="tag ${file.encrypted ? 'warn' : ''}">${file.virtualKind ? escapeHtml(file.virtualKind) : file.encrypted ? 'encrypted' : 'plain'}</span>
              <span class="tag ${file.chunk_exists ? '' : 'danger'}">${file.chunk_exists ? 'chunk ok' : 'missing chunk'}</span>
              ${file.encrypted ? `<div class="mono muted">iv ${file.iv_seed}</div>` : ''}
            `
          return `
          <tr
            class="${state.selectedFileKey === rowKey ? 'selected' : ''}"
            data-file-key="${escapeHtml(rowKey)}"
            ${file.previewUrl ? `data-preview-url="${escapeHtml(file.previewUrl)}"` : `data-file-id="${file.id}"`}
            title="${escapeHtml(file.path)}"
          >
            <td>
              <div class="file-name-row">
                <div class="file-name">${escapeHtml(file.name)}</div>
                ${file.avatarPlanUrl ? `<button class="avatar-plan-button" data-avatar-plan-url="${escapeHtml(file.avatarPlanUrl)}" title="AvatarMesh resource plan">资源</button>` : ''}
                ${file.modelUrl ? `<button class="model-preview-button" data-model-url="${escapeHtml(file.modelUrl)}" title="预览组合模型">3D</button>` : ''}
              </div>
              <div class="file-path">${escapeHtml(file.file_name)}</div>
            </td>
            <td>${escapeHtml(file.source)}</td>
            <td>${escapeHtml(file.block_name || '')}</td>
            <td>
              <div class="mono">${escapeHtml(file.chunk_file || '')}</div>
            </td>
            <td>
              ${range}
            </td>
            <td>
              ${status}
            </td>
          </tr>
        `
        })
        .join('')
    : '<tr><td colspan="6" class="empty">这个目录没有文件</td></tr>'
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.addEventListener('click', () => {
      if (row.dataset.previewUrl) {
        selectVirtualFile(row.dataset.previewUrl, row.dataset.fileKey)
      } else {
        selectFile(Number(row.dataset.fileId))
      }
    })
  })
  document.querySelectorAll('.model-preview-button').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation()
      const row = button.closest('tr[data-file-key]')
      selectModel(button.dataset.modelUrl, row.dataset.fileKey)
    })
  })
  document.querySelectorAll('.avatar-plan-button').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation()
      const row = button.closest('tr[data-file-key]')
      selectAvatarPlan(button.dataset.avatarPlanUrl, row.dataset.fileKey)
    })
  })
}

async function loadDirectory() {
  renderBreadcrumbs()
  if (state.scope === AUDIO_DIALOG_SCOPE) {
    await loadAudioDialogDirectory()
    return
  }
  if (state.scope === WWISE_SCOPE) {
    await loadWwiseDirectory()
    return
  }
  const params = new URLSearchParams({
    scope: state.scope,
    path: state.path,
    page: state.page,
    pageSize: PAGE_SIZE,
  })
  const data = await getJson(`/api/list?${params}`)
  renderStats(data.directory)
  renderDirs(data.dirs, data.virtual)
  renderFiles(data.files, data.filePage)
}

async function loadWwiseDirectory() {
  const params = new URLSearchParams({
    path: state.path,
    page: state.page,
    pageSize: PAGE_SIZE,
  })
  try {
    const data = await getJson(`/api/wwise/list?${params}`)
    renderStats(data.directory)
    renderDirs(data.dirs.map((directory) => ({
      ...directory,
      virtualKind: WWISE_SCOPE,
    })))
    renderFiles(data.files, data.page)
  } catch (error) {
    renderStats({ file_count: 0, total_bytes: 0, encrypted_count: 0, missing_chunk_count: 0 })
    renderDirs([])
    renderFiles([], { page: 1, pages: 1, total: 0 })
    clearPreviewSelection()
    $('previewContent').innerHTML = `<div class="notice">Wwise 索引读取失败：${escapeHtml(error.message)}</div>`
  }
}

async function loadAudioDialogDirectory() {
  const params = new URLSearchParams({
    language: state.audioLanguage,
    path: state.path,
    page: state.page,
    pageSize: PAGE_SIZE,
  })
  try {
    const data = await getJson(`/api/audio-dialog/list?${params}`)
    const directories = data.directories.map((directory) => ({
      ...directory,
      total_bytes: 0,
      virtualKind: AUDIO_DIALOG_SCOPE,
    }))
    const files = data.files.map((file) => {
      const previewParams = new URLSearchParams({
        language: data.language,
        path: file.logical_path,
        dialogKey: file.dialog_key,
      })
      return {
        name: file.name,
        path: file.logical_path,
        file_name: file.logical_path,
        source: data.language,
        block_name: 'AudioDialog',
        chunk_file: file.media_id,
        dialogKey: file.dialog_key,
        mediaMatchCount: file.media_match_count,
        audioStatus: file.match_status,
        virtualKind: AUDIO_DIALOG_SCOPE,
        previewUrl: `/api/audio-dialog/preview?${previewParams}`,
      }
    })
    const pages = Math.max(Math.ceil(data.page.total / data.page.pageSize), 1)
    renderAudioStats(data.summary)
    renderDirs(directories)
    renderFiles(files, {
      page: data.page.page,
      pages,
      total: data.page.total,
    })
  } catch (error) {
    renderAudioStats({
      file_count: 0,
      matched_count: 0,
      missing_count: 0,
      ambiguous_count: 0,
      collision_count: 0,
    })
    renderDirs([])
    renderFiles([], { page: 1, pages: 1, total: 0 })
    clearPreviewSelection()
    $('previewContent').innerHTML = `<div class="notice">AudioDialog 索引读取失败：${escapeHtml(error.message)}</div>`
  }
}

function resetPreviewActions() {
  $('openRawLink').textContent = '打开原始文件'
  $('downloadLink').textContent = '下载'
  $('openRawLink').removeAttribute('href')
  $('downloadLink').removeAttribute('href')
}

function clearPreviewSelection() {
  cancelActiveModelTask()
  state.selectedFileId = null
  state.selectedFileKey = null
  state.routeSelection = null
  disposeModelViewer()
  resetPreviewActions()
  $('previewContent').innerHTML = '<div class="empty">点击文件列表中的文件进行预览</div>'
}

function renderPreviewLoading() {
  disposeModelViewer()
  $('previewContent').innerHTML = '<div class="empty">正在读取文件...</div>'
  resetPreviewActions()
}

function disposeModelViewer() {
  state.disposeModelViewer?.()
  state.disposeModelViewer = null
}

function disposeModelResources(root) {
  root?.traverse((object) => {
    object.geometry?.dispose()
    const materials = Array.isArray(object.material) ? object.material : [object.material]
    for (const material of materials) {
      if (!material) continue
      for (const value of Object.values(material)) {
        if (value?.isTexture) value.dispose()
      }
      material.dispose()
    }
  })
}

function createOutlineMaterial() {
  const material = new THREE.MeshBasicMaterial({
    color: 0x16191d,
    side: THREE.BackSide,
  })
  material.onBeforeCompile = (shader) => {
    shader.uniforms.outlineWidth = { value: 0.0035 }
    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      '#include <begin_vertex>\ntransformed += objectNormal * outlineWidth;',
    ).replace(
      'void main() {',
      'uniform float outlineWidth;\nvoid main() {',
    )
  }
  material.customProgramCacheKey = () => 'endfield-outline-v1'
  return material
}

function createOutlineModel(source) {
  const outline = cloneSkeleton(source)
  const material = createOutlineMaterial()
  outline.name = 'EndfieldPreviewOutline'
  outline.traverse((object) => {
    if (!object.isMesh) return
    const sourceMaterials = Array.isArray(object.material) ? object.material : [object.material]
    const opaque = sourceMaterials.some((value) => value && !value.transparent && value.alphaTest === 0)
    object.visible = opaque
    object.material = material
    object.renderOrder = -1
  })
  return outline
}

function formatAnimationTime(value) {
  return Number(value || 0).toFixed(2).replace(/\.?0+$/, '')
}

function renderModelPreview(data) {
  const maxSelectedAnimations = Number(data.maxBlendAnimationCount) || 100
  disposeModelViewer()
  const modelDocument = data.document || {}
  const glbUrl = data.glbUrl
  const downloadUrl = `${glbUrl}${glbUrl.includes('?') ? '&' : '?'}download=1`
  $('openRawLink').href = glbUrl
  $('downloadLink').href = downloadUrl
  $('previewContent').innerHTML = `
    <div class="model-summary">
      <strong>${escapeHtml(data.asset?.path || 'Model')}</strong>
      <span>${formatInt(modelDocument.nodes?.length)} 节点</span>
      <span>${formatInt(modelDocument.meshes?.length)} 网格</span>
      <span>${formatInt(modelDocument.skins?.length)} 蒙皮</span>
      <span>${formatInt(modelDocument.materials?.length)} 材质</span>
      <span>${formatInt(modelDocument.images?.length)} 纹理</span>
      ${data.animationAsset ? `<span>${escapeHtml(data.animationAsset.path)} 动画</span>` : ''}
    </div>
    ${data.baseBlendUrl || data.blendUrl ? `
      <div class="preview-actions model-export-actions">
        <a id="modelBaseBlendLink" class="link-button" href="${escapeHtml(data.baseBlendUrl || data.blendUrl)}" title="首次导出需要等待 Blender 后台生成">导出基础模型</a>
        <a id="modelAnimationBlendLink" class="link-button" href="${escapeHtml(data.animationAsset ? data.blendUrl : '')}" ${data.animationAsset ? '' : 'hidden'} title="将当前动画保存为 Blender Action">导出当前动画</a>
      </div>
    ` : ''}
    ${data.animationCandidatesUrl ? `
      <form id="modelAnimationSearch" class="model-animation-search">
        <input id="modelAnimationQuery" type="search" placeholder="搜索动画名称或路径" autocomplete="off" />
        <button type="submit">搜索</button>
        <span id="modelAnimationSearchStatus">正在查找动画...</span>
        <div id="modelAnimationCandidates" class="model-animation-candidates" aria-label="动画候选" aria-multiselectable="true"></div>
        <input id="modelAnimationSelectedName" class="model-animation-selected-name" type="text" readonly aria-label="当前动画名称" placeholder="选择动画后可复制名称" />
        <div class="model-animation-selection-actions">
          <span id="modelAnimationSelectionStatus">未选择导出动画</span>
          <button id="modelAnimationSelectSearchResults" type="button" disabled>选择全部搜索结果</button>
          <button id="modelAnimationClearSelection" type="button" disabled>清空选择</button>
          <a id="modelAnimationBundleLink" class="link-button" hidden title="将模型与所选动画保存到同一个 Blender 文件">导出所选动画</a>
        </div>
        <details id="modelAnimationExportIssues" class="model-animation-export-issues" hidden>
          <summary id="modelAnimationExportIssuesSummary"></summary>
          <ul id="modelAnimationExportIssuesList"></ul>
        </details>
        <div class="model-animation-pager">
          <button id="modelAnimationPreviousPage" type="button" disabled>上一页</button>
          <span id="modelAnimationPageStatus">1 / 1</span>
          <button id="modelAnimationNextPage" type="button" disabled>下一页</button>
        </div>
      </form>
    ` : ''}
    <div id="modelViewport" class="model-viewport">
      <label class="model-view-option">
        <input id="modelOutlineToggle" type="checkbox" />
        <span>轮廓线</span>
      </label>
      <div id="modelAnimationControls" class="model-animation-controls" hidden>
        <button id="modelAnimationToggle" type="button" title="暂停动画">❚❚</button>
        <strong id="modelAnimationName"></strong>
        <input id="modelAnimationTime" type="range" min="0" max="0" step="0.001" value="0" aria-label="动画时间" />
        <output id="modelAnimationTimeLabel">0 / 0</output>
      </div>
      <div id="modelLoading" class="model-loading">正在加载 GLB...</div>
    </div>
  `

  const viewport = $('modelViewport')
  const loading = $('modelLoading')
  const animationTimeInput = $('modelAnimationTime')
  const animationTimeLabel = $('modelAnimationTimeLabel')
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x101317)
  const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 10000)
  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  viewport.prepend(renderer.domElement)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  scene.add(new THREE.HemisphereLight(0xffffff, 0x28313a, 2.2))
  const keyLight = new THREE.DirectionalLight(0xffffff, 3.2)
  keyLight.position.set(4, 6, 5)
  scene.add(keyLight)

  let model = null
  let mixers = []
  let animationRoots = []
  let baseTransforms = []
  let animationDuration = 0
  let isAnimationPlaying = true
  let active = true
  let modelReady = false
  let animationCandidates = []
  let animationRequestId = 0
  let animationTaskId = null
  let animationCandidateRequestId = 0
  let animationCandidateQuery = null
  let animationCandidatePage = 1
  let animationCandidatePages = 1
  let animationCandidateTotal = 0
  let animationSelectionRequestId = 0
  let blendTaskId = null
  let blendExportPending = false
  const selectedAnimationCandidates = new Map()
  const clock = new THREE.Clock()
  const renderAnimationTime = (time) => {
    animationTimeInput.value = String(time)
    animationTimeLabel.value = `${formatAnimationTime(time)} / ${formatAnimationTime(animationDuration)}`
  }
  const restoreBasePose = () => {
    baseTransforms.forEach((transforms) => {
      transforms.forEach(({ object, position, quaternion, scale }) => {
        object.position.copy(position)
        object.quaternion.copy(quaternion)
        object.scale.copy(scale)
      })
    })
    animationRoots.forEach((root) => root.updateMatrixWorld(true))
  }
  const stopAnimation = () => {
    animationRequestId += 1
    if (animationTaskId) {
      fetch(`/api/task?taskId=${encodeURIComponent(animationTaskId)}`, { method: 'DELETE' }).catch(() => {})
      animationTaskId = null
    }
    for (const mixer of mixers) mixer.stopAllAction()
    mixers = []
    animationDuration = 0
    restoreBasePose()
    const animationControls = $('modelAnimationControls')
    if (animationControls) animationControls.hidden = true
    const exportLink = $('modelAnimationBlendLink')
    if (exportLink) exportLink.hidden = true
    renderAnimationTime(0)
  }
  const setAnimationTime = (time) => {
    for (const mixer of mixers) mixer.setTime(time)
    renderAnimationTime(time)
  }
  const loadModelAnimation = async ({ previewUrl, blendUrl, assetIndex } = {}) => {
    if (!previewUrl || !animationRoots.length) {
      stopAnimation()
      return
    }
    const status = $('modelAnimationSearchStatus')
    if (status) status.textContent = '正在解析动画...'
    stopAnimation()
    const requestId = animationRequestId
    const animationUrl = new URL(previewUrl, window.location.origin)
    const task = await postJson('/api/tasks/model-animation', {
      manifestId: animationUrl.searchParams.get('manifestId'),
      assetIndex: animationUrl.searchParams.get('assetIndex'),
      animationAssetIndex: animationUrl.searchParams.get('animationAssetIndex'),
      lod: animationUrl.searchParams.get('lod') || 0,
    })
    if (!active || requestId !== animationRequestId) {
      fetch(`/api/task?taskId=${encodeURIComponent(task.taskId)}`, { method: 'DELETE' }).catch(() => {})
      return
    }
    animationTaskId = task.taskId
    let animation
    try {
      animation = await waitForTask(task.taskId, (progress) => {
        if (!active || requestId !== animationRequestId || animationTaskId !== task.taskId) return
        const labels = {
          model: '正在准备模型骨架...',
          animation: '正在导出动画...',
          binding: '正在绑定动画轨道...',
          ready: '动画已就绪',
        }
        if (status) status.textContent = labels[progress.stage] || '正在解析动画...'
      })
    } catch (error) {
      if (animationTaskId === task.taskId) animationTaskId = null
      if (error instanceof TaskCancelledError || !active || requestId !== animationRequestId) return
      throw error
    }
    if (!active || requestId !== animationRequestId || animationTaskId !== task.taskId) return
    animationTaskId = null
    const clips = animationRoots.map((root) => createModelAnimationClip(animation, root))
    mixers = animationRoots.map((root, index) => {
      const mixer = new THREE.AnimationMixer(root)
      mixer.clipAction(clips[index]).play()
      return mixer
    })
    animationDuration = Number(animation.duration) || clips[0].duration
    animationTimeInput.max = String(animationDuration)
    $('modelAnimationName').textContent = animation.name || 'AnimationClip'
    $('modelAnimationControls').hidden = false
    isAnimationPlaying = true
    $('modelAnimationToggle').textContent = '❚❚'
    $('modelAnimationToggle').title = '暂停动画'
    const exportLink = $('modelAnimationBlendLink')
    if (exportLink && blendUrl) {
      exportLink.href = blendUrl
      exportLink.hidden = false
    }
    const diagnostics = animation.diagnostics || []
    const unresolved = diagnostics.filter((item) => (
      item.code === 'ANIMATION_PATHS_UNRESOLVED' || item.code === 'ANIMATION_PATHS_AMBIGUOUS'
    )).length
    const isEmptyMorph = diagnostics.some((item) => (
      item.code === 'ANIMATION_SKELETAL_MORPH_EMPTY'
    ))
    if (status) {
      status.textContent = isEmptyMorph
        ? '空表情资源（源数据无动画曲线）'
        : `${animation.tracks.length} 条轨道${unresolved ? `，${unresolved} 项绑定诊断` : ''}`
    }
    if (state.routeSelection?.kind === 'model') {
      state.routeSelection.animationAssetIndex = assetIndex == null ? null : String(assetIndex)
      writeRoute({ replace: true })
    }
    renderAnimationTime(0)
  }

  $('modelAnimationToggle').addEventListener('click', () => {
    isAnimationPlaying = !isAnimationPlaying
    $('modelAnimationToggle').textContent = isAnimationPlaying ? '❚❚' : '▶'
    $('modelAnimationToggle').title = isAnimationPlaying ? '暂停动画' : '播放动画'
  })
  animationTimeInput.addEventListener('input', (event) => {
    setAnimationTime(Number(event.currentTarget.value))
  })
  const resize = () => {
    const width = Math.max(viewport.clientWidth, 1)
    const height = Math.max(viewport.clientHeight, 1)
    renderer.setSize(width, height, false)
    camera.aspect = width / height
    camera.updateProjectionMatrix()
  }
  const observer = new ResizeObserver(resize)
  observer.observe(viewport)
  resize()

  const animationSearch = $('modelAnimationSearch')
  const animationQuery = $('modelAnimationQuery')
  const animationCandidateList = $('modelAnimationCandidates')
  const animationSelectedName = $('modelAnimationSelectedName')
  const animationSelectionStatus = $('modelAnimationSelectionStatus')
  const animationSelectSearchResults = $('modelAnimationSelectSearchResults')
  const animationClearSelection = $('modelAnimationClearSelection')
  const baseBlendLink = $('modelBaseBlendLink')
  const currentAnimationBlendLink = $('modelAnimationBlendLink')
  const animationBundleLink = $('modelAnimationBundleLink')
  const animationExportIssues = $('modelAnimationExportIssues')
  const animationExportIssuesSummary = $('modelAnimationExportIssuesSummary')
  const animationExportIssuesList = $('modelAnimationExportIssuesList')
  const animationPreviousPage = $('modelAnimationPreviousPage')
  const animationNextPage = $('modelAnimationNextPage')
  const animationPageStatus = $('modelAnimationPageStatus')
  const animationCandidateName = (candidate) => (
    candidate?.path?.split('##').pop() || candidate?.name || ''
  )
  const renderAnimationExportIssues = (issues = []) => {
    animationExportIssues.hidden = issues.length === 0
    animationExportIssues.open = issues.length > 0
    animationExportIssuesSummary.textContent = `未导出 ${issues.length} 个动画`
    animationExportIssuesList.replaceChildren(...issues.map((issue) => {
      const item = document.createElement('li')
      const name = issue.path?.split('##').pop() || issue.path || `#${issue.assetIndex}`
      item.textContent = `${name}：${issue.message}`
      item.title = issue.path || ''
      return item
    }))
  }
  if (animationSelectedName && data.animationAsset) {
    animationSelectedName.value = animationCandidateName(data.animationAsset)
    animationSelectedName.title = data.animationAsset.path || ''
  }
  const syncAnimationBundleLink = () => {
    if (!animationSelectionStatus || !animationClearSelection || !animationBundleLink) return
    const selected = [...selectedAnimationCandidates.values()].sort((left, right) => (
      Number(left.assetIndex) - Number(right.assetIndex)
    ))
    animationSelectionStatus.textContent = selected.length
      ? `已选择 ${selected.length} 个动画`
      : '未选择导出动画'
    animationClearSelection.disabled = selected.length === 0
    animationBundleLink.hidden = selected.length === 0
    if (!selected.length || !data.baseBlendUrl) {
      animationBundleLink.removeAttribute('href')
      return
    }
    const url = new URL(data.baseBlendUrl, window.location.origin)
    url.searchParams.delete('animationAssetIndex')
    selected.forEach((candidate) => {
      url.searchParams.append('animationAssetIndex', String(candidate.assetIndex))
    })
    animationBundleLink.href = `${url.pathname}${url.search}`
    animationBundleLink.textContent = `导出模型与 ${selected.length} 个动画`
  }
  const renderAnimationCandidates = () => {
    if (!animationCandidateList) return
    animationCandidateList.replaceChildren()
    animationCandidates.forEach((candidate) => {
      const assetIndex = String(candidate.assetIndex)
      const row = document.createElement('div')
      row.className = 'model-animation-candidate'
      const checkbox = document.createElement('input')
      checkbox.type = 'checkbox'
      checkbox.checked = selectedAnimationCandidates.has(assetIndex)
      checkbox.title = '加入 Blender 批量导出'
      checkbox.setAttribute('aria-label', `选择 ${animationCandidateName(candidate)}`)
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          if (selectedAnimationCandidates.size >= maxSelectedAnimations) {
            checkbox.checked = false
            animationSelectionStatus.textContent = `单次最多选择 ${maxSelectedAnimations} 个动画`
            return
          }
          selectedAnimationCandidates.set(assetIndex, candidate)
        } else {
          selectedAnimationCandidates.delete(assetIndex)
        }
        syncAnimationBundleLink()
      })
      const preview = document.createElement('button')
      preview.type = 'button'
      preview.className = 'model-animation-candidate-preview'
      preview.textContent = animationCandidateName(candidate)
      preview.title = candidate.path || ''
      preview.disabled = !modelReady
      preview.addEventListener('click', async () => {
        animationSelectedName.value = animationCandidateName(candidate)
        animationSelectedName.title = candidate.path || ''
        try {
          await loadModelAnimation(candidate)
        } catch (error) {
          console.error('模型动画加载失败', error)
          $('modelAnimationSearchStatus').textContent = `动画加载失败：${error.message || error}`
        }
      })
      row.append(checkbox, preview)
      animationCandidateList.append(row)
    })
  }
  syncAnimationBundleLink()
  const startBlendExport = async (event) => {
    event.preventDefault()
    const link = event.currentTarget
    if (blendExportPending || blendTaskId || link.getAttribute('aria-disabled') === 'true') return
    const href = link.getAttribute('href')
    if (!href) return
    link.setAttribute('aria-disabled', 'true')
    blendExportPending = true
    animationSelectionStatus.textContent = '正在准备 Blender 导出...'
    try {
      const prepareUrl = new URL(href, window.location.origin)
      const task = await postJson('/api/tasks/model-blend', {
        manifestId: prepareUrl.searchParams.get('manifestId'),
        assetIndex: prepareUrl.searchParams.get('assetIndex'),
        lod: prepareUrl.searchParams.get('lod') || 0,
        animationAssetIndexes: prepareUrl.searchParams.getAll('animationAssetIndex'),
      })
      if (!active) {
        blendExportPending = false
        fetch(`/api/task?taskId=${encodeURIComponent(task.taskId)}`, { method: 'DELETE' }).catch(() => {})
        return
      }
      blendExportPending = false
      blendTaskId = task.taskId
      const result = await waitForTask(task.taskId, (progress) => {
        if (!active || blendTaskId !== task.taskId) return
        if (progress.stage === 'blender') {
          animationSelectionStatus.textContent = '正在生成 Blender 文件...'
        } else if (progress.stage === 'animationCache') {
          animationSelectionStatus.textContent = '已复用动画绑定结果，正在准备导出...'
        } else {
          const completed = Number(progress.completed) || 0
          const total = Number(progress.total) || 0
          animationSelectionStatus.textContent = total
            ? `正在处理动画 ${completed}/${total}...`
            : '正在处理所选动画...'
        }
      })
      if (!active || blendTaskId !== task.taskId) return
      blendTaskId = null
      renderAnimationExportIssues(result.issues || [])
      if (!result.artifactAvailable) {
        animationSelectionStatus.textContent = '所选动画均无法导出'
        return
      }
      animationSelectionStatus.textContent = result.issues?.length
        ? `已导出 ${result.exportedCount} 个，跳过 ${result.issues.length} 个`
        : result.exportedCount
          ? `已生成包含 ${result.exportedCount} 个动画的 Blender 文件`
          : '已生成基础模型 Blender 文件'
      const download = document.createElement('a')
      download.href = `/api/task-artifact?taskId=${encodeURIComponent(task.taskId)}`
      download.click()
    } catch (error) {
      blendExportPending = false
      blendTaskId = null
      animationSelectionStatus.textContent = `Blender 导出失败：${error.message || error}`
    } finally {
      if (active) link.removeAttribute('aria-disabled')
    }
  }
  baseBlendLink?.addEventListener('click', startBlendExport)
  currentAnimationBlendLink?.addEventListener('click', startBlendExport)
  animationBundleLink?.addEventListener('click', startBlendExport)
  const syncAnimationPager = (loading = false) => {
    animationPreviousPage.disabled = loading || animationCandidatePage <= 1
    animationNextPage.disabled = loading || animationCandidatePage >= animationCandidatePages
    animationSelectSearchResults.disabled = loading || animationCandidateTotal === 0
    animationSelectSearchResults.textContent = animationCandidateTotal
      ? `选择全部搜索结果（${animationCandidateTotal}）`
      : '选择全部搜索结果'
    animationPageStatus.textContent = `${animationCandidatePage} / ${animationCandidatePages}`
  }
  const fetchAnimationCandidates = (query, page, pageSize = null) => {
    const url = new URL(data.animationCandidatesUrl, window.location.origin)
    if (query != null) url.searchParams.set('q', query)
    url.searchParams.set('page', String(page))
    if (pageSize != null) url.searchParams.set('pageSize', String(pageSize))
    return getJson(url.toString())
  }
  const loadAnimationCandidates = async (query = null, page = 1) => {
    if (!data.animationCandidatesUrl || !animationCandidateList) return
    const requestId = ++animationCandidateRequestId
    const status = $('modelAnimationSearchStatus')
    status.textContent = '正在查找动画...'
    animationCandidateList.setAttribute('aria-busy', 'true')
    syncAnimationPager(true)
    const result = await fetchAnimationCandidates(query, page)
    if (!active || requestId !== animationCandidateRequestId) return
    animationCandidateQuery = result.query || ''
    animationCandidatePage = result.page || 1
    animationCandidatePages = result.pages || 1
    animationCandidateTotal = result.total || 0
    if (query == null) animationQuery.value = result.defaultQuery || animationCandidateQuery
    animationCandidates = result.files || []
    renderAnimationCandidates()
    animationCandidateList.removeAttribute('aria-busy')
    syncAnimationPager()
    status.textContent = animationCandidates.length
      ? `${result.total} 个候选，当前页 ${animationCandidates.length} 个`
      : '没有找到动画候选'
  }
  if (animationSearch) {
    animationSearch.addEventListener('submit', async (event) => {
      event.preventDefault()
      try {
        await loadAnimationCandidates(animationQuery.value, 1)
      } catch (error) {
        $('modelAnimationSearchStatus').textContent = `动画搜索失败：${error.message || error}`
        syncAnimationPager()
      }
    })
    animationPreviousPage.addEventListener('click', async () => {
      try {
        await loadAnimationCandidates(animationCandidateQuery, animationCandidatePage - 1)
      } catch (error) {
        $('modelAnimationSearchStatus').textContent = `动画搜索失败：${error.message || error}`
        syncAnimationPager()
      }
    })
    animationNextPage.addEventListener('click', async () => {
      try {
        await loadAnimationCandidates(animationCandidateQuery, animationCandidatePage + 1)
      } catch (error) {
        $('modelAnimationSearchStatus').textContent = `动画搜索失败：${error.message || error}`
        syncAnimationPager()
      }
    })
    animationSelectSearchResults.addEventListener('click', async () => {
      const requestId = ++animationSelectionRequestId
      animationSelectSearchResults.disabled = true
      animationSelectionStatus.textContent = '正在选择搜索结果...'
      try {
        const result = await fetchAnimationCandidates(
          animationCandidateQuery || '',
          1,
          maxSelectedAnimations,
        )
        if (!active || requestId !== animationSelectionRequestId) return
        if (result.total > maxSelectedAnimations) {
          animationSelectionStatus.textContent = (
            `搜索到 ${result.total} 个动画，请缩小范围至 ${maxSelectedAnimations} 个以内`
          )
          return
        }
        const merged = new Map(selectedAnimationCandidates)
        for (const candidate of result.files || []) {
          merged.set(String(candidate.assetIndex), candidate)
        }
        if (merged.size > maxSelectedAnimations) {
          animationSelectionStatus.textContent = (
            `合并后超过 ${maxSelectedAnimations} 个动画，请先清空或减少已有选择`
          )
          return
        }
        selectedAnimationCandidates.clear()
        for (const [assetIndex, candidate] of merged) {
          selectedAnimationCandidates.set(assetIndex, candidate)
        }
        renderAnimationCandidates()
        syncAnimationBundleLink()
      } catch (error) {
        animationSelectionStatus.textContent = `选择搜索结果失败：${error.message || error}`
      } finally {
        if (active && requestId === animationSelectionRequestId) syncAnimationPager()
      }
    })
    animationClearSelection.addEventListener('click', () => {
      selectedAnimationCandidates.clear()
      renderAnimationCandidates()
      syncAnimationBundleLink()
    })
    animationSelectedName.addEventListener('focus', (event) => event.currentTarget.select())
    loadAnimationCandidates().catch((error) => {
      $('modelAnimationSearchStatus').textContent = `动画搜索失败：${error.message || error}`
      syncAnimationPager()
    })
  }

  new GLTFLoader().load(
    glbUrl,
    async (gltf) => {
      if (!active) {
        disposeModelResources(gltf.scene)
        return
      }
      model = new THREE.Group()
      model.name = 'EndfieldModelPreview'
      model.add(gltf.scene)
      const outline = createOutlineModel(gltf.scene)
      outline.visible = false
      model.add(outline)
      animationRoots = [gltf.scene, outline]
      baseTransforms = animationRoots.map((root) => {
        const transforms = []
        root.traverse((object) => {
          transforms.push({
            object,
            position: object.position.clone(),
            quaternion: object.quaternion.clone(),
            scale: object.scale.clone(),
          })
        })
        return transforms
      })
      modelReady = true
      if (animationCandidateList) renderAnimationCandidates()
      $('modelOutlineToggle').addEventListener('change', (event) => {
        outline.visible = event.currentTarget.checked
      })
      scene.add(model)
      model.updateMatrixWorld(true)
      const box = new THREE.Box3().setFromObject(model)
      if (box.isEmpty()) {
        loading.textContent = '模型没有可显示的几何体'
        return
      }
      const size = box.getSize(new THREE.Vector3())
      const center = box.getCenter(new THREE.Vector3())
      model.position.sub(center)
      const verticalFov = THREE.MathUtils.degToRad(camera.fov)
      const framedHeight = Math.max(size.y, size.x / camera.aspect)
      const distance = Math.max(framedHeight / (2 * Math.tan(verticalFov / 2)) * 1.18, 0.1)
      const viewDirection = new THREE.Vector3(0.7, 0.22, 1.5).normalize()
      camera.near = Math.max(distance / 1000, 0.001)
      camera.far = distance + Math.max(size.length() * 10, 10)
      camera.position.copy(viewDirection.multiplyScalar(distance))
      camera.updateProjectionMatrix()
      controls.target.set(0, 0, 0)
      controls.update()
      if (data.animationUrl) {
        try {
          loading.textContent = '正在加载动画...'
          await loadModelAnimation({
            previewUrl: data.animationUrl,
            blendUrl: data.blendUrl,
            assetIndex: data.animationAsset?.asset_index,
          })
          loading.remove()
        } catch (error) {
          console.error('模型动画加载失败', error)
          loading.textContent = `动画加载失败：${error.message || error}`
        }
      } else {
        loading.remove()
      }
    },
    (event) => {
      if (!active || !event.total) return
      loading.textContent = `正在加载 GLB... ${Math.round(event.loaded / event.total * 100)}%`
    },
    (error) => {
      if (!active) return
      loading.textContent = `模型加载失败：${error.message || error}`
    },
  )

  renderer.setAnimationLoop(() => {
    const delta = clock.getDelta()
    if (isAnimationPlaying) {
      for (const mixer of mixers) mixer.update(delta)
    }
    if (mixers.length) {
      const time = animationDuration > 0 ? mixers[0].time % animationDuration : 0
      renderAnimationTime(time)
    }
    controls.update()
    renderer.render(scene, camera)
  })
  state.disposeModelViewer = () => {
    active = false
    animationRequestId += 1
    if (animationTaskId) {
      fetch(`/api/task?taskId=${encodeURIComponent(animationTaskId)}`, { method: 'DELETE' }).catch(() => {})
      animationTaskId = null
    }
    if (blendTaskId) {
      fetch(`/api/task?taskId=${encodeURIComponent(blendTaskId)}`, { method: 'DELETE' }).catch(() => {})
      blendTaskId = null
    }
    blendExportPending = false
    observer.disconnect()
    renderer.setAnimationLoop(null)
    for (const mixer of mixers) mixer.stopAllAction()
    mixers = []
    controls.dispose()
    disposeModelResources(model)
    renderer.dispose()
    renderer.forceContextLoss()
  }
}

function renderBinaryJsonProbe(probe) {
  const strings = probe?.lengthPrefixedStrings || []
  const firstByte = probe?.firstByte == null ? 'N/A' : `0x${Number(probe.firstByte).toString(16).padStart(2, '0')}`
  return `
    <div class="binary-json-summary">
      <div>
        <span>格式猜测</span>
        <strong>${escapeHtml(probe?.formatHint || 'unknown')}</strong>
      </div>
      <div>
        <span>置信度</span>
        <strong>${escapeHtml(probe?.confidence || 'unknown')}</strong>
      </div>
      <div>
        <span>首字节</span>
        <strong class="mono">${escapeHtml(firstByte)}</strong>
      </div>
      <div>
        <span>首字节解释</span>
        <strong>${probe?.possibleMemberCount == null ? 'N/A' : `可能是 ${formatInt(probe.possibleMemberCount)} 个成员`}</strong>
      </div>
      <div>
        <span>样本大小</span>
        <strong>${formatBytes(probe?.sampleLength || 0)} / ${formatBytes(probe?.fullLength || 0)}</strong>
      </div>
      <div>
        <span>UTF-8 片段</span>
        <strong>${formatInt(strings.length)}</strong>
      </div>
    </div>
    <div class="binary-json-section">
      <strong>可读 UTF-8 片段</strong>
      ${
        strings.length
          ? `<table class="string-probe-table">
              <thead><tr><th>offset</th><th>len</th><th>text</th></tr></thead>
              <tbody>
                ${strings.map((item) => `
                  <tr>
                    <td class="mono">0x${Number(item.offset).toString(16)}</td>
                    <td>${formatInt(item.length)}</td>
                    <td>${escapeHtml(item.text)}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>`
          : '<div class="empty small">前段样本里没有找到疑似长度前缀 UTF-8 片段。</div>'
      }
    </div>
  `
}

function renderAudioDialogPreview(data) {
  const entry = data.entry
  const media = entry.media[0]
  $('openRawLink').removeAttribute('href')
  $('downloadLink').removeAttribute('href')
  if (data.rawUrl) {
    $('openRawLink').textContent = '打开 WAV'
    $('openRawLink').href = data.rawUrl
  }
  if (data.wavDownloadUrl) {
    $('downloadLink').textContent = '下载 WAV'
    $('downloadLink').href = data.wavDownloadUrl
  }

  const statusText = {
    ready: audioMatchStatusText('ready'),
    missing: '未找到对应物理媒体',
    ambiguous: '命中多个物理媒体',
    collision: '逻辑路径哈希冲突',
  }[data.status] || data.status
  const mediaDetails = media
    ? `
      <span>PCK 文件 ${formatInt(media.pck_file_id)} · offset ${formatInt(media.offset)} · len ${formatInt(media.size)}</span>
      <span>${escapeHtml(media.source)}${media.bank_id == null ? '' : ` · bank ${escapeHtml(media.bank_id)}`}</span>
    `
    : ''
  const player = data.status === 'ready'
    ? `
      <audio class="audio-preview" src="${escapeHtml(data.rawUrl)}" controls></audio>
      <div class="preview-actions inline-actions">
        <a class="link-button" href="${escapeHtml(data.wemDownloadUrl)}">下载 WEM</a>
        <a class="link-button" href="${escapeHtml(data.wavDownloadUrl)}">下载 WAV</a>
      </div>
    `
    : `<div class="notice">${escapeHtml(statusText)}</div>`

  $('previewContent').innerHTML = `
    <div class="preview-meta">
      <strong>${escapeHtml(data.path)}</strong>
      <span>dialog ${escapeHtml(entry.dialog_key)} · ${escapeHtml(statusText)}</span>
      <span class="mono">media ${escapeHtml(entry.media_id)}</span>
      ${mediaDetails}
    </div>
    ${player}
  `
}

function renderWwiseMediaPreview(data) {
  const media = data.media
  $('openRawLink').textContent = '打开 WAV'
  $('openRawLink').href = data.rawUrl
  $('downloadLink').textContent = '下载 WAV'
  $('downloadLink').href = data.wavDownloadUrl
  $('previewContent').innerHTML = `
    <div class="preview-meta">
      <strong>Media ${escapeHtml(media.media_id)}</strong>
      <span>${escapeHtml(media.logical_path)}</span>
      <span>PCK ${formatInt(media.pck_file_id)} · offset ${formatInt(media.offset)} · len ${formatInt(media.size)}</span>
      <span>${escapeHtml(media.source)} · ${escapeHtml(media.language || 'sfx')}${media.bank_id == null ? '' : ` · bank ${escapeHtml(media.bank_id)}`}</span>
    </div>
    <audio class="audio-preview" src="${escapeHtml(data.rawUrl)}" controls></audio>
    <div class="preview-actions inline-actions">
      <a class="link-button" href="${escapeHtml(data.wemDownloadUrl)}">下载 WEM</a>
      <a class="link-button" href="${escapeHtml(data.wavDownloadUrl)}">下载 WAV</a>
    </div>
  `
}

function renderWwiseEventPreview(data) {
  const event = data.event
  resetPreviewActions()
  const relations = event.relations.length
    ? `<table class="string-probe-table">
        <thead><tr><th>来源</th><th>关系</th><th>目标</th><th>证据</th></tr></thead>
        <tbody>${event.relations.map((relation) => `
          <tr>
            <td class="mono">${escapeHtml(relation.source_kind)} ${escapeHtml(relation.source_id)}</td>
            <td>${escapeHtml(relation.relation)}</td>
            <td class="mono">${escapeHtml(relation.target_kind)} ${escapeHtml(relation.target_id)}</td>
            <td>${escapeHtml(relation.evidence)}</td>
          </tr>
        `).join('')}</tbody>
      </table>`
    : '<div class="empty small">没有恢复出可确认的关系</div>'
  const media = event.media.length
    ? event.media.map((item) => `
        <div class="wwise-media-card">
          <div class="preview-meta">
            <strong>Media ${escapeHtml(item.media_id)}</strong>
            <span>${escapeHtml(item.logical_path)} · ${escapeHtml(item.language || 'sfx')}</span>
          </div>
          <audio class="audio-preview" src="${escapeHtml(item.rawUrl)}" controls preload="none"></audio>
          <a class="link-button" href="${escapeHtml(item.wemDownloadUrl)}">下载 WEM</a>
        </div>
      `).join('')
    : '<div class="notice">关系图尚未连接到可读取的物理 Media。</div>'
  $('previewContent').innerHTML = `
    <div class="preview-meta">
      <strong>Event ${escapeHtml(event.event_id)}</strong>
      <span>${escapeHtml(event.logical_path)} · bank ${escapeHtml(event.bank_id)}</span>
      <span>${formatInt(event.relations.length)} 条关系 · ${formatInt(event.media_ids.length)} 个 Media ID · ${formatInt(event.media.length)} 个物理候选</span>
    </div>
    <div class="binary-json-section"><strong>关系链</strong>${relations}</div>
    <div class="binary-json-section"><strong>可播放媒体</strong>${media}</div>
  `
}

function renderWwiseBankPreview(data) {
  const bank = data.bank
  resetPreviewActions()
  $('previewContent').innerHTML = `
    <div class="preview-meta">
      <strong>Bank ${escapeHtml(bank.bank_id)}</strong>
      <span>${escapeHtml(bank.logical_path)} · PCK ${formatInt(bank.pck_file_id)}</span>
      <span>offset ${formatInt(bank.offset)} · len ${formatInt(bank.size)}</span>
      <span>${formatInt(bank.object_count)} 个对象 · ${formatInt(bank.relation_count)} 条关系</span>
      <span>${formatInt(bank.diagnostic_count)} 个未解析关系诊断</span>
    </div>
    <table class="string-probe-table">
      <thead><tr><th>HIRC 类型</th><th>数量</th></tr></thead>
      <tbody>${bank.object_kinds.map((item) => `
        <tr><td>${escapeHtml(item.kind)}</td><td>${formatInt(item.count)}</td></tr>
      `).join('')}</tbody>
    </table>
    ${bank.diagnostics.length ? `
      <div class="binary-json-section">
        <strong>未解析关系</strong>
        ${bank.diagnostics.map((item) => `
          <div class="notice"><span class="mono">${escapeHtml(item.object_kind)} ${escapeHtml(item.object_id)}</span> · ${escapeHtml(item.message)}</div>
        `).join('')}
      </div>
    ` : ''}
  `
}

function renderConvertedFormat(data) {
  if (data.encoding === 'memorypack-json') {
    const details = data.memoryPack || {}
    const consumption = details.complete
      ? `完整消费 ${formatInt(details.consumed)} / ${formatInt(details.bytes)} bytes`
      : `已消费 ${formatInt(details.consumed)} / ${formatInt(details.bytes)} bytes`
    return `
      <span class="preview-format-badge memorypack">MemoryPack → JSON</span>
      <span class="preview-format-detail">${escapeHtml(details.class || '未知根类型')} · ${consumption}</span>
    `
  }
  if (data.encoding === 'sparkbuffer-json') {
    return '<span class="preview-format-badge sparkbuffer">SparkBuffer → JSON</span>'
  }
  return ''
}

function renderPreview(data) {
  if (data.kind === 'audioDialog') {
    renderAudioDialogPreview(data)
    return
  }
  if (data.kind === 'wwiseMedia') {
    renderWwiseMediaPreview(data)
    return
  }
  if (data.kind === 'wwiseEvent') {
    renderWwiseEventPreview(data)
    return
  }
  if (data.kind === 'wwiseBank') {
    renderWwiseBankPreview(data)
    return
  }
  const file = data.file
  const resolved = data.resolvedFile
  $('openRawLink').href = data.rawUrl
  $('downloadLink').href = data.downloadUrl

  const fallback = data.usedFallback
    ? `<div class="notice">当前记录的 chunk 不可用，已回落到 ${escapeHtml(resolved.source)}。</div>`
    : ''
  const assetDetails = renderAssetSummary(data.asset)
  const convertedFormat = renderConvertedFormat(data)
  const meta = `
    <div class="preview-meta">
      <strong>${escapeHtml(file.source_logical_id)}</strong>
      <span>chunk: ${escapeHtml(resolved.chunk_file)}</span>
      <span>offset ${formatInt(resolved.offset)} · len ${formatInt(resolved.length)}</span>
      <span>${resolved.encrypted ? 'encrypted' : 'plain'} · ${resolved.chunk_exists ? 'chunk ok' : 'missing chunk'}</span>
      ${convertedFormat}
    </div>
    ${fallback}
    ${assetDetails}
  `
  const message = data.message ? `<div class="notice">${escapeHtml(data.message)}</div>` : ''
  const convertedActions = data.convertedRawUrl
    ? `
      <div class="preview-actions inline-actions">
        <a class="link-button" href="${escapeHtml(data.convertedRawUrl)}" target="_blank" rel="noreferrer">打开转换结果</a>
        <a class="link-button" href="${escapeHtml(data.convertedDownloadUrl || data.convertedRawUrl)}">下载转换结果</a>
      </div>
    `
    : ''

  if (data.kind === 'text') {
    $('previewContent').innerHTML = `
      ${meta}
      ${message}
      ${convertedActions}
      ${data.truncated ? '<div class="notice">文件较大，仅显示前段内容。</div>' : ''}
      <pre class="preview-text">${escapeHtml(data.text)}</pre>
    `
    return
  }
  if (data.kind === 'binaryJson') {
    $('previewContent').innerHTML = `
      ${meta}
      <div class="notice">${escapeHtml(data.message || '该 .json 文件不是文本 JSON，当前显示二进制结构探针。')}</div>
      ${renderBinaryJsonProbe(data.probe)}
      ${data.truncated ? '<div class="notice">文件较大，仅分析并显示前段内容。</div>' : ''}
      <pre class="preview-text mono">${escapeHtml(data.hex)}</pre>
    `
    return
  }
  if (data.kind === 'hex') {
    $('previewContent').innerHTML = `
      ${meta}
      <div class="notice">${escapeHtml(data.message || '当前显示十六进制预览。')}</div>
      ${data.truncated ? '<div class="notice">文件较大，仅显示前段内容。</div>' : ''}
      <pre class="preview-text mono">${escapeHtml(data.hex)}</pre>
    `
    return
  }
  if (data.kind === 'image') {
    $('previewContent').innerHTML = `${meta}${message}<img class="media-preview" src="${data.rawUrl}" alt="${escapeHtml(file.name || file.file_name)}" />`
    return
  }
  if (data.kind === 'cubemap') {
    const faces = data.faces.map((face) => `
      <figure class="cubemap-face">
        <img src="${escapeHtml(face.rawUrl)}" alt="${escapeHtml(face.name)}" />
        <figcaption>
          <strong>${escapeHtml(face.name)}</strong>
          <span>${formatBytes(face.size)}</span>
          <a href="${escapeHtml(face.downloadUrl)}">下载</a>
        </figcaption>
      </figure>
    `).join('')
    $('previewContent').innerHTML = `
      ${meta}
      ${message}
      <div class="cubemap-grid">${faces}</div>
    `
    return
  }
  if (data.kind === 'video') {
    $('previewContent').innerHTML = `${meta}${message}<video class="media-preview" src="${data.rawUrl}" controls></video>`
    return
  }
  if (data.kind === 'audio') {
    $('previewContent').innerHTML = `${meta}${message}<audio class="audio-preview" src="${data.rawUrl}" controls></audio>`
    return
  }
  if (data.kind === 'container') {
    $('previewContent').innerHTML = `
      ${meta}
      <div class="container-preview">
        <strong>二级容器</strong>
        <p>${escapeHtml(data.message)}</p>
        <button id="internalListButton">查看内部结构</button>
        <div id="internalList" class="internal-list"></div>
      </div>
    `
    $('internalListButton').addEventListener('click', async () => {
      await loadInternalList(file.id, '')
    })
    return
  }
  $('previewContent').innerHTML = `${meta}<div class="empty">暂不支持预览该文件类型，请下载查看。</div>`
}

function renderPlanAsset(asset, fallback = '未解析') {
  if (!asset) return `<span class="avatar-plan-missing">${escapeHtml(fallback)}</span>`
  return `
    <div class="avatar-plan-asset">
      <strong>${escapeHtml(asset.name || asset.path || '未命名资源')}</strong>
      <span class="mono">${escapeHtml(asset.path || '')}</span>
      <span class="mono">${escapeHtml(asset.bundleName || '')}</span>
    </div>
  `
}

function renderAvatarPlan(data) {
  disposeModelViewer()
  const plan = data.plan || {}
  const summary = data.summary || {}
  const parts = plan.parts || []
  const bundles = plan.bundles || []
  const unresolved = Number(summary.unresolvedReferenceCount || 0)
  const complete = unresolved === 0 && plan.avatarAsset && parts.length > 0

  $('openRawLink').textContent = '打开 JSON'
  $('openRawLink').href = data.planUrl
  $('downloadLink').removeAttribute('href')
  $('downloadLink').textContent = '无需下载'
  $('previewContent').innerHTML = `
    <section class="avatar-plan-header">
      <div>
        <span class="avatar-plan-eyebrow">AvatarMesh 资源计划</span>
        <h3>${escapeHtml(plan.name || data.asset?.name || 'AvatarMesh')}</h3>
        <p class="mono">${escapeHtml(data.asset?.path || '')}</p>
      </div>
      <span class="avatar-plan-state ${complete ? 'complete' : 'incomplete'}">
        ${complete ? '引用完整' : `${unresolved} 项未解析`}
      </span>
    </section>
    <div class="avatar-plan-metrics">
      <div><span>LOD</span><strong>${formatInt(plan.lod)}</strong></div>
      <div><span>槽位</span><strong>${formatInt(summary.slotCount)}</strong></div>
      <div><span>部件</span><strong>${formatInt(parts.length)}</strong></div>
      <div><span>直接 Bundle</span><strong>${formatInt(bundles.length)}</strong></div>
    </div>
    ${summary.mainPrefabHashResolved ? '' : '<div class="notice">mainPrefabHash 未还原；这不影响已列出的 Avatar、Mesh 和 Material 引用验证。</div>'}
    <section class="avatar-plan-section">
      <h4>Avatar</h4>
      ${renderPlanAsset(plan.avatarAsset, '未解析 Avatar')}
    </section>
    <section class="avatar-plan-section">
      <h4>LOD ${formatInt(plan.lod)} 部件</h4>
      <div class="avatar-part-list">
        ${parts.map((part) => `
          <article class="avatar-part ${part.meshAsset ? '' : 'has-error'}">
            <header>
              <div>
                <strong>${escapeHtml(part.meshName || `部件 ${part.meshIndex}`)}</strong>
                <span>${escapeHtml(part.slotName || `槽位 ${part.slotIndex}`)}</span>
              </div>
              <span class="tag ${part.active ? '' : 'warn'}">${part.active ? '启用' : '停用'}</span>
            </header>
            <dl>
              <dt>根骨</dt><dd>${escapeHtml(part.rootBoneName || '未配置')}</dd>
              <dt>Mesh</dt><dd>${renderPlanAsset(part.meshAsset, '未解析 Mesh')}</dd>
              <dt>材质</dt><dd class="avatar-material-list">
                ${(part.materialAssets || []).length
                  ? part.materialAssets.map((asset) => renderPlanAsset(asset, '未解析 Material')).join('')
                  : '<span class="avatar-plan-missing">未配置材质</span>'}
              </dd>
            </dl>
          </article>
        `).join('') || '<div class="empty">当前 LOD 没有部件</div>'}
      </div>
    </section>
    <details class="avatar-plan-bundles">
      <summary>直接 Bundle <span>${formatInt(bundles.length)}</span></summary>
      <p>此列表只表示 Avatar、Mesh 和 Material 直接所在的 Bundle，实际导出还需要计算纹理、Shader 等传递依赖。</p>
      <div class="avatar-bundle-list">
        ${bundles.map((bundle) => `<span class="mono">${escapeHtml(bundle.bundleName || '')}</span>`).join('')}
      </div>
    </details>
  `
}

async function loadInternalList(fileId, path = '', page = 1) {
  const target = $('internalList')
  target.innerHTML = '<div class="empty">正在解析内部结构...</div>'
  try {
    const params = new URLSearchParams({ id: fileId, path, page, pageSize: PAGE_SIZE })
    const data = await getJson(`/api/internal/list?${params}`)
    renderInternalList(fileId, data)
  } catch (error) {
    target.innerHTML = `<div class="notice">内部结构读取失败：${escapeHtml(error.message)}</div>`
  }
}

function renderInternalList(fileId, data) {
  const target = $('internalList')
  if (data.status !== 'ready') {
    target.innerHTML = `
      <div class="notice">${escapeHtml(data.message || '该容器暂未接入内部解析。')}</div>
      <pre class="preview-text mono">${escapeHtml(JSON.stringify(data, null, 2))}</pre>
    `
    return
  }

  const packageMeta = data.kind === 'audioPackage' && data.meta
    ? `<span>${formatInt(data.meta.entryCount || 0)} WEM · WAV ${data.meta.wavPreviewAvailable ? '可预览' : '缺少转码工具'}</span>`
    : ''
  const videoMeta = data.kind === 'criVideo' && data.meta
    ? `<span>MP4 · ${data.meta.usmConvertAvailable ? '外部转换可用' : '内置抽流'}</span>`
    : ''
  const filePage = data.filePage || { page: 1, pages: 1, total: data.files.length }
  const parts = data.path ? data.path.split('/') : []
  const crumbs = [{ label: '内部根目录', path: '' }]
  let current = ''
  for (const part of parts) {
    current = current ? `${current}/${part}` : part
    crumbs.push({ label: part, path: current })
  }

  target.innerHTML = `
    <div class="internal-toolbar">
      <div class="internal-crumbs">
        ${crumbs.map((crumb) => `<button data-internal-path="${escapeHtml(crumb.path)}">${escapeHtml(crumb.label)}</button>`).join('<span>/</span>')}
      </div>
      <span>${formatInt(data.files.length)} files</span>
      ${packageMeta}
      ${videoMeta}
      ${filePage.pages > 1 ? `
        <button data-internal-page="${filePage.page - 1}" ${filePage.page <= 1 ? 'disabled' : ''}>上一页</button>
        <span>${filePage.page} / ${filePage.pages}</span>
        <button data-internal-page="${filePage.page + 1}" ${filePage.page >= filePage.pages ? 'disabled' : ''}>下一页</button>
      ` : ''}
    </div>
    ${renderInternalHelp(data)}
    <div id="internalPreview" class="internal-preview">
      <div class="empty">选择内部文件进行预览</div>
    </div>
    <div class="internal-grid">
      ${data.dirs
        .map((dir) => `
          <button class="internal-entry dir" data-internal-path="${escapeHtml(dir.path)}">
            <strong>${escapeHtml(dir.name)}</strong>
            <span>${formatInt(dir.fileCount)} files · ${formatBytes(dir.totalBytes)}</span>
          </button>
        `)
        .join('')}
      ${data.files
        .map((file) => `
          <button class="internal-entry file" data-internal-file="${escapeHtml(file.lookup || file.path)}">
            <strong>${escapeHtml(file.name)}</strong>
            <span>${escapeHtml(file.kind)} · ${formatBytes(file.size)}</span>
            ${renderAssetSummary(file.asset)}
          </button>
        `)
        .join('')}
      ${!data.dirs.length && !data.files.length ? '<div class="empty">内部目录为空</div>' : ''}
    </div>
  `
  target.querySelectorAll('[data-internal-path]').forEach((button) => {
    button.addEventListener('click', () => loadInternalList(fileId, button.dataset.internalPath))
  })
  target.querySelectorAll('[data-internal-page]').forEach((button) => {
    button.addEventListener('click', () => loadInternalList(fileId, data.path, Number(button.dataset.internalPage)))
  })
  target.querySelectorAll('[data-internal-file]').forEach((button) => {
    button.addEventListener('click', () => {
      target.querySelectorAll('[data-internal-file]').forEach((entry) => {
        entry.classList.toggle('selected', entry === button)
      })
      loadInternalPreview(fileId, button.dataset.internalFile)
    })
  })
}

function renderInternalHelp(data) {
  const helpByKind = {
    assetBundle: [
      '内部目录是按需导出的 Unity 资源类型目录。',
      'Texture2D、Sprite、TextAsset 等不是原始磁盘目录，而是从 AssetBundle 中解析出的资源分类。',
      '资源卡片中的 Container 字段通常最接近游戏内原始 asset 路径。',
    ],
    audioPackage: [
      'wem 是 PCK 中真实存放的 Wwise 音频条目，点击后可下载解密后的原始 WEM。',
      'wav 是为了浏览器预览虚拟出来的目录，点击后会按需把对应 WEM 转码为 WAV 并缓存。',
      '目录里显示的 WAV 大小是转码前的 WEM 占位大小，实际生成后可能不同。',
    ],
    criVideo: [
      'mp4 是为了浏览器预览虚拟出来的目录。',
      '点击 MP4 文件时会按需把 CRI/USM 视频转换为 MP4 并缓存；原始 .usm 不会被修改。',
    ],
  }
  const lines = helpByKind[data.kind]
  if (!lines) return ''
  return `
    <div class="internal-help">
      ${lines.map((line) => `<p>${escapeHtml(line)}</p>`).join('')}
    </div>
  `
}

async function loadInternalPreview(fileId, path) {
  const target = $('internalPreview')
  target.innerHTML = '<div class="empty">正在读取内部文件...</div>'
  try {
    const params = new URLSearchParams({ id: fileId, path })
    const data = await getJson(`/api/internal/preview?${params}`)
    renderInternalPreview(data)
  } catch (error) {
    target.innerHTML = `<div class="notice">内部文件读取失败：${escapeHtml(error.message)}</div>`
  }
}

function renderInternalPreview(data) {
  const target = $('internalPreview')
  const rawUrl = escapeHtml(data.rawUrl)
  const downloadUrl = escapeHtml(data.downloadUrl)
  const actions = `
    <div class="preview-actions">
      <a class="link-button" href="${rawUrl}" target="_blank" rel="noreferrer">打开</a>
      <a class="link-button" href="${downloadUrl}">下载</a>
    </div>
  `
  const heading = `
    <div class="internal-preview-title">
      <strong>${escapeHtml(data.path)}</strong>
      <span>${formatBytes(data.size)}</span>
      ${actions}
    </div>
    ${renderAssetSummary(data.asset)}
    ${data.audioEntry ? renderAssetSummary({
      Type: 'WEM',
      Name: data.audioEntry.id,
      Container: `wwise/${data.audioEntry.id}.wem`,
      Source: data.audioEntry.source,
      PathID: data.audioEntry.id,
    }) : ''}
  `
  if (data.kind === 'image') {
    target.innerHTML = `${heading}<img class="media-preview" src="${rawUrl}" alt="${escapeHtml(data.name)}" />`
    return
  }
  if (data.kind === 'video') {
    target.innerHTML = `${heading}<video class="media-preview" src="${rawUrl}" controls></video>`
    return
  }
  if (data.kind === 'audio') {
    target.innerHTML = `${heading}<audio class="audio-preview" src="${rawUrl}" controls></audio>`
    return
  }
  if (data.kind === 'text') {
    target.innerHTML = `${heading}${data.truncated ? '<div class="notice">文件较大，仅显示前段内容。</div>' : ''}<pre class="preview-text">${escapeHtml(data.text)}</pre>`
    return
  }
  if (data.kind === 'hex') {
    target.innerHTML = `${heading}${data.truncated ? '<div class="notice">文件较大，仅显示前段内容。</div>' : ''}<pre class="preview-text mono">${escapeHtml(data.hex)}</pre>`
    return
  }
  target.innerHTML = `${heading}<div class="empty">暂不支持预览该内部文件。</div>`
}

async function selectFile(fileId, { updateRoute = true } = {}) {
  cancelActiveModelTask()
  state.selectedFileId = fileId
  state.selectedFileKey = `id:${fileId}`
  state.routeSelection = { kind: 'file', fileId, fileKey: state.selectedFileKey }
  if (updateRoute) writeRoute()
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === state.selectedFileKey)
  })
  renderPreviewLoading()
  const data = await getJson(`/api/preview?id=${fileId}`)
  renderPreview(data)
}

async function selectVirtualFile(previewUrl, fileKey, { updateRoute = true } = {}) {
  const normalizedUrl = normalizePreviewUrl(previewUrl)
  if (!normalizedUrl) throw new Error('Invalid preview URL')
  cancelActiveModelTask()
  state.selectedFileId = null
  state.selectedFileKey = fileKey
  state.routeSelection = { kind: 'virtual', previewUrl: normalizedUrl, fileKey }
  if (updateRoute) writeRoute()
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === fileKey)
  })
  renderPreviewLoading()
  try {
    const data = await getJson(normalizedUrl)
    renderPreview(data)
  } catch (error) {
    $('previewContent').innerHTML = `<div class="notice">资源读取失败：${escapeHtml(error.message)}</div>`
  }
}

async function selectModel(modelUrl, fileKey, { updateRoute = true } = {}) {
  const normalizedUrl = normalizePreviewUrl(modelUrl)
  if (!normalizedUrl) throw new Error('Invalid model URL')
  const params = new URL(normalizedUrl, window.location.origin).searchParams
  const manifestId = params.get('manifestId')
  const assetIndex = params.get('assetIndex')
  if (!manifestId || !assetIndex) throw new Error('Model URL is missing its asset identity')
  state.selectedFileId = null
  state.selectedFileKey = fileKey
  state.routeSelection = {
    kind: 'model',
    manifestId,
    assetIndex,
    lod: params.get('lod'),
    animationAssetIndex: params.get('animationAssetIndex'),
    fileKey,
  }
  if (updateRoute) writeRoute()
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === fileKey)
  })
  cancelActiveModelTask()
  const taskSerial = state.modelTaskSerial
  renderPreviewLoading()
  try {
    const task = await postJson('/api/tasks/model', {
      manifestId,
      assetIndex,
      lod: params.get('lod') || 0,
      animationAssetIndex: params.get('animationAssetIndex'),
    })
    if (state.modelTaskSerial !== taskSerial || state.selectedFileKey !== fileKey) {
      fetch(`/api/task?taskId=${encodeURIComponent(task.taskId)}`, { method: 'DELETE' }).catch(() => {})
      return
    }
    state.modelTaskId = task.taskId
    const result = await waitForTask(task.taskId, (progress) => {
      if (state.modelTaskSerial === taskSerial && state.modelTaskId === task.taskId && state.selectedFileKey === fileKey) {
        renderModelTaskProgress(progress)
      }
    })
    if (state.modelTaskSerial !== taskSerial || state.modelTaskId !== task.taskId || state.selectedFileKey !== fileKey) return
    state.modelTaskId = null
    renderModelPreview(result)
  } catch (error) {
    if (state.modelTaskSerial !== taskSerial || state.selectedFileKey !== fileKey) return
    state.modelTaskId = null
    $('previewContent').innerHTML = `<div class="notice">模型读取失败：${escapeHtml(error.message)}</div>`
  }
}

async function selectAvatarPlan(planUrl, fileKey, { updateRoute = true } = {}) {
  const normalizedUrl = normalizePreviewUrl(planUrl)
  if (!normalizedUrl) throw new Error('Invalid avatar plan URL')
  const params = new URL(normalizedUrl, window.location.origin).searchParams
  const manifestId = params.get('manifestId')
  const assetIndex = params.get('assetIndex')
  if (!manifestId || !assetIndex) throw new Error('Avatar plan URL is missing its asset identity')
  cancelActiveModelTask()
  state.selectedFileId = null
  state.selectedFileKey = fileKey
  state.routeSelection = {
    kind: 'avatarPlan',
    manifestId,
    assetIndex,
    lod: params.get('lod') || '0',
    fileKey,
  }
  if (updateRoute) writeRoute()
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === fileKey)
  })
  renderPreviewLoading()
  try {
    const data = await getJson(normalizedUrl)
    renderAvatarPlan({ ...data, planUrl: normalizedUrl })
  } catch (error) {
    $('previewContent').innerHTML = `<div class="notice">资源计划读取失败：${escapeHtml(error.message)}</div>`
  }
}

async function search() {
  const q = $('searchInput').value.trim()
  if (!q) return
  const params = new URLSearchParams({ scope: state.scope, q, limit: 100 })
  const data = await getJson(`/api/search?${params}`)
  $('dirCount').textContent = '搜索结果'
  $('dirList').innerHTML = '<div class="empty">搜索模式下不显示目录</div>'
  renderFiles(data.items, { page: 1, pages: 1, total: data.items.length })
}

function bindEvents() {
  $('scopeSelect').addEventListener('change', (event) => {
    navigateDirectory({ scope: event.target.value, path: '', page: 1 })
  })
  $('audioLanguageSelect').addEventListener('change', (event) => {
    navigateDirectory({ audioLanguage: event.target.value, path: '', page: 1 })
  })
  $('rootButton').addEventListener('click', () => {
    navigateDirectory({ path: '', page: 1 })
  })
  $('upButton').addEventListener('click', () => {
    const parts = state.path.split('/').filter(Boolean)
    parts.pop()
    navigateDirectory({ path: parts.join('/'), page: 1 })
  })
  $('prevPage').addEventListener('click', () => {
    if (state.page <= 1) return
    navigateDirectory({ page: state.page - 1 })
  })
  $('nextPage').addEventListener('click', () => {
    if (state.page >= state.pageInfo.pages) return
    navigateDirectory({ page: state.page + 1 })
  })
  $('searchButton').addEventListener('click', search)
  $('searchInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') search()
  })
}

function syncScopeControls() {
  const isAudioDialog = state.scope === AUDIO_DIALOG_SCOPE
  $('audioLanguageField').classList.toggle('hidden', !isAudioDialog)
  $('searchField').classList.toggle('hidden', isAudioDialog || state.scope === WWISE_SCOPE)
}

async function restoreRoute() {
  const query = new URLSearchParams(window.location.search)
  const requestedScope = query.get('scope')
  state.scope = requestedScope && state.availableScopes.has(requestedScope)
    ? requestedScope
    : 'effective'
  state.path = normalizeDirectoryPath(query.get('path'))
  state.page = parsePositiveInteger(query.get('page'))

  const requestedLanguage = query.get('audioLanguage')
  const availableLanguages = new Set(
    Array.from($('audioLanguageSelect').options, (option) => option.value),
  )
  state.audioLanguage = requestedLanguage && availableLanguages.has(requestedLanguage)
    ? requestedLanguage
    : 'chinese'

  $('scopeSelect').value = state.scope
  $('audioLanguageSelect').value = state.audioLanguage
  syncScopeControls()
  clearPreviewSelection()
  await loadDirectory()

  const selectionKey = query.get('selectionKey')
  const avatarPlanManifestId = query.get('avatarPlanManifestId')
  const avatarPlanAssetIndex = query.get('avatarPlanAssetIndex')
  const modelManifestId = query.get('modelManifestId')
  const modelAssetIndex = query.get('modelAssetIndex')
  const animationAssetIndex = query.get('animationAssetIndex')
  const fileId = Number.parseInt(query.get('fileId'), 10)
  const previewUrl = normalizePreviewUrl(query.get('previewUrl'))

  if (avatarPlanManifestId && avatarPlanAssetIndex) {
    const params = new URLSearchParams({
      manifestId: avatarPlanManifestId,
      assetIndex: avatarPlanAssetIndex,
      lod: query.get('lod') || '0',
    })
    await selectAvatarPlan(
      `/api/manifest-asset/avatar-plan?${params}`,
      selectionKey || `avatar-plan:${avatarPlanManifestId}:${avatarPlanAssetIndex}`,
      { updateRoute: false },
    )
  } else if (modelManifestId && modelAssetIndex) {
    const params = new URLSearchParams({
      manifestId: modelManifestId,
      assetIndex: modelAssetIndex,
    })
    if (query.has('lod')) params.set('lod', query.get('lod'))
    if (animationAssetIndex) params.set('animationAssetIndex', animationAssetIndex)
    await selectModel(
      `/api/manifest-asset/model?${params}`,
      selectionKey || `model:${modelManifestId}:${modelAssetIndex}`,
      { updateRoute: false },
    )
  } else if (Number.isInteger(fileId) && fileId > 0) {
    await selectFile(fileId, { updateRoute: false })
  } else if (previewUrl) {
    await selectVirtualFile(
      previewUrl,
      selectionKey || `virtual:${previewUrl}`,
      { updateRoute: false },
    )
  }
}

async function init() {
  bindEvents()
  const manifest = await getJson('/api/manifest')
  state.availableScopes = new Set([
    ...manifest.scopes.map((item) => item.scope),
    AUDIO_DIALOG_SCOPE,
    WWISE_SCOPE,
  ])
  renderScopes(manifest.scopes)
  renderSummary(manifest.scopes)
  await restoreRoute()
  writeRoute({ replace: true })
  window.addEventListener('popstate', () => {
    restoreRoute().catch((error) => {
      $('previewContent').innerHTML = `<div class="notice">URL 鐘舵€佹仮澶嶅け璐ワ細${escapeHtml(error.message)}</div>`
    })
  })
}

init().catch((error) => {
  document.body.innerHTML = `<pre class="fatal">${escapeHtml(error.stack || error.message)}</pre>`
})
