const PAGE_SIZE = 100

const state = {
  scope: 'effective',
  path: '',
  page: 1,
  pageInfo: { page: 1, pages: 1, total: 0 },
  selectedFileId: null,
}

const scopeNames = {
  effective: 'Effective',
  Persistent: 'Persistent',
  StreamingAssets: 'StreamingAssets',
  all: 'All Sources',
}

const $ = (id) => document.getElementById(id)

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
    throw new Error(`${response.status} ${response.statusText}`)
  }
  return response.json()
}

function renderScopes(scopes) {
  const select = $('scopeSelect')
  select.innerHTML = scopes
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
      state.scope = card.dataset.scope
      state.path = ''
      state.page = 1
      $('scopeSelect').value = state.scope
      loadDirectory()
    })
  })
}

function renderBreadcrumbs() {
  const parts = state.path ? state.path.split('/') : []
  const crumbs = [{ label: scopeNames[state.scope] || state.scope, path: '' }]
  let current = ''
  for (const part of parts) {
    current = current ? `${current}/${part}` : part
    crumbs.push({ label: part, path: current })
  }
  $('breadcrumbs').innerHTML = crumbs
    .map((crumb, index) => `
      <button class="crumb" data-path="${escapeHtml(crumb.path)}">${escapeHtml(crumb.label)}</button>
      ${index < crumbs.length - 1 ? '<span>/</span>' : ''}
    `)
    .join('')
  document.querySelectorAll('.crumb').forEach((button) => {
    button.addEventListener('click', () => {
      state.path = button.dataset.path
      state.page = 1
      loadDirectory()
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

function renderDirs(dirs) {
  $('dirCount').textContent = `${dirs.length} 个子目录`
  $('dirList').innerHTML = dirs.length
    ? dirs
        .map((dir) => `
          <button class="dir-card" data-path="${escapeHtml(dir.path)}">
            <strong>${escapeHtml(dir.name)}</strong>
            <span>${formatInt(dir.file_count)} files · ${formatBytes(dir.total_bytes)}</span>
            ${dir.missing_chunk_count ? `<em>${formatInt(dir.missing_chunk_count)} missing chunks</em>` : ''}
          </button>
        `)
        .join('')
    : '<div class="empty">没有子目录</div>'
  document.querySelectorAll('.dir-card').forEach((card) => {
    card.addEventListener('click', () => {
      state.path = card.dataset.path
      state.page = 1
      loadDirectory()
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
        .map((file) => `
          <tr class="${state.selectedFileId === file.id ? 'selected' : ''}" data-file-id="${file.id}" title="${escapeHtml(file.path)}">
            <td>
              <div class="file-name">${escapeHtml(file.name)}</div>
              <div class="file-path">${escapeHtml(file.file_name)}</div>
            </td>
            <td>${escapeHtml(file.source)}</td>
            <td>${escapeHtml(file.block_name)}</td>
            <td>
              <div class="mono">${escapeHtml(file.chunk_file)}</div>
            </td>
            <td>
              <div class="mono">offset ${formatInt(file.offset)}</div>
              <div class="mono">len ${formatInt(file.length)}</div>
            </td>
            <td>
              <span class="tag ${file.encrypted ? 'warn' : ''}">${file.encrypted ? 'encrypted' : 'plain'}</span>
              <span class="tag ${file.chunk_exists ? '' : 'danger'}">${file.chunk_exists ? 'chunk ok' : 'missing chunk'}</span>
              ${file.encrypted ? `<div class="mono muted">iv ${file.iv_seed}</div>` : ''}
            </td>
          </tr>
        `)
        .join('')
    : '<tr><td colspan="6" class="empty">这个目录没有文件</td></tr>'
  document.querySelectorAll('#fileRows tr[data-file-id]').forEach((row) => {
    row.addEventListener('click', () => selectFile(Number(row.dataset.fileId)))
  })
}

async function loadDirectory() {
  renderBreadcrumbs()
  const params = new URLSearchParams({
    scope: state.scope,
    path: state.path,
    page: state.page,
    pageSize: PAGE_SIZE,
  })
  const data = await getJson(`/api/list?${params}`)
  renderStats(data.directory)
  renderDirs(data.dirs)
  renderFiles(data.files, data.filePage)
}

function renderPreviewLoading() {
  $('previewContent').innerHTML = '<div class="empty">正在读取文件...</div>'
  $('openRawLink').removeAttribute('href')
  $('downloadLink').removeAttribute('href')
}

function renderPreview(data) {
  const file = data.file
  const resolved = data.resolvedFile
  $('openRawLink').href = data.rawUrl
  $('downloadLink').href = data.downloadUrl

  const fallback = data.usedFallback
    ? `<div class="notice">当前记录的 chunk 不可用，已回落到 ${escapeHtml(resolved.source)}。</div>`
    : ''
  const meta = `
    <div class="preview-meta">
      <strong>${escapeHtml(file.source_logical_id)}</strong>
      <span>chunk: ${escapeHtml(resolved.chunk_file)}</span>
      <span>offset ${formatInt(resolved.offset)} · len ${formatInt(resolved.length)}</span>
      <span>${resolved.encrypted ? 'encrypted' : 'plain'} · ${resolved.chunk_exists ? 'chunk ok' : 'missing chunk'}</span>
    </div>
    ${fallback}
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
    $('previewContent').innerHTML = `${meta}<img class="media-preview" src="${data.rawUrl}" alt="${escapeHtml(file.name || file.file_name)}" />`
    return
  }
  if (data.kind === 'video') {
    $('previewContent').innerHTML = `${meta}<video class="media-preview" src="${data.rawUrl}" controls></video>`
    return
  }
  if (data.kind === 'audio') {
    $('previewContent').innerHTML = `${meta}<audio class="audio-preview" src="${data.rawUrl}" controls></audio>`
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

async function loadInternalList(fileId, path = '') {
  const target = $('internalList')
  target.innerHTML = '<div class="empty">正在解析内部结构...</div>'
  try {
    const params = new URLSearchParams({ id: fileId, path })
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
    </div>
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
          <button class="internal-entry file" data-internal-file="${escapeHtml(file.path)}">
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
  target.querySelectorAll('[data-internal-file]').forEach((button) => {
    button.addEventListener('click', () => {
      target.querySelectorAll('[data-internal-file]').forEach((entry) => {
        entry.classList.toggle('selected', entry === button)
      })
      loadInternalPreview(fileId, button.dataset.internalFile)
    })
  })
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

async function selectFile(fileId) {
  state.selectedFileId = fileId
  document.querySelectorAll('#fileRows tr[data-file-id]').forEach((row) => {
    row.classList.toggle('selected', Number(row.dataset.fileId) === fileId)
  })
  renderPreviewLoading()
  const data = await getJson(`/api/preview?id=${fileId}`)
  renderPreview(data)
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
    state.scope = event.target.value
    state.path = ''
    state.page = 1
    loadDirectory()
  })
  $('rootButton').addEventListener('click', () => {
    state.path = ''
    state.page = 1
    loadDirectory()
  })
  $('upButton').addEventListener('click', () => {
    const parts = state.path.split('/').filter(Boolean)
    parts.pop()
    state.path = parts.join('/')
    state.page = 1
    loadDirectory()
  })
  $('prevPage').addEventListener('click', () => {
    if (state.page <= 1) return
    state.page -= 1
    loadDirectory()
  })
  $('nextPage').addEventListener('click', () => {
    if (state.page >= state.pageInfo.pages) return
    state.page += 1
    loadDirectory()
  })
  $('searchButton').addEventListener('click', search)
  $('searchInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') search()
  })
}

async function init() {
  bindEvents()
  const manifest = await getJson('/api/manifest')
  renderScopes(manifest.scopes)
  renderSummary(manifest.scopes)
  await loadDirectory()
}

init().catch((error) => {
  document.body.innerHTML = `<pre class="fatal">${escapeHtml(error.stack || error.message)}</pre>`
})
