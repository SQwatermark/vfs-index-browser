# VFS 浏览器架构

## 目标

工具只依赖本地游戏文件，先稳定提供可查询的基础文件层，再在其上构建干员、武器、模型、任务和音频等聚合视图。

## 三层模型

### 1. VFS 物理读取层

SQLite 主索引保存逻辑文件、来源、物理 chunk、偏移、长度、加密标记和 IV。`server.py` 根据这些字段读取精确字节范围，并在必要时执行 ChaCha20 解密。

### 2. Manifest 逻辑目录层

`manifest.hgmmap` 是游戏提供的全局 BundleManifest。`manifest_index.py` 解压并解析：

- Bundle 名称及索引；
- AssetInfo 逻辑路径；
- AssetInfo 到 Bundle 的映射；
- 资源声明大小。

解析结果按 manifest 内容 SHA-256 缓存为派生 SQLite。前端在 `manifest.hgmmap` 同级显示虚拟目录，进入后只查询当前目录直属子目录和当前页文件，不加载整棵树。
首次完整读取后，缓存会原子记录 VFS 提供的逻辑文件 MD5/长度身份到已验证 SHA-256 SQLite
的别名。后续进程重启可在 schema 与 fingerprint 仍匹配时直接打开派生索引；别名损坏、身份
变化或缓存无效都会回退到完整读取。这只优化已选择来源，不等价于证明 VFS 主索引仍新鲜。

### 3. 内容解析层

manifest 负责回答“资源在哪里”，不负责解释 Unity 对象。用户选择资源后，服务根据 Bundle 名称定位 Effective `.ab`，调用仓库内 VFS Unity worker，并用 AssetMap 的 `Container` 精确匹配导出文件。常规资源类型无法导出时，`.asset` 和 `.prefab` 可按同一 container 精确回退到 MonoBehaviour TypeTree Dump；同一逻辑资源关联的多个组件会合并为一份可预览文本，而不会扫描整个 Bundle。Cubemap 也按 container 独立解析，但一个逻辑资源会产生六个带方向身份的面，HTTP 层以多产物预览返回，不能套用普通资源“一项对应一个文件”的假设。PCK、USM、TableCfg 和 MemoryPack 采用相同的按需解析原则。

`manifest_asset_service.py` 负责把 manifest 文件 ID 与 asset index 解析成当前可读的 manifest
来源、AssetInfo 和 AssetBundle。Persistent 记录失效时的同逻辑文件 fallback、来源排序、
资源不存在和 Bundle 缺失均在该服务内完成，并以带状态码的应用错误返回；它不写 HTTP 响应。
模型入口类型校验、批量动画资源的去重和稳定排序也属于这一边界，任务 Handler 只消费已经
解析完成的资源元组。

Prefab 模型恢复使用 VFS Unity worker 的版本化对象快照协议。内嵌 AnimeStudio 核心负责
Unity 对象身份、TypeTree 载荷和跨 Bundle PPtr 解析；服务负责从 manifest 构造依赖闭包、
调用 worker 并组装 ModelDocument。两者通过
`AnimeStudioObjectSnapshot/1.0.0` 契约连接，不读取 AnimeStudio 内部类型，也不从导出
文件名猜测对象引用。CAB 映射是请求内显式输入，worker 校验 CAB 名、稳定 input ID 和
SerializedFile 偏移后再导出，不依赖旧 CLI 的进程级 `Maps/` 状态。

模型对象和纹理由仓库内 `unity-worker/` 的版本化构建提供。纹理选择使用精确
`sourceFile + pathId`，不能按可能重复的资源名称猜测。Cubemap 由 worker 按精确 container
输出六个带方向身份的面。通用 Bundle 预览与模型播放所需动画均由 worker 的固定白名单协议
提供；没有任意类型 Convert 回退，也不再定位外部 AnimeStudio CLI。

Prefab 模型属于该层的聚合解析：服务查询 Bundle 传递依赖闭包，通过跨 Bundle PPtr 恢复 `ModelDocument`，再由独立导出器生成 GLB。ModelDocument 保留完整模型语义和原始材质参数，GLB 只承载 LOD0 通用预览所需的资源子集。

一次模型构建的输入、CABMap、对象、纹理、ModelDocument 和几何全部写入同一个不可变 run。
run 内完成标记写入后，缓存根目录的 `run.json` 才会原子切换；失败构建不会覆盖上一份结果。
文档中的几何和纹理 URL 携带 run 身份，因此发布新模型不会让已打开页面混读两代产物。

AvatarMesh 采用另一种入口适配：`npc_avatar_config.py` 解析 TypeTree 并从本地 VFS 的 effective `StringPathHash.bin` 恢复引用候选，`npc_avatar_resources.py` 再通过 manifest 唯一确定 Mesh、按槽位排序的 Material、Avatar 和 Bundle。HTTP API `/api/manifest-asset/avatar-plan` 暂时暴露这份中间计划以便真实样本验证；它不是第二种公开模型格式。对象提取完成后仍进入同一个 ModelDocument 和[角色材质恢复管线](material-pipeline.md)。

### 后台任务边界

`task_registry.py` 只负责状态文件、原子结果发布、当前进程取消句柄和保留策略；
`task_service.py` 将存储损坏或缺失统一映射成应用层的任务不存在，并给请求层提供状态、取消
响应和已登记产物。`server.py` 只解析 HTTP 参数、选择状态码并流式写出产物，不直接解释任务
目录或私有结果字段。模型、动画和 Blender 创建参数由 `task_requests.py` 转成不可变 DTO，
LOD、可选动画和批量上限不再在三个 Handler 中重复解析。`task_operations.py` 为每项后台
工作创建不带 socket、headers 或响应流的独立构建实例，并集中绑定任务种类、取消事件和进度
回调；Handler 不再捕获自身或手写后台 lambda。manifest 资源解析核心也已移入无 HTTP 依赖的
应用服务；模型、可选单动画和批量动画任务均直接调用该服务，不再拼装查询参数后绕经 HTTP
兼容方法。保留的同步模型、GLB、动画候选和 Blender 入口也通过统一适配方法调用同一模型
校验；批量动画同步入口使用服务的去重、排序和解析结果。`manifest_asset_requests.py` 集中解析
同步 query 中的 manifest/asset 身份与逗号或重复形式的动画集合，HTTP 适配方法不再各自解释
这些字符串。外部进程也不由 Handler 直接启动：Unity worker、Blender 模型导出、vgmstream
音频转换和 USM 转码分别通过独立适配器执行并负责超时、失败清理与原子产物发布。
`usm_video_service.py` 还拥有 USM 虚拟目录和 MP4 缓存身份：身份同时包含 VFS 文件摘要、
偏移/长度和转码工具文件信息，相同长度的游戏热更或工具升级不能误命中旧视频。
PCK 的 AKPK/BNK 结构只由 `audio_package.py` 解析；`audio_package_service.py` 负责按 VFS
内容身份缓存媒体索引、提供 WEM/WAV 虚拟目录、解密并原子发布 WEM，再通过 vgmstream 适配器
派生 WAV。Handler 不再维护第二套宽松二进制解析器或音频缓存路径规则。
已发布 AssetBundle run 的目录遍历、路径逃逸防护、文件类型分类以及导出文件到 AssetMap
`Type + Name + PathID` 的回绑由 `assetbundle_browser.py` 负责；HTTP 层只选择 run 和返回响应。
AssetBundle、Projectile、Cubemap、MonoBehaviour 与动画导出的通用 run 生命周期由
`worker_run_service.py` 统一管理：服务在发布前校验 worker 声明及派生文件的路径、大小和
SHA-256，以 `meta.json` 作为唯一发布指针，并在同一指针的发布临界区内完成缓存复验或构建；
不同缓存指针之间不互相阻塞。失败
构建只清理本次未发布目录，不改写上一份有效指针；Handler 只保留各资源能力特有的输入准备、
worker 调用和领域校验。
其中 manifest 资源的 Bundle 切片暂存、缓存身份和 Projectile、Cubemap、MonoBehaviour
领域产物约束进一步由 `manifest_worker_service.py` 管理；AnimationClip 也复用它的通用导出
入口，并由服务完成 AssetMap 唯一身份、PathID/名称与文档协议回验。Handler 上保留的领域
同名方法只是同步调用兼容层，不再暴露通用 Worker 导出方法或包含文件派生逻辑。
整 Bundle 的 AssetMap 与媒体预览输入身份、切片暂存、Worker 调用和 `AssetEntries` 契约由
`assetbundle_worker_service.py` 管理。服务要求每项已导出或明确跳过的媒体都与 AssetMap 的
`Type + PathID + Name + Container` 多重集合完全一致；仅含无预览协议类型的 Bundle 也发布
可验证空 run。Handler 只把服务异常映射为既有 HTTP `mapFailed`/`exportFailed` 响应。

普通模型和 Avatar 模型共享 `model_run_store.py` 的发布读取边界。活动 `run.json` 只指向包含
自身完成标记的直属不可变 run；缓存命中同时要求版本、完整 source identity、ModelDocument
语义、geometry 和声明过的 texture 目录一致。普通模型仅在文档声明 buffer 时要求 geometry，
Avatar 模型则始终要求 geometry。HTTP 按显式 run 读取旧资源也复用同一安全解析函数。
两类模型的 source identity 由 `model_source_identity.py` 构造，统一记录入口 VFS record、chunk
修改身份、asset、依赖闭包、缺失依赖、领域计划、builder 和 Worker 工具身份。identity 保持
有序依赖列表和既有 JSON 结构，纯模块迁移不会误使真实缓存失效。
每次未命中缓存的模型构建由 `model_build_session.py` 持有独占 request/run 身份、CAB/对象/纹理
目录、子 Worker request ID、已完成步骤和进度总数，并统一生成最终 run metadata。领域管线不再
分别拼接时间戳/UUID 或维护易分叉的步骤列表；活动指针仍只由 `ModelRunStore` 发布。
模型发布写侧也由该 store 统一：先写 ModelDocument、可选或必需 geometry 和 run 自身完成
标记，最后才通过同目录临时文件原子切换活动指针；指针替换失败会清理临时文件并保留旧指针。
普通模型和 Avatar 模型调用 Unity worker 的共享过程位于 `model_worker_service.py`。服务为每个
稳定 input ID 暂存独占 Bundle 文件，统一执行并校验 CABMap、对象快照和精确 Texture2D 产物；
具体模型管线只决定输入闭包、对象类型、container 与纹理 selection，并负责把结构化产物组装
为各自 ModelDocument。纹理输出必须位于新的空目录；服务遇到非空目录会失败并保留现场，
不会递归删除调用方路径或用新产物掩盖陈旧文件。
普通 manifest 模型的对象快照解释和领域组装由 `ordinary_model_document_service.py` 负责：
选择唯一 container 根节点、构造层级与 geometry、收集材质 Texture2D 身份，再按
`sourceFile + pathId` 回绑 Worker 图片。缺失纹理和依赖 Bundle 形成稳定诊断，最终文档必须
通过 ModelDocument 语义校验后才能交给 run store 发布。
`ordinary_model_build_service.py` 是普通模型应用层入口，按固定次序协调 source identity、缓存
读取、build session、Worker 服务、文档服务和 run store。HTTP Handler 的兼容方法只转发已解析
record、asset 与依赖闭包，不再持有模型构建步骤或产物 URL 规则。
Avatar 模型的已选 Mesh/Material/Avatar 解释、材质纹理 selection、导出图片名称回绑和静态
ModelDocument/geometry 构建由 `avatar_model_document_service.py` 负责；资源闭包和 container
仍由 Avatar 资源计划决定，重复 Texture2D 名称不会被后到产物静默覆盖。
`avatar_model_build_service.py` 在资源计划能力之上协调依赖闭包完整性、source identity、缓存、
build session、Worker、文档服务与 run store。Handler 只注入索引相关的计划、闭包和 Bundle 来源
解析方法，并保留原有 HTTP/任务调用签名；缺失依赖会在创建 run 和调用 Worker 之前失败。
已发布 ModelDocument 到基础 GLB 的输入复验与缓存派生由 `model_glb_service.py` 负责。服务只接受
与当前 record、asset、LOD 和不可变 run 完全一致的纹理 URI，拒绝目录逃逸，再以 geometry、纹理、
动画片段、绑定器、导出器和材质计划身份决定是否重建基础或动画 GLB。两类 GLB 与 metadata
均以唯一临时文件替换发布；动画与 Blender 上层仍通过 Handler 的兼容方法消费该稳定边界，
不再自行解释基础模型纹理路径或复制 GLB 发布规则。
多动画请求的稳定 identity、缓存清单、逐片 AnimationJSON 导出、绑定隔离、兼容性跳过和最终
动画 GLB 协调位于 `model_animation_service.py`。每个片段先在文档副本上绑定，只有产生兼容
轨道才提交到累计结果；失败片段在允许跳过时形成区分 `clipExport` 与 `modelBinding` 的诊断。
请求清单通过唯一临时文件原子切换，缓存恢复仍会复核有效动画集合对应的 GLB 是否存在。
Blender 派生分成两层：`model_blend_service.py` 选择基础或动画 GLB、处理全部动画不兼容的短路、
报告 Blender 阶段并构造私有 artifact 结果；`blender_export.py` 只拥有可取消外部进程、输入
mtime 缓存和 `.blend` 发布。HTTP Handler 注入两类 GLB provider 与 Blender provider，不再
拼装后台任务结果或直接构造导出适配器。
`model_preview_service.py` 是模型网页结果的应用入口：按普通/Avatar 类型选择构建服务，组装
GLB、Blender、动画候选和单动画 URL，裁剪公开 run 摘要，并将领域构建阶段映射为后台任务总
进度。manifest 索引与 Bundle 来源仍通过注入接口访问；Handler 保留原方法名供同步路由和任务
系统兼容，不再掌握模型结果文档的字段规则。
模型文档中的 buffer/texture URL 由 `model_artifact_resolver.py` 解析为不可变 run 内文件。请求
身份统一校验 record、asset、LOD 与显式 run；纹理路径先 URL 解码再限制于该 run 的 `textures/`
目录，路径逃逸和未完成 run 都不会返回文件。Handler 只保留 400/404 映射、内容类型与流式发送。
单片网页动画由 `model_single_animation_service.py` 协调。服务先通过普通或 Avatar 构建入口取得
模型，再按资源路径严格区分 AnimationClip 与 Dialog Morph：前者使用精确 Worker 导出并绑定，
后者调用 skeletal morph builder；取消检查位于模型、导出、绑定和发布结果边界，进度保持
`model → animation → binding → ready` 契约（Morph 无独立 binding 阶段）。
模型动画候选查询由 `model_animation_catalog_service.py` 协调。服务保留 ManifestIndex 的搜索和
分页语义，统一计算路径默认查询与显式查询，验证 Avatar LOD，并为普通/Avatar 候选分别组装带
缓存版本的单动画和 Blender URL。Handler 只负责解析模型来源并把查询或索引错误映射为 HTTP 400。

## 关键约束

- `runtime_config.py` 是环境配置唯一入口。所有持久数据库和派生缓存默认归属同一 data root；
  细粒度 override 只覆盖对应字段，不得恢复相邻仓库或固定用户目录探测。
- `tool_registry.py` 是可选外部工具能力的唯一判断入口。HTTP 展示、缓存身份和实际适配器执行
  必须使用同一解析结果；工具缺失只降级对应能力，不影响核心浏览服务启动。
- `cache_versions.py` 统一登记服务端自己生成的派生缓存版本，并通过健康检查公开诊断快照。
  游戏协议版本、外部数据契约版本和持久数据库 schema 仍归各自解析模块，不混入缓存版本。
- 监听地址、端口、日志级别和日志格式由 `runtime_config.py` 提供环境默认值，CLI 只做显式
  覆盖。`service_logging.py` 是服务日志唯一出口，默认输出带事件名和字段的单行 JSON。
- 普通目录是否含 `.ab` 不再影响目录结构。
- 不以全量 AnimeStudio 扫描作为全局索引来源。
- manifest 缓存是可删除的派生产物，不是持久数据源。
- 启动健康审计只在证据充分时报告 `stale`；仅未发现已消失 chunk 时报告 `unverified`，不能
  在缺少 `.blc` 内容身份的情况下声称索引为 current。
- 陈旧或旧格式索引在监听 HTTP 前进入临时重建 run。JSONL、候选 SQLite、完整性检查和 BLC
  身份复核全部成功后才通过同卷 `os.replace` 切换活动数据库；失败不会修改旧库。manifest
  派生索引也在服务 ready 前预热，用户请求不承担更新后的首次完整读取。
- 数据 API 与新任务创建以 `indexFreshness=current` 为统一门禁。索引陈旧、不可用或尚未验证
  时返回带 `code=index_stale` 的 503；健康检查和已有任务的观察、取消、产物下载不受影响。
- 新格式解析器遇到结构不一致时应明确报错，不静默忽略。
- HTTP 层不复制二进制格式知识；格式解析应位于独立模块。

## 后续演进

当前跨领域优先级、并行边界和验收标准统一记录在
[资源恢复整合路线](integrated-roadmap.md)中；音频逻辑身份与虚拟目录单独见
[音频语义索引与虚拟目录](audio-index.md)。

1. 稳定统一模型、装配计划和材质源数据契约。
2. 自动发现本地 AudioDialog、语言 PCK，并继续修正材质源数据。
3. 将 `server.py` 中 PCK、AB、USM 适配器逐步拆成独立模块。
4. 增加小型合成样本测试，避免测试依赖本机游戏文件。
