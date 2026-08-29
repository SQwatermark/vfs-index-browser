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

## 关键约束

- 普通目录是否含 `.ab` 不再影响目录结构。
- 不以全量 AnimeStudio 扫描作为全局索引来源。
- manifest 缓存是可删除的派生产物，不是持久数据源。
- 启动健康审计只在证据充分时报告 `stale`；仅未发现已消失 chunk 时报告 `unverified`，不能
  在缺少 `.blc` 内容身份的情况下声称索引为 current。
- 陈旧或旧格式索引在监听 HTTP 前进入临时重建 run。JSONL、候选 SQLite、完整性检查和 BLC
  身份复核全部成功后才通过同卷 `os.replace` 切换活动数据库；失败不会修改旧库。manifest
  派生索引也在服务 ready 前预热，用户请求不承担更新后的首次完整读取。
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
