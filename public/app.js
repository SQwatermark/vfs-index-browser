import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js'

const PAGE_SIZE = 100
const MANIFEST_VIRTUAL_DIR = '__manifest_assets__'

const state = {
  scope: 'effective',
  path: '',
  page: 1,
  pageInfo: { page: 1, pages: 1, total: 0 },
  selectedFileId: null,
  selectedFileKey: null,
  disposeModelViewer: null,
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
  $('dirCount').textContent = '?? ' + dirs.length
  $('dirList').innerHTML = dirs.length
    ? dirs
        .map((dir) => `
          <button class="dir-card ${dir.virtualKind ? 'virtual' : ''}" data-path="${escapeHtml(dir.path)}">
            <strong>${escapeHtml(dir.name)}</strong>
            <span>${formatInt(dir.file_count)} files · ${dir.virtualKind ? '虚拟目录' : formatBytes(dir.total_bytes)}</span>
            ${dir.virtualKind ? '<span class="tag">manifest</span>' : ''}
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
        .map((file) => {
          const rowKey = file.previewUrl ? `virtual:${file.previewUrl}` : `id:${file.id}`
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
              <div class="mono">offset ${formatInt(file.offset)}</div>
              <div class="mono">len ${formatInt(file.length)}</div>
            </td>
            <td>
              <span class="tag ${file.encrypted ? 'warn' : ''}">${file.virtualKind ? 'manifest' : file.encrypted ? 'encrypted' : 'plain'}</span>
              <span class="tag ${file.chunk_exists ? '' : 'danger'}">${file.chunk_exists ? 'chunk ok' : 'missing chunk'}</span>
              ${file.encrypted ? `<div class="mono muted">iv ${file.iv_seed}</div>` : ''}
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
  renderDirs(data.dirs, data.virtual)
  renderFiles(data.files, data.filePage)
}

function renderPreviewLoading() {
  disposeModelViewer()
  $('previewContent').innerHTML = '<div class="empty">正在读取文件...</div>'
  $('openRawLink').removeAttribute('href')
  $('downloadLink').removeAttribute('href')
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

function renderModelPreview(data) {
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
    <div id="modelViewport" class="model-viewport">
      <label class="model-view-option">
        <input id="modelOutlineToggle" type="checkbox" checked />
        <span>轮廓线</span>
      </label>
      <div id="modelLoading" class="model-loading">正在加载 GLB...</div>
    </div>
  `

  const viewport = $('modelViewport')
  const loading = $('modelLoading')
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
  let active = true
  const clock = new THREE.Clock()
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

  new GLTFLoader().load(
    glbUrl,
    (gltf) => {
      if (!active) {
        disposeModelResources(gltf.scene)
        return
      }
      model = new THREE.Group()
      model.name = 'EndfieldModelPreview'
      model.add(gltf.scene)
      const outline = createOutlineModel(gltf.scene)
      model.add(outline)
      if (gltf.animations.length) {
        mixers = [gltf.scene, outline].map((root) => {
          const mixer = new THREE.AnimationMixer(root)
          mixer.clipAction(gltf.animations[0]).play()
          return mixer
        })
      }
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
      loading.remove()
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
    for (const mixer of mixers) mixer.update(delta)
    controls.update()
    renderer.render(scene, camera)
  })
  state.disposeModelViewer = () => {
    active = false
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

function renderPreview(data) {
  const file = data.file
  const resolved = data.resolvedFile
  $('openRawLink').href = data.rawUrl
  $('downloadLink').href = data.downloadUrl

  const fallback = data.usedFallback
    ? `<div class="notice">当前记录的 chunk 不可用，已回落到 ${escapeHtml(resolved.source)}。</div>`
    : ''
  const assetDetails = renderAssetSummary(data.asset)
  const meta = `
    <div class="preview-meta">
      <strong>${escapeHtml(file.source_logical_id)}</strong>
      <span>chunk: ${escapeHtml(resolved.chunk_file)}</span>
      <span>offset ${formatInt(resolved.offset)} · len ${formatInt(resolved.length)}</span>
      <span>${resolved.encrypted ? 'encrypted' : 'plain'} · ${resolved.chunk_exists ? 'chunk ok' : 'missing chunk'}</span>
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

async function selectFile(fileId) {
  state.selectedFileId = fileId
  state.selectedFileKey = `id:${fileId}`
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === state.selectedFileKey)
  })
  renderPreviewLoading()
  const data = await getJson(`/api/preview?id=${fileId}`)
  renderPreview(data)
}

async function selectVirtualFile(previewUrl, fileKey) {
  state.selectedFileId = null
  state.selectedFileKey = fileKey
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === fileKey)
  })
  renderPreviewLoading()
  try {
    const data = await getJson(previewUrl)
    renderPreview(data)
  } catch (error) {
    $('previewContent').innerHTML = `<div class="notice">资源读取失败：${escapeHtml(error.message)}</div>`
  }
}

async function selectModel(modelUrl, fileKey) {
  state.selectedFileId = null
  state.selectedFileKey = fileKey
  document.querySelectorAll('#fileRows tr[data-file-key]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.fileKey === fileKey)
  })
  renderPreviewLoading()
  try {
    renderModelPreview(await getJson(modelUrl))
  } catch (error) {
    $('previewContent').innerHTML = `<div class="notice">模型读取失败：${escapeHtml(error.message)}</div>`
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
  const query = new URLSearchParams(window.location.search)
  const manifestId = query.get('modelManifestId')
  const assetIndex = query.get('modelAssetIndex')
  const animationAssetIndex = query.get('animationAssetIndex')
  if (manifestId && assetIndex) {
    const params = new URLSearchParams({ manifestId, assetIndex })
    if (animationAssetIndex) params.set('animationAssetIndex', animationAssetIndex)
    const modelUrl = `/api/manifest-asset/model?${params}`
    await selectModel(modelUrl, `model:${manifestId}:${assetIndex}`)
  }
}

init().catch((error) => {
  document.body.innerHTML = `<pre class="fatal">${escapeHtml(error.stack || error.message)}</pre>`
})
