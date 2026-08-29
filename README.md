# Endfield VFS Index Browser

本项目是一个独立的《明日方舟：终末地》本地资源浏览器。它读取本机游戏资源索引，不依赖远程 CDN，也不属于 Endaxis。

## 能力

- 按 `Effective`、`Persistent`、`StreamingAssets` 和 `All Sources` 浏览 VFS 逻辑路径。
- 根据索引中的 chunk、offset、length 和 IV 读取并解密单个文件。
- 在 `manifest.hgmmap` 同级提供虚拟目录，浏览完整 AssetInfo 逻辑树。
- 按需解析单个 AssetBundle，不预扫描全部 `.ab` 文件。
- 预览文本、图片、音频和视频，并下载原始或转换后的文件。
- 按逻辑路径定位 AnimationClip 等 Unity 子资源，并处理无 Container 的命名子资源。
- 解析 TableCfg/SparkBuffer，并实验性解析 JsonData/MemoryPack 二进制配置。
- 按 `projectileId` 精确定位并按需解析 ProjectileComponentData 及其 Unity 对象。
- 按需恢复 Prefab 的组合模型，并以自包含 GLB 在浏览器中预览或下载。
- 将缓存 GLB 派生为贴图内嵌、可继续编辑的 Blender 文件。

## 架构原则

资源定位和资源内容解析分为两层：

1. `manifest.hgmmap` 提供逻辑资源路径、所属 Bundle 和大小，是虚拟目录的数据源。
2. 用户点击虚拟目录中的资源时，服务定位 Effective `.ab`，再按 AssetMap 的 `Container` 精确查找导出内容。

因此，包含 `.ab` 的普通文件夹不会生成额外虚拟目录，也不需要运行耗时的全量 AnimeStudio 扫描。详细设计见 [docs/design/architecture.md](docs/design/architecture.md)。

## 启动

安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

首次从 JSONL/TGZ 索引构建 SQLite 并启动：

```powershell
python server.py --rebuild
```

已有 `data/endfield-vfs-index.sqlite` 时直接启动：

```powershell
python server.py
```

启动会校验历史 chunk 与已记录的 `.blc` 内容身份。索引陈旧时，服务先在同目录临时 run 中
重新生成 JSONL 和候选 SQLite，完成完整性及来源身份验证后原子切换；失败则保留旧库并以
`degraded` 启动。诊断时可用 `--no-auto-rebuild` 只报告陈旧状态。此时数据 API 返回 HTTP 503
及 `code: index_stale`；健康检查、既有任务状态/取消/产物和静态页面仍可使用。

默认地址为 `http://127.0.0.1:8765`。局域网访问可添加 `--host 0.0.0.0`。
`VFS_BROWSER_HOST` 和 `VFS_BROWSER_PORT` 可设置持久默认值，命令行参数优先。

持久数据默认位于仓库的 `data/`。可用一个环境变量整体迁移数据库、缓存和派生索引：

```powershell
$env:VFS_BROWSER_DATA_ROOT = "D:\EndfieldTools\vfs-data"
```

`VFS_BROWSER_DB`、`VFS_BROWSER_INDEX`、`VFS_BROWSER_INTERNAL_CACHE`、
`VFS_BROWSER_AUDIO_DIALOG_DB`、`VFS_BROWSER_WWISE_DB` 和
`VFS_BROWSER_SHADER_ARCHIVE_ROOT` 可继续覆盖单项。默认配置不再引用相邻 Endaxis 仓库。

服务日志默认向 stderr 输出单行 JSON，可直接交给日志采集器；本地人工调试可使用
`--log-format text`。`VFS_BROWSER_LOG_FORMAT` 和 `VFS_BROWSER_LOG_LEVEL` 可设置默认格式与
级别，命令行 `--log-format`、`--log-level` 仍可逐次覆盖。

常用参数：

```powershell
python server.py `
  --index D:\path\to\endfield-vfs-index.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 0.0.0.0 `
  --port 8765 `
  --rebuild
```

MonoBehaviour Raw、TypeTree Dump、Projectile 领域解码、AssetMap/CABMap、通用 Bundle 预览、
对象快照、模型纹理、Cubemap 和动画均由仓库内 `unity-worker/` 提供；开发构建可通过
`VFS_BROWSER_UNITY_WORKER` 显式覆盖。内部索引和导出缓存位于 `data/internal-cache/`，每次
worker 操作写入独占 run，校验后才原子发布当前指针。生产服务不再定位或调用旧
AnimeStudio CLI。

manifest 中的 `.asset`、`.prefab` 如果无法按常规资源类型导出，服务会让 VFS worker 按
精确 container 尝试 TypeTree Dump，用于查看 VolumeProfile 等自定义 Unity 组件。

manifest 中的 Cubemap 会由 worker 按 container 精确导出六个面，并在预览区组成可逐面打开、
下载的画廊。

Cubemap 缓存会记录 VFS Unity worker 及其运行依赖的文件身份，worker 重新构建后自动失效。

模型预览页可以直接生成并下载 `.blend`，Prefab 与 AvatarMesh（含 `lod`）使用同一
导出链路。服务默认查找 PATH 中的 Blender，然后选择标准 Windows 安装目录下版本号
最高的 Blender；其他安装位置可配置：

```powershell
$env:BLENDER_EXE = "D:\Applications\Blender\blender.exe"
```

首次下载会在后台调用 Blender，后续请求复用模型缓存目录中的结果。GLB、材质
后端或光照代码更新后，缓存会自动重新生成。

## 主要模块

| 路径 | 职责 |
| --- | --- |
| `server.py` | VFS SQLite、HTTP API、文件读取以及各容器适配入口 |
| `runtime_config.py` | 数据根目录、数据库、缓存、schema 与外部工具的统一环境配置 |
| `cache_versions.py` | 服务端派生缓存产物的具名版本注册表 |
| `service_logging.py` | 单行 JSON/文本服务日志配置与格式化 |
| `tool_registry.py` | 可选外部工具的统一解析、能力查询与健康诊断 |
| `unity_worker.py` | VFS 自有 Unity worker 的唯一 Python 进程适配器与健康诊断 |
| `worker_run_service.py` | Unity worker 运行产物校验、缓存命中、失败清理与原子发布 |
| `unity-worker/` | 可独立构建和发布的 .NET Unity 资源 worker |
| `task_registry.py` | 任务状态落盘、原子结果发布与当前进程取消控制 |
| `task_service.py` | 后台任务状态、取消和产物查询的应用层边界 |
| `task_requests.py` | 模型、动画与 Blender 后台任务的严格输入 DTO |
| `task_operations.py` | 已解析领域输入到独立后台构建服务的任务组装 |
| `manifest_index.py` | HGM manifest 解析、派生 SQLite 缓存和逻辑目录查询 |
| `manifest_asset_requests.py` | manifest 资源身份与批量动画 query 的严格解析 |
| `manifest_asset_service.py` | manifest/asset 身份到本地可读 AssetBundle 的解析 |
| `manifest_worker_service.py` | manifest 资源的 Worker 输入暂存、领域导出与缓存编排 |
| `model_run_store.py` | 普通/Avatar 模型不可变 run 定位、缓存完整性与文档复验 |
| `model_worker_service.py` | 模型 Bundle 暂存、CABMap、对象快照与精确纹理 Worker 编排 |
| `model_source_identity.py` | 普通/Avatar 模型完整来源、依赖、builder 与工具缓存身份 |
| `model_build_session.py` | 模型单次 run 路径、请求身份、步骤、进度与元数据协调 |
| `ordinary_model_document_service.py` | 普通模型层级、geometry、纹理回绑、诊断与语义校验 |
| `ordinary_model_build_service.py` | 普通模型缓存、Worker、文档和发布的应用层协调 |
| `avatar_model_document_service.py` | Avatar 资源解释、纹理名称回绑与静态 ModelDocument 组装 |
| `avatar_model_build_service.py` | Avatar 计划、依赖、缓存、Worker、文档和发布的应用层协调 |
| `model_glb_service.py` | 已发布模型输入校验、材质计划与基础/动画 GLB 缓存派生 |
| `model_animation_service.py` | 动画请求身份、逐片导出/绑定、诊断清单与动画 GLB 协调 |
| `model_blend_service.py` | 基础/动画 GLB 选择、Blender 准备进度与 artifact 结果协调 |
| `model_preview_service.py` | 模型构建选择、公开预览 URL、结果文档与任务进度协调 |
| `model_artifact_resolver.py` | 不可变模型 run 的 geometry/texture 请求身份与安全文件定位 |
| `model_single_animation_service.py` | 单 AnimationClip/Dialog Morph 的模型准备、绑定与进度协调 |
| `index_freshness.py` | 启动时审计主索引中已消失的 VFS chunk 引用 |
| `blender_export.py` | 可取消 Blender 子进程、缓存命中与原子 `.blend` 发布 |
| `audio_export.py` | vgmstream WEM→WAV 转换与原子缓存发布 |
| `audio_package_service.py` | PCK 媒体索引、WEM 提取与 WAV 派生缓存服务 |
| `assetbundle_browser.py` | 已发布 AssetBundle 目录浏览与 AssetMap 元数据回绑 |
| `assetbundle_worker_service.py` | AssetMap、预览媒体、空 run 与 AssetBundle Worker 缓存编排 |
| `projectile_data.py` | projectileId 精确路径规则与 ProjectileComponentData JSON 选择 |
| `sparkbuffer.py` | TableCfg/SparkBuffer 解码 |
| `usm.py` | CRI USM 视频处理 |
| `usm_video_service.py` | USM 虚拟目录、转换工具身份与原子 MP4 缓存发布 |
| `public/` | 无构建步骤的浏览器前端 |
| `tools/` | 格式探测、索引提取和离线解码工具 |
| `schemas/` | MemoryPack 已知类型与 union 映射 |

仓库内的 `tools/index_endfield_vfs.py` 是当前 VFS 主索引生成器；它只解析 `.blc/.chk` 文件边界，
不会把 Unity、TableCfg、音频等二级格式混入索引阶段。
| `docs/design/` | 当前设计和演进方向 |
| `docs/research/` | 已验证的格式研究记录 |

## API

运行时诊断不会触发资源导出：

```text
GET /api/health
```

响应会列出 Unity worker 的协议、版本和能力，以及 Blender、ffmpeg 等可选工具和仍被
未迁移链路使用的旧工具。可选工具缺失不影响核心服务的 `ready` 状态。

Projectile 与模型预览提供可取消的长任务入口：

```text
POST   /api/tasks/projectile
POST   /api/tasks/model
POST   /api/tasks/model-blend
POST   /api/tasks/model-animation
GET    /api/task?taskId=<id>
DELETE /api/task?taskId=<id>
GET    /api/task-artifact?taskId=<id>
```

Projectile 创建请求体为 `{"projectileId":"projectile_..."}`；模型请求体包含
`manifestId`、`assetIndex`、`lod` 和可选的 `animationAssetIndex`。任务结果写入独占缓存目录，
成功后才由原子状态文件发布；模型任务会在状态中报告资源计划、CAB 映射、对象、纹理、缓存
发布和 GLB 派生阶段，前端收到成功结果时基础 GLB 已就绪。浏览器切换模型时会取消旧任务，
`DELETE` 会实际终止对应的独占 worker 进程，而不只是改变前端状态。原同步模型查询仍保留给
已有调用者兼容。

Blender 任务请求使用相同的模型身份及 `animationAssetIndexes` 数组。基础模型、当前动画和
批量动画三个网页入口都使用后台任务；动画导出、绑定和 Blender 阶段会持续更新任务状态，
离开模型预览会取消当前导出。成功任务通过 `task-artifact` 下载，状态 JSON 不暴露本机缓存
路径。

动画预览任务使用 `manifestId`、模型 `assetIndex`、`animationAssetIndex` 和 `lod`。切换动画
会取消旧任务，基础骨架、AnimationJSON 导出和轨道绑定分别报告进度；原同步动画 GET 继续
作为兼容入口，并复用同一构建函数。

任务注册表默认保留最近 512 个且七天内的终态任务。清理仅删除
`data/internal-cache/tasks/<taskId>` 中的状态和结果引用，不删除模型、动画或 Blender 派生缓存；
运行中任务、无法识别的目录和符号链接不会被清理。服务重启后遗留的非终态任务会先明确标为
`task_interrupted`，再按普通终态任务进入保留策略。

```text
GET /api/manifest
GET /api/list?scope=effective&path=&page=1&pageSize=100
GET /api/search?scope=effective&q=SkillConditionTable&limit=100
GET /api/projectile?projectileId=projectile_chr_0030_zhuangfy_attack_sword_1
GET /api/audio-dialog/list?language=chinese&path=&page=1&pageSize=100
GET /api/audio-dialog/entry?language=chinese&path=v1d0/story/example.wav
GET /api/audio-dialog/preview?language=chinese&path=v1d0/story/example.wav
GET /api/audio-dialog/raw?language=chinese&path=v1d0/story/example.wav&format=wav
GET /api/preview?id=123
GET /api/raw?id=123&download=1
GET /api/internal/list?id=123&path=assets&page=1&pageSize=100
GET /api/internal/preview?id=123&path=Texture2D/example.png
GET /api/internal/raw?id=123&path=Texture2D/example.png
GET /api/tablecfg/json?id=123
GET /api/akedb-compatible/TableCfg-1.4.4@9433094-12/CharacterTable.json
GET /api/akedb-compatible/SkillData/manifest.json
GET /api/akedb-compatible/SkillData/chr_0004_pelica_attack1.json
GET /api/akedb-compatible/BuffData/manifest.json
GET /api/akedb-compatible/BuffData/buff_chr_0004_example.json
GET /api/akedb-compatible/ProjectileData/manifest.json
GET /api/akedb-compatible/ProjectileData/projectile_chr_0004_example.json
GET /api/akedb-compatible/AbilityEntityData/manifest.json
GET /api/akedb-compatible/AbilityEntityData/abilityentity_chr_0004_example.json
GET /api/manifest-asset/preview?manifestId=123&assetIndex=456
GET /api/manifest-asset/raw?manifestId=123&assetIndex=456
GET /api/manifest-asset/model?manifestId=123&assetIndex=456
GET /api/manifest-asset/model-glb?manifestId=123&assetIndex=456
GET /api/manifest-asset/model-blend?manifestId=123&assetIndex=456
GET /api/manifest-asset/model-blend?manifestId=123&assetIndex=456&lod=0
GET /api/manifest-asset/model?manifestId=123&assetIndex=456&animationAssetIndex=789
GET /api/manifest-asset/model-animation?manifestId=123&assetIndex=456&animationAssetIndex=789
GET /api/manifest-asset/model-animations?manifestId=123&assetIndex=456&q=pelica
GET /api/manifest-asset/model-blend?manifestId=123&assetIndex=456&animationAssetIndex=789
```

`/api/akedb-compatible/` 是给 Endaxis 下载器使用的精确资源接口，不做模糊搜索。TableCfg 名称映射到
`Table/Data/TableCfg/<name>.bytes`，集合文件映射到
`JsonData/Data/Json/<collection>/<file>.json`；两者都只读取 Effective 逻辑文件。TableCfg 经
SparkBuffer 解码，SkillData/BuffData 经 MemoryPack schema 完整解码，存在未消费字节时返回 `422`，
不会输出不完整 JSON。集合 manifest 只枚举该集合的直接文件。

兼容边界是“Endaxis 可用同一个逻辑路径和 source schema 消费解码结果”，并非与 AKEDB 的 JSON 文本
逐字节相同；空白、字段顺序以及已知的新旧曲线表示可以不同。响应包含
`X-Endaxis-Source: vfs-index-browser`，供下载器记录逐文件来源。AKEDB 仍由 Endaxis 作为首选提供者，
此接口只在其资源尚未更新或不可用时补齐。

ProjectileData 和 AbilityEntityData 不是 AKEDB 当前已有的数据集，而是为同一批量下载协议提供的
VFS-only 集合。两者的 manifest 只枚举 canonical Unity asset 目录的直接子项。ProjectileData
通过精确 projectile asset path 导出 `ProjectileComponentData`；AbilityEntityData 通过精确
abilityentity asset path 的 Raw MonoBehaviour，只解析已由静态证据和样本共同确认的
`AbilityEntityTemplateData` 前缀。未知组件字段不会被猜测或静默解释为“无行为”。

Projectile API 不走模糊路径搜索。它把 `projectileId` 映射为
`assets/beyond/dynamicassets/gamedata/projectile/data_<projectileId>.asset`，要求 manifest
中恰好存在一个匹配，然后只读取所属 AB，以 JSON 模式导出其中的 MonoBehaviour，并按
`layout + projectileId` 选择唯一组件。
成功响应包含稳定来源身份 `source.asset`、聚焦结果 `projectileComponentData`，以及拥有该
组件的完整 `unityObject`。`decode.status` 为 `decoded`、`partial` 或 `unparsed`；当前解码器
会把尚未完全语义化但已经过字节边界校验的字段标为 `partial`。

错误状态固定为：无效 ID 返回 `400`，manifest 中不存在返回 `404`，Unity 对象无法形成
唯一组件结果返回 `422`，本地 manifest、AB chunk 或 Unity worker 不可用返回 `503`。
首次请求会构建 manifest SQLite 缓存并导出目标对象，后续请求复用带源文件和工具身份的缓存。
研究证据和当前解码边界见
[ProjectileComponentData 本地解析链](docs/research/projectile-component-data.md)。

AudioDialog API 默认读取 `data/audio-dialog-index.sqlite`。可通过
`VFS_BROWSER_AUDIO_DIALOG_DB` 指定其他位置；索引不存在时，普通 VFS 浏览不受影响，
AudioDialog API 会明确返回未构建状态。

manifest 逻辑树通过普通 `list` API 浏览；资源预览使用 `manifestId + assetIndex` 稳定定位。`.ab`、`.pck` 和 `.usm` 仍通过 internal API 浏览各自的按需内部视图。

模型接口可选的 `animationAssetIndex` 指向同一 manifest 内的 `AnimationClip`。
服务按需导出紧凑动画数据，并将 Unity 路径哈希绑定到基础模型的稳定节点 ID。
基础 GLB 不包含动画，切换动画时浏览器只获取独立动画 JSON，不会重复生成或下载模型。
独立动画数据的格式由 `schemas/model-animation.schema.json` 固定。
模型预览会从 manifest 搜索动画候选，并允许切换基础姿势、播放片段和导出当前动画。
带动画的 Blender 文件是由基础模型与所选片段按需派生的独立缓存，不会改写基础模型缓存。
批量导出会按所选动画集合保存带完整输入身份的准备结果；准备检查与随后下载共享同一次动画
导出、兼容性判定和 GLB 绑定结果，不会在下载阶段再次绑定全部片段。
直接预览链接使用：

```text
/?modelManifestId=123&modelAssetIndex=456&animationAssetIndex=789
```

浏览器会把当前视图、逻辑目录、文件分页和预览对象同步到地址栏。复制当前 URL
即可恢复同一浏览位置；浏览器前进和后退也会重新加载对应状态。常用参数包括：

- `scope`、`path`、`page`：当前数据视图、逻辑目录和分页。
- `audioLanguage`：AudioDialog 视图使用的语言。
- `fileId`：普通 VFS 文件。
- `previewUrl`：AudioDialog、Wwise 等虚拟文件的站内预览地址。
- `modelManifestId`、`modelAssetIndex`、`animationAssetIndex`：模型及当前动画。
- `avatarPlanManifestId`、`avatarPlanAssetIndex`、`lod`：AvatarMesh 资源计划。

`previewUrl` 只接受本站 `/api/` 路径，避免复制链接时引入外部预览目标。

## 研究工具

- `tools/parse_hgmmap.py`：离线验证 BundleManifest 结构。
- `tools/extract_indexed_file.py`：按 VFS 文件 ID 提取并解密文件。
- `tools/analyze_sparkbuffer_schema_ownership.py`：以 SparkBuffer type hash 为名义类型身份，从多个
  schema 根递归生成领域私有/共享类型归属；支持已解密文件和 VFS logical ID 两种输入。
- `tools/validate_schema_reference_edges.py`：校验人工取证的跨 schema 字符串 ID 边确实来自报告中的
  type hash、字段和 owner，并强制附带证据；不会按 `skillId`、`buffId` 等字段名猜目标类型。
- `tools/build_audio_dialog_index.py`：从 AudioDialog JSON 和现有 PCK 元数据构建逻辑语音 SQLite 索引。
- `tools/scan_jsondata_formats.py`：批量统计 JsonData 的真实编码格式。
- `tools/probe_binary_json.py`：对单个二进制 JSON 做结构探测。
- `tools/extract_memorypack_schema.py`：从 IL2CPP dump 提取 MemoryPack schema；可用 `--union-map`
  将已恢复派生类型显式纳入根集合。
- `tools/extract_memorypack_unions.py`：从运行时注册恢复 union；部分初始化快照可传入同版本
  `--metadata`，严格核对类型名、token 和索引。复现与边界见
  [诀资源解码记录](docs/research/memorypack-arcane-2026-08-26.md)。
- `tools/decode_memorypack_json.py`：使用已知 schema 解码二进制配置。
- `tools/blender_import_model.py`：在 Blender 4.3 中导入模型 GLB，根据
  `endfieldSourceMaterial` 与 `endfieldPreview` 自动建立 Eevee CharacterNPR
  预览材质、相机、灯光和可选轮廓。

例如直接分析 VFS 索引中的多个 TableCfg 根：

```powershell
python tools/analyze_sparkbuffer_schema_ownership.py `
  --vfs-db data/endfield-vfs-index.sqlite `
  --logical-root operator=Table/Data/TableCfg/CharacterTable.bytes `
  --logical-root operator=Table/Data/TableCfg/CharGrowthTable.bytes `
  --logical-root equipment=Table/Data/TableCfg/EquipTable.bytes `
  --logical-root weapon=Table/Data/TableCfg/WeaponBasicTable.bytes `
  --output schema-ownership.json
```

同一 owner 可以重复指定多个根。工具不会按名称或字段形状合并类型；同一 hash 若出现不同名称、
kind 或字段/枚举签名会直接失败。

字符串 ID 指向 MemoryPack/JsonData 等另一种 schema 时，先把目标和原生证据写入
`GameDataReferenceGraph` 版本 1，再运行：

```powershell
python tools/validate_schema_reference_edges.py schema-ownership.json reference-graph.json
```

Blender 脚本必须由 Blender 自带的 Python 执行：

```powershell
blender --background --factory-startup `
  --python tools/blender_import_model.py -- `
  model.glb model.blend --render preview.png
```

角色信息界面的 Cubemap 六面导出后，可先构建独立的光照输入：

```powershell
python tools/build_character_lighting.py `
  path/to/exported/Cubemap `
  path/to/character-lighting.json

blender --background --factory-startup `
  --python tools/blender_import_model.py -- `
  model.glb model.blend `
  --lighting path/to/character-lighting.json `
  --main-light-direction 0 -1 0 `
  --framing portrait `
  --render preview.png
```

`character-lighting.json` 保存原始 Profile 参数、六面相对路径和生成的等距柱状环境贴图路径。当前 AnimeStudio 通过 PNG 输出 BC6H Cubemap，因此这条链路属于 LDR 预览，不能保留原资源的 HDR 动态范围。

`--main-light-direction` 是独立的预览主光方向，不从 CharacterVolume 的环境光方向推导。`--framing` 支持 `full` 和 `portrait` 两种验证构图；使用 `--outline` 可启用近似的 Freestyle 轮廓。脚本会保留 GLB 导入的骨架、蒙皮、纹理和材质自定义属性，并为面部 SDF、身体 Skin、头发和丝袜选择独立路径；当前节点组是 Eevee 静态预览后端，不等同于完整 HGRP Shader。

格式结论和未完成事项以 `docs/research/` 中的文档为准，不应从临时终端输出推断。
当前角色、NPC、Shader、动画和音频工作的统一优先级见
[资源恢复整合路线](docs/design/integrated-roadmap.md)。
完整的文档、生产模块和研究工具入口见[文档与工具导航](docs/README.md)。

## 当前限制

- manifest 已能建立完整路径到 Bundle 的映射，但尚未对所有 Unity 类型提供预览。
- manifest 资源会自动衔接对应 AB；AnimeStudio 未支持的 Unity 类型会明确提示无法导出。
- MemoryPack 解码仍依赖从当前客户端 IL2CPP 数据提取的 schema，游戏升级后需要重新验证。
- 组合模型已完成首个角色样本的网页与 Blender 验证；Transform 动画已能按需解码为独立数据，并在浏览器中绑定到基础 GLB 播放。当前预览区分衣物 PBR 与面部/头发 CharacterNPR，保留衣物 Spec 通道并近似处理乘算覆盖阴影；完整 Shader、采样检查、Animator、浮点曲线、运行时面部姿态和 BlendShape 尚未恢复。
- 音频用途、任务台本等聚合视图尚未建立。
