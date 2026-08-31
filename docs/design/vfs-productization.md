# VFS 产品化与 AnimeStudio 内嵌计划

本文是 VFS 产品化工作的主交接文档。它同时约束目标架构、迁移边界、阶段门禁和当前
进度。涉及产品化的代码改动必须同步更新本文的“实施状态”和“变更记录”；未写入本文、
自动化测试或其他稳定设计文档的口头约定，不视为已经进入架构。

## 目标

把当前依赖特定开发机环境的研究工具整理为可独立部署、接口明确、结构清晰的 Windows
产品，并将 VFS 实际使用的 AnimeStudio 能力以内嵌源码和内部 worker 的形式纳入同一仓库、
同一构建和同一发布过程。

最终产物必须满足：

- 从干净的 VFS checkout 可以构建完整产品，不依赖另一份 AnimeStudio 源码目录；
- 发布包包含运行所需的 Unity 读取能力，不要求用户另外安装 AnimeStudio；
- Python 服务只依赖 VFS 自有的版本化 worker 协议，不了解 AnimeStudio 内部类型；
- 外部原生工具和可选能力均在启动诊断中显式报告，不通过开发机路径碰运气；
- HTTP、索引、格式解析、任务编排和 Unity 读取各有清晰归属；
- 真实样本等价验证通过后，才允许删除旧调用路径或弃用独立 AnimeStudio 仓库。

## 非目标

- 不把 AnimeStudio GUI 整体搬进 VFS；VFS 只保留产品所需能力。
- 不在本轮重写已经验证可靠的 Unity 序列化、纹理、动画或 Shader 解码算法。
- 不让 Python 直接加载 .NET 程序集。进程边界用于隔离崩溃、原生依赖和长任务。
- 不以“一次能跑”为完成标准；可构建、可诊断、可测试、可发布缺一不可。
- 不在迁移过程中顺手改变游戏数据语义。解析行为变化必须有样本证据和独立测试。

## 当前证据基线

截至 2026-08-25：

- VFS 当前提交为 `9a549f1`，工作树已有大量用户进行中的改动；产品化不得清理、覆盖或
  回退这些改动。
- 台式机 AnimeStudio 当前提交为 `8cdec963c4e187ea0a4a339b8969844a9574638b`，分支包含
  终末地 ACL、对象快照、Shader 二进制包与资源绑定等定制能力。
- VFS 内 `data/research/AnimeStudio` 是被 `.gitignore` 排除的研究副本，提交为 `03336c4`，
  且带有未提交修改；它不是可复现的生产依赖，也不是本次导入的权威来源。
- `server.py` 目前直接持有 AnimeStudio 可执行文件路径，并分散调用 AssetMap、CABMap、
  ObjectJSON、纹理、MonoBehaviour、Cubemap、模型、动画和通用 AssetBundle 导出。
- 当前启动说明暴露 `D:\Projects\AnimeStudio` 等开发机路径；这类路径只可作为迁移期的
  显式调试覆盖，不能出现在最终默认行为中。
- Blender、vgmstream、usm-convert、ffmpeg 等也是外部能力。它们可以保持可选，但必须被
  统一的能力检查、错误模型和文档覆盖。

## 目标架构

```text
浏览器 / API 客户端
        |
        v
HTTP 请求层
        |
        v
应用服务与后台任务  ----->  缓存、进度、取消、结果清单
        |
        +-----> VFS / manifest / TableCfg / PCK 等独立格式模块
        |
        +-----> UnityWorkerClient（唯一进程适配器）
                       |
                       v
                Vfs.UnityWorker
                       |
             +---------+----------+
             |                    |
      AnimeStudio.Core     Endfield.Extensions
      Unity/AB 通用读取     ACL/Shader/专用组件
```

建议仓库布局：

```text
vfs-index-browser/
  server/                    Python HTTP、应用服务和基础设施
  unity-worker/
    src/
      AnimeStudio.Core/      内嵌并逐步裁剪的通用 Unity 读取实现
      Endfield.Extensions/   终末地专用扩展
      Vfs.UnityWorker/       VFS 自有命令入口和协议实现
    tests/
    THIRD_PARTY_NOTICES.md
    README.md
  public/
  tests/
  docs/
```

目录名表达最终边界，不要求首次导入时立即完成物理裁剪。首次导入可以保留较完整的上游
源码以降低行为变化风险，但 VFS 自有入口、协议和构建必须从第一天独立存在；后续只能沿
目标边界收敛，不能继续让 Python 直接拼 AnimeStudio 命令行。

## 强制架构规则

### 1. 单一 Unity worker 边界

Python 侧只允许一个 `UnityWorkerClient`（名称可调整）知道 worker 的位置、启动方式、超时
和协议。HTTP handler、模型模块、动画模块及资源解析器不得自行执行 worker 或寻找 EXE。

### 2. VFS 拥有协议

协议属于 VFS，不以 AnimeStudio CLI 参数作为公共接口。协议至少包含：

- `handshake`：协议版本、构建版本、提交来源和能力集合；
- 请求 ID、操作名、输入文件与输出目录；
- 结构化成功结果、产物清单、诊断、进度和错误；
- 可区分的输入错误、数据不兼容、能力缺失、超时、取消和内部崩溃；
- 向后兼容规则和显式协议版本。

初期可用单次进程 JSON 请求，稳定后再按性能证据选择 JSON Lines 常驻 worker 或本地管道。
不得在没有测量的情况下把生命周期优化与语义迁移绑在一起。

首版单次请求通过 `Vfs.UnityWorker request <request.json>` 调用：

```json
{
  "protocolVersion": "1.0.0",
  "requestId": "调用方生成的稳定 ID",
  "operation": "exportMonoBehaviourRaw",
  "arguments": {
    "inputPath": "绝对或调用目录下的 Bundle 路径",
    "outputDirectory": "本次请求独占的新输出目录",
    "container": "可选的精确 Unity container"
  }
}
```

worker 只能写入本次请求的新目录，不覆盖旧结果；成功响应返回每个产物的相对路径、
source file、PathID、container、长度和 SHA-256。`container` 使用精确匹配，不在 worker
中猜逻辑资源名。第一项能力只导出 Raw 对象字节，不夹带 TypeTree、程序集恢复或 Python
专用 Projectile/AbilityEntity 解码。

### 3. 源码与许可证可追溯

- 导入必须记录上游仓库、精确提交和导入日期；
- AnimeStudio 的 MIT 许可证及所有随源码进入的第三方许可证必须保留；
- 原生 DLL 必须能追溯来源、架构和用途，不得只提交一个来历不明的二进制目录；
- 上游同步应通过可重复脚本或明确流程完成，不能手工覆盖后再凭记忆挑文件。
- VFS 或定制 AnimeStudio 源码随仓库保存；未修改且明确开源的第三方依赖可以在项目初始化
  时按锁定提交/版本下载源码或二进制，不要求全部 vendor。下载项必须有校验和、许可证和
  本地缓存，服务正常运行时不得隐式联网；发布包仍须自包含运行时依赖。

### 4. 能力而非机器路径

默认运行时只查找发布包或仓库构建输出中的 worker。开发期覆盖必须使用一个明确变量，
并在启动诊断中标为 override。禁止扫描 `data/research`、相邻仓库或固定盘符寻找最新版。

### 5. 迁移保持结果等价

每类能力迁移前固定代表性输入、旧结果和诊断；迁移后比较结构化输出，而不是只比较进程
退出码。未知 Unity 类型、损坏 Bundle、缺少依赖和版本不兼容必须继续显式失败。

### 6. 文档是阶段门禁

每个阶段开始前记录预期边界和验收项，完成后记录证据、测试命令和遗留问题。代码与本文
不一致时，该阶段不得标为完成。

## 迁移顺序

### P0：文档与依赖盘点

- [x] 建立产品化主文档和持续交接规则。
- [x] 建立 Python 调用点、CLI 操作、原生库、NuGet 包和可选工具的初始清单。
- [x] 固定 AnimeStudio 权威提交、许可证清单和源码导入方法。
- [x] 定义干净机器、构建、协议和结果等价的验收矩阵。

完成门禁：任何接手者只阅读本文和文档索引，就能解释为何内嵌、内嵌什么、如何迁移、
如何判定成功，以及当前下一步是什么。

### P1：源码导入与 worker 骨架

- [x] 从台式机权威提交导入所需源码和许可证，不使用现有研究副本充当来源。
- [x] 建立只包含生产所需项目的 solution/project。
- [x] 实现 `handshake`，输出协议版本、构建身份、运行平台和能力集合。
- [x] 添加 worker 契约测试及一条仓库内构建命令。

完成门禁：在 VFS 仓库内无需 AnimeStudio 外部目录即可构建 worker，并通过握手测试。

### P2：Python 调用收口

- [x] 实现唯一 worker 定位、调用、超时和错误翻译适配器。
- [x] 实现 worker 进程级取消，并以 Projectile 建立首条 HTTP 任务创建/查询/取消链路。
- [x] 让外部命令通过独立适配器执行，消除 `server.py` 中的直接 subprocess 调用。
- [x] 删除源码目录自动探测；worker 只使用仓库产物或显式开发 override。
- [x] 提供 `/api/health`，显示 worker 协议/能力、可选工具及尚未迁移的旧工具依赖。

完成门禁：除适配器测试外，Python 生产代码中不存在 AnimeStudio 路径或直接调用。

### P3：能力分批迁移

按以下顺序迁移，每批独立验证和可回退：

- [x] 原始/TypeTree MonoBehaviour、Projectile、AbilityEntity；
- [x] AssetMap、CABMap、ObjectJSON 和通用 AssetBundle 导出；
- [x] Texture2D、Sprite、Cubemap、模型层级和 AvatarMesh；AudioClip 无生产消费者，保留显式不支持诊断；
- [x] AnimationClip、Humanoid、ACL；
- [x] Shader 二进制包、程序映射和反汇编输入当前只服务离线研究，没有 HTTP/应用服务消费者，
  因而不引入无调用者的 worker 协议；研究线继续消费已验证的无损包读取器。

完成门禁：旧入口已无生产调用者，代表性真实样本和合成错误样本全部通过等价测试。

### P4：服务端产品化

- [x] 将单体 `server.py` 拆成请求层、应用服务、任务系统、缓存和基础设施适配器。
- [x] 长任务具备 ID、进度、取消、原子结果发布和结构化失败。
- [x] 配置、缓存版本、日志、端口和数据根目录有统一入口。
- [x] 启动时校验 VFS 主索引与当前游戏安装的一致性；索引过期时自动重建并原子切换，
  不能把陈旧索引导致的漏项返回为“资源不存在”。
- [x] 可选外部工具通过能力注册表接入，不散落路径判断。

完成门禁：handler 不包含二进制格式细节或 subprocess 编排，长任务不会发布半成品状态。

### 长任务与取消边界

worker 采用“一次请求一个子进程”。取消不能只是修改 UI 状态：Python 适配器必须终止该
请求的独占进程、等待进程退出，并返回稳定的 `worker_cancelled`。HTTP 层通过持久化任务
记录控制取消事件；内存中只保存小型控制句柄，任务结果写入独占目录，完成后由原子状态
文件指向结果，禁止把大结果只保存在全局变量中。

长任务入口使用以下协议，保留原同步查询供现有调用者兼容：

- `POST /api/tasks/projectile`：校验 `projectileId` 后创建任务；
- `POST /api/tasks/model`：按 manifest 资源身份创建普通模型或 AvatarMesh 构建任务，并报告
  资源计划、CAB 映射、对象、纹理与发布进度；
- `POST /api/tasks/model-blend`：创建基础模型、单动画或批量动画 Blender 派生任务；
- `POST /api/tasks/model-animation`：创建单片 AnimationJSON 导出与模型绑定任务；
- `GET /api/task?taskId=...`：读取原子状态，成功时可包含已发布结果；
- `DELETE /api/task?taskId=...`：设置取消事件；只有实际运行中的本机任务可进入
  `cancelling`，终止完成后转为 `cancelled`。
- `GET /api/task-artifact?taskId=...`：只为成功任务返回已登记产物；公开状态不包含本机路径。

任务状态至少区分 `pending`、`running`、`cancelling`、`succeeded`、`failed`、
`cancelled`。取消或失败不得写入成功结果指针，服务重启后遗留的非终态任务必须明确标为
中断，不能永远伪装为仍在运行。

### 后续优化：VFS 主索引新鲜度与启动重建

这项 P4 产品化任务的启动检测、仓库内生成、候选验证、原子切换和陈旧查询门禁均已实现。
2026-08-25 的梨子诺资源排查已经证明，旧
SQLite 仍指向已被游戏更新替换的 Persistent `.chk`；服务能够启动且旧记录仍标记
`chunk_exists=1`，但实际资源闭包已经过期。调用方因此可能得到错误的“资源不存在”，而不
是可诊断的“索引陈旧”。启动流程后续必须满足：

- 索引元数据保存生成时的 StreamingAssets/Persistent 根目录，以及所有 `.blc` 的稳定身份；
  身份至少包含规范化相对路径、文件长度和内容摘要，不能只依赖容易失真的数据库记录或
  修改时间；
- 服务进入 `ready` 前先比较当前安装与索引身份。完全一致时复用现有 SQLite；不一致时在
  独占的新 run 目录生成 JSONL 和 SQLite，并校验源根目录、解析错误、记录数量与所引用
  chunk 的存在性；
- 只有新索引完整通过校验后，才通过原子替换切换活动索引。不得原地覆盖旧数据库，也不得
  在新索引完成前删除最后一份可用索引；
- 自动重建失败时保留最后一份可用索引供诊断，但健康状态必须明确报告 `degraded` 和
  `indexFreshness: stale`。依赖资源身份的查询应返回可区分的索引陈旧错误，不得以 404 或
  空 manifest 冒充权威的“不存在”；
- 当前使用的外部索引脚本仍位于开发机临时目录。正式实现前必须将生成逻辑和版本身份纳入
  VFS 仓库、统一启动配置与测试，不能把固定盘符上的脚本变成新的隐式生产依赖。

验收至少覆盖：游戏未更新时无重建、只更新 Persistent 热更块时会重建、构建中断后旧索引
仍可恢复、无效新索引不会发布，以及陈旧状态不会被资源查询误报为缺失。

### P5：发布与弃用

- [ ] 构建 Windows 自包含发布包并在干净环境验证。
- [x] 完成安装、升级、故障诊断、开发和发布文档。
- [ ] 验证没有固定盘符、相邻源码仓库或用户目录依赖。
- [ ] 归档独立 AnimeStudio 仓库，并在 VFS 中记录最后同步点。

完成门禁：新机器仅凭发布包和游戏数据即可使用已声明能力。

## 验收矩阵

| 维度 | 必须验证 |
| --- | --- |
| 构建 | 干净 checkout、锁定 SDK/NuGet、x64 Release、可重复命令 |
| 协议 | 版本握手、能力发现、未知操作、超时、取消、worker 崩溃 |
| 数据 | 正常 Bundle、跨 Bundle PPtr、未知类型、损坏输入、缺失依赖 |
| 等价 | 对象身份、container、PathID、结构化载荷、文件哈希或容许差异 |
| 部署 | 无 AnimeStudio 外部目录、无固定盘符、原生 DLL 随包且架构正确 |
| 运维 | 健康检查、结构化日志、缓存身份、错误可定位、长任务无半成品发布 |

## 实施状态

当前阶段：**P5 发布准备**。P0 至 P4 均已完成。后续若出现 AudioClip 或在线 Shader 导出的
真实消费者与样本，按新能力增量设计协议，不重新打开外部 AnimeStudio CLI 回退。

首个 Windows x64 发布候选已在本机从隔离 Python 环境生成。Python 服务由 PyInstaller
6.22.2 以 onedir 形式冻结，Unity worker 使用锁定的 .NET SDK 9.0.200 发布为 win-x64
自包含运行时；包内包含 ACL 原生库、前端、schema、Blender 辅助入口和第三方许可证。
冻结服务以 EXE 所在目录为应用根，不依赖 PyInstaller 的临时源码位置。发布候选已通过
606 项 Python 测试、37 项 .NET 测试（6 项外部真实样本按设计跳过）、EXE `--help`、worker
握手，以及连接现有外部 data root 后的完整启动验证：主页 HTTP 200，健康状态 `ready`，
主索引 `current`、Manifest `ready`、worker `ready`、缺失 worker 能力 0、旧工具 0。
包内文件审计确认未混入未跟踪的 `public/` 研究样本，也未发现外部 AnimeStudio 绝对路径。
P5 的剩余门禁是从干净 checkout/另一台机器复跑、完成机器路径依赖审计，并在用户确认后
归档独立 AnimeStudio 仓库；当前本机成功不能替代新机器验收。

正在进行：在已经可构建的通用核心上整理第一批 MonoBehaviour 所需扩展。VFS 自有
`Vfs.UnityWorker` 已声明并验证 `handshake`、`exportMonoBehaviourRaw`、
`exportMonoBehaviourTypeTreeDump`、`decodeProjectileComponent`、`buildAssetMap`、
`buildCabMap`、`exportObjectSnapshots`、`exportIdentifiedTextures`、`exportCubemapFaces`、
`exportBundlePreviewMedia` 和 `exportAnimationClipJson`，worker 版本已升至 `0.14.0`。Projectile 已从巨型 CLI 中拆出可由三份真实样本证明的前缀、MoveMode 字典和
主特效结束条件；未知尾部完整保留为 Raw words，公开结果明确为 `partial`。下一项代码工作
已用 Python 唯一 `UnityWorkerClient` 把 Projectile 和共用 MonoBehaviour Raw 调用切换到
新 worker。Projectile、Raw 和 TypeTree Dump 复用同一个多产物原子导出框架：每次构建
写入独占 run 目录，所有产物的路径、大小与 SHA-256 全部校验完成，且同一 run 内的派生
预览已经生成后，才通过原子替换元数据指针一次性发布。AbilityEntity、骨骼形变配置和
AvatarMesh TypeTree 因此不再通过旧 CLI 获取这些输入。Projectile 已具备落盘任务状态、
HTTP 创建/查询/取消入口及进程级终止，结果不保存在全局变量中。单 Bundle AssetMap 也已
迁移到相同原子 run 框架，输出中的 `Source` 被规范化为稳定逻辑标识，不携带临时路径。
跨 Bundle CABMap 已有无机器路径的 VFS JSON 契约，新的对象快照操作会在同一请求中显式
绑定物理输入、CABMap、主输入、选择 input ID、类型和 container。模型与 AvatarMesh 生产路径
的对象和纹理均已切换并完成真实下游验证；纹理只接受精确 `sourceFile + pathId`，不再使用
旧 `BuildCABMap + UseCABMap` 或名称正则。不能把权威提交中只包含
对象外壳的通用 `JSON` 冒充领域解码能力，也不能复制旧 CLI 的全局 `Maps/` 隐式状态。

### 当前调用与依赖清单

Python 当前直接使用的 AnimeStudio 操作如下：

| 能力组 | CLI 操作/类型 | 当前调用位置 | 迁移批次 |
| --- | --- | --- | --- |
| 组件原始数据 | MonoBehaviour + `Raw`/`Dump`/`JSON` | `server.py` | P3.1 |
| 资源映射 | `AssetMap`、显式多输入 CABMap 已迁移；模型链不再使用旧 `UseCABMap` | worker、`server.py` | P3.2 |
| 对象快照 | GameObject、Transform、Renderer、Mesh、Material、Animator、Avatar 已迁移；LODGroup 从内嵌 TypeTree 严格恢复 | worker + `server.py` | P3.2 |
| 通用资源 | Texture2D、Sprite、TextAsset、VideoClip、AnimationClip YAML 已迁入 worker 与独占 run；AudioClip 暂无样本 | worker、`server.py` | P3.2/P3.3 |
| 纹理身份 | 精确 Texture2D `sourceFile + pathId` 已迁移 | worker、`server.py`、`avatar_mesh_snapshot.py` | P3.3 |
| Cubemap | 精确 container + 六面 PNG 已迁移 | worker、`server.py` | P3.3 |
| 动画 | 精确 PathID + 名称的 AnimationClip `AnimationJSON` 已迁入 worker 与独占 run | worker、`server.py` | P3.4 |
| Shader | Shader 二进制包及终末地扩展 | AnimeStudio 定制源码、离线工具 | P3.5 |

外部可选工具为 Blender、vgmstream、usm-convert 和 ffmpeg。它们不属于 Unity worker，
环境配置已由 `runtime_config.py` 集中读取；Blender 依次使用显式 override、PATH 和标准 Windows
安装目录，其他工具可由环境变量覆盖。下一步把可用性与能力判断也收进统一注册表。

权威 AnimeStudio 工程依赖初步分为：

- 通用托管核心：`AnimeStudio`；
- 导出与终末地扩展：当前混在 `AnimeStudio.CLI`、`AnimeStudio.Utility` 中，导入后需按
  VFS 协议入口与领域扩展拆开；
- 托管桥：`AnimeStudio.PInvoke`、`AnimeStudio.FBXWrapper`；
- 原生实现：`AnimeStudio.ACLNative`、`AnimeStudio.FBXNative`、`AnimeStudio.Oodle`；
- 明确不进入产品：GUI、Patcher；
- 待证明确有需要后再进入：FBX、fmod、HLSLDecompiler、BinaryDecompiler 及旧 ACL 变体。

当前上游 NuGet 包包括 Newtonsoft.Json、System.CommandLine、
System.Configuration.ConfigurationManager、K4os.Hash.xxHash、Kyaru.Texture2DDecoder、
MessagePack、ZstdSharp.Port、SixLabors.ImageSharp.Drawing、Mono.Cecil 和
Vortice.D3DCompiler。新 worker 骨架只使用 .NET 自带 `System.Text.Json`；后续按真实源码闭包
逐项引入，不能照抄旧 CLI 的全部依赖。

当前已知风险：

- 研究副本与权威 AnimeStudio 相差多个提交，且研究副本有本地修改，不能直接复用；
- 当前 CLI 工程会携带 FBX、ACL、Oodle 等多组原生库，需要区分必需、可选与未使用；
- AnimeStudio 定制分支包含大量第三方 ACL/RTM 代码，许可证和构建平台必须单独核对；
- `server.py` 调用点分散，先收口适配器再拆 HTTP 层，避免同时改变协议和业务语义；
- 当前 VFS 工作树不干净，产品化改动必须使用独立目录和小范围补丁，避免覆盖既有工作。
- 上游核心锁定的 MessagePack 3.1.4 存在多项已知漏洞；VFS 构建已先提升至 3.1.8，仍需
  通过 AssetMap MessagePack 读写回归确认兼容。

## 当前交接断点

截至 2026-08-31，当前工作树的可交付边界为：

- `tools/Publish-Windows.ps1` 已形成不覆盖目标目录的发布入口。它只复制 Git 跟踪的前端、
  schema 与许可证，发布自包含 worker，验证服务帮助和 worker 握手，并写出含提交与运行时
  版本的 `release.json`；成功后只清理经过 `.tmp` 根目录校验的本次 GUID 构建目录；
- `docs/deployment/windows-release.md` 已覆盖隔离构建环境、安装、外部 data root、健康检查、
  路径覆盖、并列目录升级与回退。正式包不要求 Python、.NET 或 AnimeStudio；

- worker `0.14.0` 已实现并声明 `handshake`、MonoBehaviour Raw、TypeTree Dump、Projectile
  聚焦解码、单 Bundle AssetMap、多输入 CABMap、对象快照、精确纹理、Cubemap 六面和固定
  白名单的 Bundle 预览媒体导出，以及精确 AnimationClip 的 AnimationJSON；
- Python 唯一 `UnityWorkerClient` 已封装 `buildCabMap`、`exportObjectSnapshots` 与
  `exportIdentifiedTextures`，模型和 AvatarMesh 生产路径均已接入；
- 浏览器模型预览已改走持久化后台任务，切换资源会取消旧任务；取消事件贯穿 Avatar 计划、
  CABMap、对象与纹理 worker，任务状态保存当前阶段，并在发布成功结果前完成基础 GLB 派生。
  兼容的同步模型 GET 复用同一结果构建函数；批量动画绑定与 Blender 派生下载仍是后续可
  任务化的同步路径；
- 批量动画准备结果按请求集合写入独立清单，身份包含模型、动画资源、worker/绑定和 GLB
  版本。准备检查后的下载直接复用有效动画集合、结构化跳过原因和动画 GLB，不再重复导出与
  绑定整批片段；
- 基础模型、当前动画和批量动画的 Blender 导出均已接入后台任务。动画逐项处理、缓存命中与
  Blender 阶段会报告进度；取消会贯穿动画 worker，并终止仍在运行的 Blender 子进程。任务
  artifact 接口从私有结果元数据提供下载，不向浏览器暴露绝对路径；
- 单片动画网页预览也已接入后台任务，基础骨架、AnimationJSON worker 导出和模型绑定分阶段
  报告状态；切换动画、恢复基础姿势或离开预览都会取消旧任务。兼容同步 GET 复用相同领域
  构建函数；
- 任务注册表在创建新任务时清理超过七天或最近 512 项之外的终态记录。清理范围严格限制在
  `internal-cache/tasks/<taskId>`，不会删除领域缓存、未知目录、符号链接或本进程活动任务；上次
  进程遗留的非终态记录先转为 `task_interrupted`，不再永久占用任务目录；
- 真实浏览器审计已使用当前 manifest 的权威虚拟目录索引完成：庄方宜 PostModel
  `263486` 构建为 565 节点、44 网格、52 蒙皮，单片攻击动画可播放；120 ms 内连续切换
  两个动画时前一任务进入 `cancelled`，后一任务成功发布。两片攻击动画的 Blender 任务
  以 2/2 成功并登记可下载产物，公开任务 JSON 不含 `_artifactPath`。秦桔臣 AvatarMesh
  `188410` 构建为 321 节点、9 网格、9 蒙皮，通用待机动画绑定并播放成功；
- `server.py` 的 AssetMap 已迁移到独占 run、完整产物校验和原子指针发布。模型与 AvatarMesh
  对象快照、引用纹理和 Cubemap 不再调用旧 `ObjectJSON`/`IdentifiedTexture`/`Convert`；
  通用预览中的 Texture2D、Sprite、TextAsset、VideoClip、AnimationClip YAML 已接入新媒体
  协议；AudioClip 暂无样本，只保留 AssetMap 身份并报告为无预览协议，不再走旧缓存或 CLI；
  模型动画入口则已通过 AssetMap 锁定唯一 `PathID + Name`，并使用独占 run、哈希校验和
  原子指针发布 `AnimeStudioAnimationClip/1.1.0`；
- CABMap JSON 是可审计的稳定中间产物，不含 baseFolder 或物理路径。worker 对象导出和两条
  服务端模型路径均已直接消费该契约；这两条路径已删除旧 `BuildCABMap + UseCABMap`，并将
  输入、CABMap、对象、纹理、模型文档和几何收口到独占 run，由根 `run.json` 原子发布；
- 对象 exporter 只迁移权威 `ObjectSnapshotExporter` 所需最小契约，没有复制巨型
  `Exporter.cs`；公共快照已移除 `sourceOriginalPath`、`loadedSourceOriginalPath` 和
  `targetSourceOriginalPath`，以稳定 input ID 补充来源绑定；
- 旧 ObjectJSON 会把 `sourceOriginalPath`、`loadedSourceOriginalPath` 等物理路径写入快照。
  新协议必须用稳定 input ID 替换这些字段，不能把开发机路径带回产品契约；
- 对象身份仍必须保持 `sourceFile + pathId`，container 必须来自 AssetBundle/ResourceManager
  的 PPtr 映射，跨 Bundle 引用必须依据显式输入闭包解析，禁止退回按文件名猜对象。

下一位接手者应按以下顺序继续：

1. 在干净 checkout 或另一台未安装 Python/.NET/AnimeStudio 的 Windows x64 机器，用发布包与
   游戏数据复跑主页、健康检查和至少一条真实 Unity 导出链；
2. 对 Git 跟踪的发布配置和实际发布目录做固定盘符、相邻仓库与用户目录扫描，并把检查固化
   到自动化门禁；开发工具中的示例证据路径不应误判为生产依赖；
3. 验收通过后由用户决定何时归档独立 AnimeStudio 仓库，并在本文记录最终同步提交；
4. P5 关闭后再回到消费者驱动的领域工作。AudioClip 或在线 Shader 没有真实消费者时不设计
   协议，LODGroup 没有完整 TypeTree 时继续明确失败。

2026-08-29 本机使用庄方宜 PostModel 的 71 Bundle 闭包完成新旧对象快照审计：忽略旧流程中
没有专用 CLR 解析器的 LODGroup 后，旧 1252 个 `sourceFile + pathId` 身份全部存在于新结果，
共同对象 container 0 差异；新结果额外成功导出 1 个 Animator。20 个 Material 的 Shader 名
由旧结果空字符串变为已解析身份，不作为字节等价失败。全部新 JSON 均未包含输入物理路径。
该证据已固化为可选 `VFS_WORKER_OBJECT_SNAPSHOT_FIXTURE_ROOT` 本机回归。

同日以一个 56 Bundle AvatarMesh LOD0 闭包复核：精确选择 23 个 container 后得到 13 Mesh、
13 Material 输入，派生 ModelDocument 的 437 节点、13 Mesh、11 Material、13 Skin 均与旧结果
一致；`geometry.bin` 为 3,381,104 字节且 SHA-256 逐字节一致。新 worker 导出的 AvatarMesh
38 张 PNG 与旧产物逐字节一致。模型生产路径在旧 CLI 故意不存在时完整执行，验证了 565
节点、44 Mesh、17 Material、52 Skin、9,971,028 字节 geometry 和 39 张纹理；几何及全部
PNG 与旧产物逐字节一致，并成功发布 run 指针。

2026-08-31 已恢复完整验证门禁：Python discovery `554/554` 通过；.NET 解决方案
`37` 项通过，`6` 项缺少本机外部样本的证据测试按设计跳过，0 失败。SDK 固定为 .NET 9；
AnimeStudio.PInvoke 通过 VFS 自有包装工程编译上游唯一源码，不再让上游 net9/net10 多目标工程
进入产品 restore 图。检查还发现 `64176df` checkpoint 曾把 AnimeStudioAnimation 1.1 fixture、
Humanoid oracle 完整输入、runtime probe 调试权限，以及两项已有证据的 MemoryPack 类型覆盖退回
旧状态；现已按该提交之前的实现和研究文档恢复。后续合并 checkpoint 时必须运行两套全量门禁，
不能把“研究保存提交”默认视为只增加文件。锁定导入的上游 vendor 保留其原始尾随空白，不以
格式化改写破坏来源比对；真实 fixture 位于被忽略的研究目录，不进入提交。

## 变更记录

### 2026-08-31

- 新增 Windows x64 自包含发布链：PyInstaller 6.22.2 冻结服务，.NET SDK 9.0.200 发布
  self-contained worker，发布包只纳入 Git 跟踪的运行资产、包内许可证和 Blender 辅助入口。
  冻结根目录、非覆盖输出、原生 ACL、worker 握手、版本清单和安全临时目录清理均已固化。
  本机完整门禁为 Python `606/606`、.NET `37` 通过且 6 项外部证据跳过；发布服务连接现有
  data root 后主页 200、健康状态全绿。安装、升级、诊断、开发和发布说明见
  `docs/deployment/windows-release.md`；另一台干净机器验收仍未完成。
- 同步模型、Avatar plan、GLB、Blend 与单动画路由的 LOD、下载和预检查参数已统一到
  `model_sync_request.py`。解析严格保持既有布尔集合和 ValueError 分类；Blend 下载 URL 去除
  `prepare` 时保留重复动画参数。27 项模型路由回归及 Python discovery `590/590` 通过。
- 单 Manifest 资源、模型、可选动画和批量动画的 query 解析与领域调用已统一到
  `manifest_request_resolver_service.py`。请求语法错误稳定为 400，资源解析状态原样保留；Handler
  四个兼容入口只映射错误。无动画参数继续在服务构造前返回空列表，保持基础模型路径零额外
  数据库访问。Python discovery 更新为 `586/586`，`server.py` 降至 2514 行。
- 普通 VFS preview/raw、TableCfg JSON 与内部目录现统一消费严格 file-ID 来源结果。TableCfg 新入口
  不要求 Handler 提供 SQLite connection，并保留来源错误状态；请求层旧的 original/resolve/quiet
  三套记录辅助方法已删除。真实领域仍分别拥有预览、SparkBuffer、容器和 raw 响应逻辑，没有
  强行合并异构结果。Python discovery 更新为 `581/581`，`server.py` 降至 2547 行。
- AB/PCK/USM 内部文件的 file ID、原记录、同逻辑来源 fallback 和容器内目标解析已形成单一高层入口。
  `LogicalFileSourceService.resolve_file_id_required` 明确区分 ID 不存在与 chunk/fallback 不可读，
  `InternalFileResolverService.resolve_file_id` 再消费已解析来源；Handler 不再为内部 preview/raw
  打开 SQLite。跨来源与容器参数透传均有回归，Python discovery 更新为 `580/580`。
- 主索引初审与可选原子重建、二级音频启动、Manifest 预热及五类健康报告合成已迁到
  `application_startup_service.py`。`main()` 不再伪造一个未初始化的 `BrowserHandler` 获取 Manifest，
  而是用 Manifest 服务、VFS reader 和进程级索引缓存直接预热；当前、重建、禁用自动修复及三类
  独立失败均有合成回归。Python discovery 更新为 `578/578`。
- JSONL 到派生 SQLite 的批量插入、source/all/effective entry、目录聚合、来源优先级和 meta 发布已
  迁到 `vfs_database_builder.py`；`server.py` 的数据库构建入口由 250 余行缩为直接导入。旧实现
  对 5000 项满批次与末尾残批次复制两套更新逻辑，新服务统一走同一函数，并以 batch size 2 的
  跨批回归证明 readable Persistent 会稳定覆盖 StreamingAssets、缺失来源仍保留诊断。Python
  discovery 更新为 `574/574`，`server.py` 当前为 2565 行。
- 派生 VFS SQLite 的 schema、查询索引、目录行与父级 entry 发布已迁到
  `vfs_database_schema.py`。服务入口不再持有 140 余行 DDL 与目录 SQL；内存数据库回归覆盖完整
  表/索引集合、重建清空旧数据以及同 scope 直接子目录计数。Python discovery 更新为 `572/572`。
- 索引 JSONL 的 plain/gzip/单成员 tar.gz 读取已迁到 `vfs_index_jsonl.py`，数据库构建不再让 HTTP
  模块持有压缩容器细节；多文件归档严格拒绝，四类输入边界均有合成回归。当前 Python discovery
  `569/569` 通过；当前机器只有 .NET 10，仓库 `global.json` 锁定的 9.0.200 未安装，因此保留白天
  `37` 项全绿基线，本轮没有改写 SDK 约束来制造假通过。
- VFS ChaCha20 轮函数、流处理和文件 nonce 规则已迁到 `vfs_crypto.py`。8 个索引/格式诊断工具改为
  直接依赖基础模块（文本探测也改从 `file_preview_service.py` 导入），不再为了读取一个加密切片
  初始化完整 HTTP 服务、运行时配置与 worker。`server.py` 保留旧名称重导出以兼容现有调用者；
  新增跨 64 字节块、计数器回绕、往返与输入长度测试，8 个真实 CLI `--help` 入口均可独立启动。
- AssetBundle map/preview 两阶段的异常边界已抽到 `assetbundle_export_service.py`。Worker、缓存、
  JSON 与领域校验失败统一成为携带 `mapFailed`/`exportFailed` 身份的应用错误；Handler 兼容方法
  只负责 `emit_errors` 与 HTTP 500 映射，内部动画/Manifest 调用不再依赖会隐式发送响应的底层
  协调器。合成回归覆盖参数与取消信号透传、两阶段结果不变及失败文档分类。
- 恢复服务拆分后 `ModelBlendService` 的测试桩签名，基础/动画 GLB provider 的取消与进度参数重新
  进入测试契约；相关 11 项测试通过。
- 新增 `Vfs.AnimeStudio.PInvoke` 包装工程。它只编译 vendor 中唯一的 `DllLoader.cs`，产品目标框架
  固定为 net9，不复制实现、不修改上游 csproj；锁定 SDK 9 的解决方案可直接 restore/build/test。
- 审计并修复 `64176df` 引入的历史回退：恢复 Humanoid Avatar pose、humanScale、IK/采样选项，
  恢复 runtime RVA 探针的 `SeDebugPrivilege`，更新 AnimationClip 1.1 fixture，并按已实现的 morph
  delta 语义覆盖 translation/scale 两条轨道。
- 依据 `docs/research/combat-config-runtime-bridge.md` 的三份本地技能完整消费证据，将
  `BlackboardSuperArmorValue.value` 恢复为 Int32；依据既有反编译布局恢复 8 字节
  `EnemyCheckAIMarkerInfo`，未改动 checkpoint 后新增的其他 MemoryPack 规则。
- HTTP GET/POST 路由表已从 `BrowserHandler` 抽到 `request_router.py`。新请求层只决定入口与
  index gate 顺序，不承载领域逻辑；健康/任务观察绕过 stale gate、数据 API 先过 gate、静态文件
  回退和 POST 404 边界均有独立测试。`do_GET` 从 104 行缩为 5 行。
- AB/PCK/USM 内部文件定位已统一到 `internal_file_resolver_service.py`：三类容器共享一次 VFS
  记录解析和统一结果对象，AB 路径直接复用 `assetbundle_browser.resolve_export_path`，删除了
  `server.py` 内的重复安全路径实现；preview/raw 不再先查记录后又各自重复查询。错误仍保留原有
  400/404/500 分类，AssetBundle worker 已报告失败时仍返回 None，避免重复响应。
- Manifest 普通导出、MonoBehaviour fallback 和 Cubemap 面集合已抽到
  `manifest_asset_file_service.py`。服务以不可变结果对象返回 bundle、目标文件/面集合、AssetMap
  元数据和 Manifest 资源身份；Handler 只保留 query 解析及 HTTP 错误映射。普通预览仍按原顺序
  先尝试 Cubemap 再回退普通文件，显式 `face=` 缺失仍为 404。旧测试不再从 `server.py` 间接导入
  `manifest_asset_entries`，改为依赖其权威模块。
- `InternalDirectoryService` 现在拥有 AB/PCK/USM 目录错误分类和可选工具能力摘要；Handler 不再
  拼装 vgmstream/usm-convert/ffmpeg 状态，也不再根据文件后缀二次解释异常。AB 的具体本机路径
  错误仍折叠为稳定的 `internal directory not found`，PCK/USM 保留领域错误文本。
  `handle_internal_list` 从 60 行降为 44 行。
- Projectile 的 Manifest 定位、精确 asset 选择、Bundle 解析、Unity worker 错误分类、导出解析和
  API 文档组装已迁到 `projectile_service.py`。`BrowserHandler.build_projectile_document` 保留为
  15 行兼容入口，后台任务、HTTP 与 AKEDB-compatible 路径继续调用同一入口；原 95 行实现不再
  混在请求类中。
- 审计发现 `3326e9d` 已将模型动画调用切换到 `SkeletalMorphService.build`，却遗漏删除原 72 行
  Handler 实现；旧方法甚至引用了当时已移除的解析函数导入。该不可达第二实现已删除，调用搜索
  确认只剩服务入口。AbilityEntity 同样按 Projectile 分层模式迁入 `ability_entity_service.py`，
  Handler 兼容方法由 45 行缩为 9 行。
- Manifest 模型到基础 GLB 的准备编排已迁到 `manifest_model_glb_service.py`。AvatarMesh 直接复用
  构建服务返回的已发布 `model.json`；普通模型则通过 `ModelRunStore.resolve_model_path` 验证指定
  immutable run 后取得产物，不在请求层复制缓存目录公式或扫描未发布目录。Handler 保留同名兼容
  委托供预览、动画与 Blender 服务调用，普通/Avatar/未发布运行三条边界均有直接测试。
- `StringPathHash.bin` 的有效 VFS 来源选择、缓存身份、版本校验和物化发布已迁到
  `string_path_hash_file_service.py`。失效重建不再先删除旧文件，而是写入唯一候选文件、校验长度后
  原子替换；复制或元数据发布失败会清理候选，已有缓存保持可恢复。原 Handler 方法仅保留兼容
  委托。
- AKEDB-compatible 的 TableCfg、Skill/Buff 集合、Projectile 与 AbilityEntity 路径语法已迁到
  `akedb_compatible_route.py`。该模块集中维护允许的集合与名称规则、URL 解码后的路径穿越拒绝、
  标准化 ID 和 400/404 分类；Handler 不再用 50 行条件树解释兼容协议，只分派解析后的封闭路由。
- VFS 逻辑文件的 effective 优先、来源排序与本地可读性回退已迁到
  `logical_file_source_service.py`。Manifest、Projectile、AbilityEntity 与共享运行时资源仍通过 Handler
  兼容委托调用同一规则；后续 Manifest 查询服务可直接依赖该基础设施，不应复制查询 SQL。
- 当前安装 Manifest 的打开和精确文件名候选查询已并入既有 `ManifestAssetService`，直接复用上述
  逻辑来源服务。候选查询只接受单个文件名并保留全部同名资产，统一构造 preview/raw URL；Handler
  只映射领域错误，不再自行解析 Manifest 或组装候选文档。
- Manifest Asset 解析与模型依赖闭包原先分别维护的 Bundle 来源 SQL 已统一到
  `bundle_source_service.py`。服务以单次批量查询取得所有候选，按统一来源优先级选择本地可读文件，
  并按调用方输入顺序分别返回成功来源和缺失 Bundle；空请求不打开数据库。
- `LogicalFileSourceService` 进一步统一了按 file ID 读取原记录，以及“指定记录可读则保持、否则按
  同 logical ID 来源排序回退”的规则；调用方可传入现有 SQLite 连接，避免额外连接和事务视图变化。
  `ManifestAssetService` 与 Handler 中原有的两套查询和回退实现均已删除。
- `ManifestVirtualDirectoryService.list_from_vfs` 现在负责虚拟目录入口、file ID、来源回退、ManifestIndex
  和目录文档的完整无 HTTP 编排，来源错误以稳定领域异常返回。普通 VFS 目录中的 Manifest 资产数
  统计也迁入 `ManifestAssetService.asset_count`；记录缺失或索引损坏按摘要语义返回 0，不再调用会先
  写出 404 的 Handler 方法，消除了随后继续发送目录 JSON 的双响应风险。
- `LogicalFileSourceService.resolve_file_id` 以单一连接完成 ID 查询和来源回退，Wwise media 的 VFS
  来源不再手写组合查询。AKEDB-compatible 的 TableCfg、集合清单和集合文件读取已迁入
  `akedb_compatible_data_service.py`，Handler 仅映射领域状态并发送统一来源头。
- MemoryPack 类型推断、可选解码器加载、值解码、消费位置与 discovered union 收集已统一到通用
  `memorypack_value_decoder.py`；AKEDB 在其结果上强制完整消费，普通文件预览复用同一结果显示诊断，
  不再保留第二套解码流程。
- worker 共用的 VFS 文件物化已迁入 `vfs_file_materializer.py`：普通切片和解密内容先写唯一候选文件，
  校验精确长度后原子替换；并发请求不再共享固定 `.tmp`，截断或失败会清理候选并保留旧目标。
- ManifestIndex 的内存缓存、锁和持久缓存入口已迁入进程级 `manifest_index_service.py`；MemoryPack
  schema/union 的惰性加载、线程锁与稳定失败缓存已迁入 `memorypack_schema_service.py`。HTTP Handler
  不再持有这两类进程状态。
- StringPathHash 的共享锁也移出 Handler；GLB 迁移后无调用者的模型缓存路径包装已删除。
  AssetBundle map/preview 统一通过一个 worker service factory 构造，不再重复列出缓存、worker、物化器和
  run store 依赖。VFS 索引 meta 与可用 scope 根目录文档已并入 `VfsDirectoryService.overview`，
  `/api/manifest` 不再直接执行查询和拼装结果。
- VFS 记录的偏移读取、limit、解密 IV 和范围边界已迁入 `vfs_file_reader.py`，worker、音频、配置与
  预览继续通过 Handler 兼容委托共享同一实现。TableCfg 的 file ID/source/type 解析和 SparkBuffer
  JSON 字节构建已迁入 `tablecfg_service.py`；AKEDB、普通预览和下载不再分别组合这些步骤。
  `server.py` 维持 3111 行，但请求类中的文件读取和 TableCfg 领域逻辑已替换为显式服务边界。
- 当前放行结果：Python `554/554`；.NET `37` 通过、`6` 个外部样本测试跳过。

### 2026-08-25

- 建立本文，冻结“源码内嵌、内部 worker 进程边界、VFS 自有协议”的方向。
- 记录 VFS `9a549f1`、权威 AnimeStudio `8cdec963` 和研究副本 `03336c4` 三个基线。
- 将持续更新文档写入阶段门禁；下一步为完成依赖/许可证盘点和导入方案。
- 从权威提交生成 SHA-256 为
  `52222131df3457f108f11b68ae90510989f4f779219278d622951118b3b4fdb2` 的临时源码归档；
  归档只用于盘点，未直接复制到生产目录。
- 新增 `unity-worker` 骨架、VFS 自有 `1.0.0` 握手协议和结构化未知操作错误。
- `dotnet build Vfs.UnityWorker.slnx -c Release` 通过，0 警告；2 个 MSTest 契约测试通过；
  进程级握手返回唯一已实现能力 `handshake`，未知操作以退出码 2 和
  `unknown_operation` 返回。
- 根据依赖策略反馈，改为“定制源码入库、明确开源第三方依赖初始化下载、发布包自包含”；
  ACL 与 RTM 分别锁定到 `3ee56854`、`d046447c`，归档带 SHA-256 校验。
- 从权威归档初筛 392 个文件；按依赖策略移出 ACL/RTM 的 130 个重复第三方文件，当前
  `unity-worker/vendor` 待跟踪文件为 262 个，并建立 `Vfs.AnimeStudio.Core` 构建项目。
  首次构建暴露 MessagePack 3.1.4 安全告警和 4 个上游编译告警，因此尚未把 P1 标为完成。
- MessagePack 已提升到 3.1.8，NuGet 漏洞审计无已知漏洞；以最小可记录补丁消除 4 个
  上游警告。当前 Release 构建为 0 警告、0 错误，2 个 worker 契约测试通过。
- 新增 VFS 自有 JSON 单次请求协议和 `exportMonoBehaviourRaw`。worker 只解析
  AssetBundle、MonoBehaviour、MonoScript，按精确 container 选择，并拒绝非空输出目录。
- 真实样本使用汤汤三段攻击 Projectile：Bundle SHA-256 为
  `14d52a41720e6e1da48e7fa66e6534fbd0269e89bb1c8cd8bf13cf167de21a08`；新 worker 与
  权威 AnimeStudio `8cdec963` CLI 均导出 1 个 4988 字节对象，SHA-256 同为
  `89e92655155aeb2fd232c75d120264b9f3aa3f55bc2a0252223af968eec68e59`。
- 新增共用 `MonoBehaviourExportPipeline`，Raw 与 TypeTree Dump 复用输入校验、Bundle
  加载、container 映射及精确对象选择，不再各自实现一遍定位逻辑。
- 新增 `exportMonoBehaviourTypeTreeDump`。同一汤汤样本的新旧 Dump 均为 1320 字节，
  SHA-256 同为
  `88b3b67142ea3766be66d6e00a6cf179deefa6324c562ec70d4cdf139d4707ab`；内嵌 TypeTree
  仅消费 4988 个序列化字节中的 228 字节，因此结果明确返回 `complete: false`，不把基础
  MonoBehaviour 外壳误报为完整组件。
- 对权威 `8cdec963` CLI 做同资源三路对照：`JSON` 仅 360 字节 MonoBehaviour 外壳；
  `Convert` 为 1080 字节 managed-reference 框架但各 `data` 为空；两者都不含
  `ProjectileComponentData`。聚焦解码主体实际位于被忽略的研究副本提交 `03336c4`
  （该副本是 grafted 根提交）中，其 `Exporter.cs` 另有 85 行未提交修正；两层都不在权威
  台式机分支，不能由权威上游自动重建，已列为下一项必须迁移并固化来源的 VFS 定制代码。
- 已在 `unity-worker/UPSTREAM.md` 固化研究解码器两层来源：`03336c4` 提交中的
  `Exporter.cs` blob `1b66a69e`、85/1 行工作树补丁 blob `82ca7105`，以及合并文件
  SHA-256 `c3571ce7...`。这些仅用于审计，构建不得读取被忽略研究目录，也不得把约 1 MB
  巨型文件整体搬入新产品。
- 已新增与字段语义解耦的 `ManagedReferenceRegistryScanner`，只恢复 registry 版本、RID、
  类型身份及 payload 字节边界。合成测试覆盖正常链和伪整数头；汤汤真实 Raw 的本地证据
  测试确认唯一 registry 位于偏移 84、版本 2、共 4 项，ProjectileComponentData header/
  payload 分别始于 1480/1560，payload 长 3428 字节。扫描器采用“完整强类型链优先、再退到
  null sentinel”的两阶段选择，避免连续零 payload 抢占后续真实 header。
- 已把旧巨型 Exporter 内的 payload 游标重写为独立 `ManagedReferencePayloadReader`；它只
  提供严格边界、有限浮点数、bool32 和对齐字符串读取，错误携带字段路径。后续 Projectile
  字段解码只能依赖这个基础件，不再依赖 CLI 全局状态。
- 已固定研究版两套二进制行为：2026-07-30 的 net9 构建（SHA-256 `1b664a2b...`）尚未
  包含 85 行布局修正，在汤汤样本的 `allowHitSameTarget` 处错位并返回 `$unparsed`；
  2026-08-02 的 net8 构建（SHA-256 `c5fbf6e4...`）包含该修正，能形成 `$decoded/$partial`
  组件。旧失败输出不再被视作正确兼容目标，但其结构化失败仍是必须保留的边界。
- 新增 `ProjectileComponentPrefixDecoder`，按修正后的字段顺序恢复到 `moveSegments` 末尾。
  汤汤真实样本从组件 payload 偏移 1560 精确消费至 2032，恢复 ID、2 秒结束时长、10 米
  结束距离、命中与碰撞字段，以及 `LaunchPoint -> Default -> TargetPoint` 移动段；剩余
  2956 字节作为未迁移 tail 明确返回。
- 新增独立 `ProjectileMoveModeDictionaryDecoder`：汤汤样本的 `Default` 字典值按研究证据
  固定为 124 word，先恢复 traceType、traceTime、traceUntilDistance、moveType、parabolaDef，
  其余 115 word 保留原始证据；整个字典精确消费 `[2032, 2548)`。该固定边界仍标记
  `partial`，不会推广成未经多样本验证的通用规则。
- 新增 `ProjectileMainEffectFinishDecoder`，显式支持“序列化 finishType + BlackboardDouble”
  和“仅 BlackboardDouble”两种已知形态。汤汤样本恢复 `Default` 与 40 米，并把后续边界推进
  到 2564；当前剩余 2424 字节为特效、声音和最终距离/倍率尾部。
- 从台式机 VFS 索引提取庄方易两枚历史样本，AB SHA-256 分别为 `ecf674d5...`、
  `cb5084d0...`，Raw 分别为 5784 字节且 SHA-256 为 `2c73c17d...`、`56095f93...`。
  三样本均验证 124-word MoveModeData：汤汤阶段边界为 2032/2548/2564，庄方易两枚均为
  2296/2812/2828。剩余 Raw words 分别为 606、739、739。
- 新增正式 `decodeProjectileComponent` 操作，要求精确 container、唯一组件和严格 ID
  相等；所有未理解尾部被完整消费为 Raw words，结果返回 `decodeStatus: partial`。汤汤
  聚焦 JSON 为 63662 字节，SHA-256 `2c9901df...`，现有 Python
  `load_projectile_export` 可直接识别根组件；重复写同一输出目录返回 `output_not_empty`。
- 当前 Release 构建仍为 0 警告、0 错误；常规运行 16 项通过、2 项本地证据测试因未配置
  fixture 跳过，显式配置后真实资源审计覆盖汤汤及庄方易两枚样本并通过；现有 Python
  ProjectileData 10 项测试通过；逐项目 NuGet 漏洞审计无已知漏洞。
- Release 构建继续保持 0 警告、0 错误；worker 契约与输出边界测试增至 6 个并全部通过；
  NuGet 漏洞审计仍无已知漏洞。
- 新增 Python 唯一 `UnityWorkerClient`，集中处理 worker 发现、版本化请求、超时、结构化错误
  和启动产物身份。Projectile 是首条迁移链路：新结果进入独占 run 目录，只有完整校验后的
  `meta.json` 指针可通过 `os.replace` 原子发布；旧 AnimeStudio MonoBehaviour 路径暂不改动。
- Projectile Python 接入已用真实汤汤 Bundle 验证，能够经新 worker 产生并重新读取
  `partial` 聚焦组件；缓存测试覆盖命中复用、唯一产物/哈希校验及失败不发布指针。
- `/api/health` 已报告 worker 握手、协议兼容性、必需能力、可选工具和未迁移旧工具；
  可选工具缺失不会把核心服务误判为不可用。
- 共用 MonoBehaviour Raw 已迁移到新 worker，并与 Projectile 复用同一原子导出框架，
  没有再复制缓存发布逻辑。汤汤真实样本仍精确导出 4988 字节，SHA-256 为
  `89e92655...`，连续调用命中同一已发布 run。
- worker 缓存身份除启动 EXE/DLL 外，还包含发布目录全部直接文件的确定性元数据摘要；
  单独重编译领域解码 DLL 时也会可靠失效，不再依赖“顺便更新 apphost”这一偶然行为。
- 原子导出框架已从单产物扩展为多产物：逐项拒绝路径逃逸、重复路径、大小或 SHA-256
  不一致，并在发布前验证派生文件。TypeTree Dump 已迁移到该框架，多个 MonoBehaviour
  的文本会在同一 run 内生成 `combined-dump.txt` 后一起发布。
- 汤汤真实 TypeTree 回归仍为 1320 字节、SHA-256 `88b3b671...`，消费 228/4988 字节并
  明确报告 `complete: false`；连续调用命中同一 run。旧 MonoBehaviour 专用 CLI override
  已删除，因为生产调用点已不存在。
- `UnityWorkerClient` 新增可取消执行：取消前不会启动进程，运行中取消会 terminate、等待
  回收，超时再 kill，并返回稳定 `worker_cancelled`。请求临时目录只会在进程退出后清理。
- 新增持久化 `BackgroundTaskRegistry` 和 Projectile 首条异步入口。状态与结果位于独占任务
  目录，`status.json` 是原子指针；内存仅保存当前进程的取消事件。成功结果写盘后才发布，
  取消/失败不含结果指针，服务重启遗留的非终态任务会转为 `task_interrupted`。
- 新增 `buildAssetMap`，复用权威 AnimeStudio 的单 Bundle 对象索引实现及锁定类型配置，
  但由 VFS 协议显式传入输出类型和稳定 `sourceLabel`。上游写入的临时绝对路径会在 worker
  边界被替换，因而相同资源不会随机器或缓存 run 改变 `AssetEntries[].Source`。
- AssetMap 已接入共用多产物原子 run 框架；旧 CLI 目前只继续承担 `Convert`。死亡少女骨骼
  Bundle 以 `Mesh`、`Material`、`Avatar` 三类得到 32 个条目。权威源码能把 container 解析为
  `assets/beyond/arts/entity/npc/major/girl/deathgirl/models/sk_npc_major_deathgirl_01.fbx`；本机旧
  net8 研究二进制的同一条目则为空，因此旧研究二进制不是等价目标，权威来源结果优先。
- AssetMap 与 CABMap 的边界已明确分开：前者索引单个 Bundle 内对象；后者表达多个 Bundle
  的 CAB 名、稳定输入 ID、序列化文件偏移及依赖。因此先定义无机器路径的 VFS 多输入
  CABMap JSON 契约，再让对象导出显式消费它；不得复制旧 `BuildCABMap` 写全局 `Maps/`
  目录、`UseCABMap` 再隐式读取的行为。
- 新增 `buildCabMap` VFS 协议：输入为显式 `{ inputId, inputPath }` 列表，JSON 产物只含 CAB
  名、稳定输入 ID、SerializedFile 偏移和外部 CAB 依赖，不保存 baseFolder 或物理路径。
  实现直接复用权威 `AssetsManager` 的 Bundle/SerializedFile 读取，不调用旧全局 CABMap。
  输入 ID、输入路径或 CAB 名重复均明确失败；死亡少女骨骼真实 Bundle 已验证可产生非空映射
  且产物不含样本绝对路径。此能力尚未替换对象导出的 `UseCABMap`，不能误标为整条链路完成。
- worker `0.7.0` 新增 `exportIdentifiedTextures`，与对象快照共用显式输入和 CABMap，只接受
  `sourceFile + pathId` 精确选择并返回 PNG 的尺寸、字节数和 SHA-256。模型与 AvatarMesh
  生产调用已迁移，旧名称正则、全局 CABMap 和相关命令辅助函数已删除；真实证据为普通模型
  39/39、AvatarMesh 38/38 张 PNG 与旧产物逐字节一致。
- 普通模型与 AvatarMesh 构建改为独占 run：每轮输入、CABMap、对象、纹理、ModelDocument
  和 geometry 不再覆盖固定目录；run 内完成标记先落盘，根 `run.json` 最后原子切换。几何与
  纹理 URL 绑定 run ID，可继续读取历史已发布 run。庄方宜完整链路后的故意 CABMap 失败验证
  了发布指针、旧 run 和 9,971,028 字节 geometry 均未变化；56 Bundle AvatarMesh 完整链路
  仍得到 437 节点、13 Mesh、11 Material、13 Skin、38 张纹理，3,381,104 字节 geometry 与
  旧产物逐字节一致。
- worker `0.8.0` 新增精确 container 的 `exportCubemapFaces`，严格验证六份连续完整 mip 链，
  沿用 Unity 面序且不做 Texture2D 垂直翻转。角色环境 BC6H 样本的六张 PNG 与旧 CLI
  逐字节一致。Cubemap 同时接入共用独占 run；领域六面集合会在根 `meta.json` 原子发布前及
  缓存命中时复验，不完整结果不再短暂成为可见缓存。
- worker `0.9.0` 新增 `exportBundlePreviewMedia`，协议仅允许 `Texture2D`、`TextAsset` 和
  `VideoClip`，没有任意类型 fallback。产物以类型、名称和 PathID 稳定命名，返回 source file、
  container、长度、SHA-256 和跳过原因。真实 Texture2D 与 TextAsset 样本分别输出 206 字节
  PNG 和 2297 字节 JSON，SHA-256 均与旧 CLI 逐字节一致；本地缓存尚无 VideoClip 样本，且
  生产通用导出尚未切换，因此下一步仍须完成 run 接入和该类型真实验证。
- worker `0.10.0` 将 `Sprite` 纳入同一固定预览媒体协议，仅链接权威 Utility 的
  `SpriteHelper.cs`，没有引入 FMOD 或动画 YAML/ACL 闭包。它保留 atlas、裁剪、packing
  rotation 和 tight mesh mask 逻辑；真实 `deco_bg04` 样本输出 6058 字节 PNG，SHA-256
  `eee6d7af...` 与旧 CLI 逐字节一致。
- 通用 AssetBundle 浏览已把上述五类切入独占 `asset-export/runs/<id>`：worker 文件先按
  长度和 SHA-256 校验，尚未迁移的 AudioClip/AnimationClip 只允许写各自类型目录并作为
  derived files 再校验，最后原子替换根 `meta.json`。缓存命中会同时复验主产物和派生产物；
  AssetMap 身份集合还必须与 worker 的 artifact + skipped 集合完全一致。真实 `100542`
  Bundle 已在同一 run 发布一张 Texture2D 和一张 Sprite，第二次读取直接命中该 run。
- worker `0.11.0` 将预览媒体路径补全为 `类型/source file/名称_p<PathID>`，防止同一 Bundle
  含多个 SerializedFile 时 PathID/name 碰撞。服务端发布门禁也从集合升级为包含类型、PathID、
  名称和 container 的多重计数，重复身份不会被集合去重掩盖。对本机 6848 个已有通用 Bundle
  缓存的只读统计为 Texture2D 2404、Sprite 1083、TextAsset 231、AnimationClip 77，AudioClip
  与 VideoClip 均为 0；因此不为无真实样本的 AudioClip 引入 FMOD，剩余实际消费者集中在
  5 个 Bundle 的 AnimationClip，并入后续统一动画迁移。
- worker `0.12.0` 新增精确 `PathID + expectedName` 的 `exportAnimationClipJson`。服务先以
  AssetMap 锁定唯一对象，再由 worker 输出并校验 `AnimeStudioAnimationClip/1.1.0`，产物进入
  独占 run 后原子发布；模型动画生产路径不再扫描旧 CLI 输出。所需 ACL/RTM 依赖由 lock
  文件固定，`Build-EndfieldAcl.ps1` 在仓库内重建 x64 原生桥。未压缩 `Recorded (16)` 与
  Endfield ACL 压缩的佩丽卡 idle 两份样本均与旧生产 JSON 逐字节一致，后者包含 696 条曲线、
  3,294,623 字节，SHA-256 为
  `5ae4a043bbceb33655c336605c602448d5ed1d457a5c8613cc857fffbf9d82db`。
- worker `0.13.0` 将通用 AnimationClip YAML 纳入固定 Bundle 预览白名单，并从旧派生 Convert
  移入 worker 主产物与 AssetMap 身份门禁。本机全部 5 个缓存 Bundle 共 77 个片段均能输出；
  逐文件 SHA-256 审计为 77/77 与当前旧 CLI 一致；
  对话 Bundle 的 72 个片段包含 45 个被严格 AnimationJSON 拒绝的 Euler/PPtr 片段，证明
  YAML 浏览与播放 JSON 必须保留两个契约。`Recorded (16)` YAML 的 SHA-256 为
  `918b1637d8ca340fa4a8d60aa85ebf7d428b4c5be9bcdaf58816c69545cb389b`；佩丽卡 ACL 压缩
  idle YAML 为 28,140,391 字节，SHA-256 为
  `7b050e940a718ee6feddbe37ec701f4105a11bb103b8e372df729942098d8ff3`，均与当前旧 CLI 一致。
- worker `0.14.0` 将 LODGroup 加入对象快照白名单，但不依赖不存在的专用 CLR 类型，而是
  严格读取资源内嵌 TypeTree。庄方宜真实闭包恢复 4 个 LOD 层级及各层 Renderer PPtr；本地
  证据测试同时要求导出身份/container 与旧缓存一致、`m_LODs` 非空且引用元数据实际包含
  `$.m_LODs[...]` 路径。缺失 TypeTree 会返回 `object_type_tree_missing`，不发布伪成功结果。
- 生产服务已删除最后的 AnimeStudio CLI 定位、健康检查和任意 Convert 回退。无当前消费者的
  AudioClip 不引入 FMOD；若 AssetMap 只含无预览契约的类型，服务会发布带
  `unsupportedPreviewTypes` 的可验证空 run，而不是伪造成功产物或依赖外部 CLI。
- 已删除只为外部 `AnimeStudio.CLI.exe` 校验 `vfs-tool-manifest.json` 的孤立 Python 模块、
  静态工具清单和对应测试；架构与启动文档统一改述为仓库内 VFS Unity worker。保留的旧 CLI
  名称只用于迁移历史、逐字节等价证据和 vendor 来源说明，不再构成发布配置。
- `task_service.py` 已成为任务请求的首个应用服务边界：状态查询、取消状态码和已登记产物解析
  不再由 `BrowserHandler` 直接读取注册表异常与私有元数据。真实 HTTP 回归保持成功查询 200、
  终态取消 200、未知任务 404、产物下载 200；四类任务提交也已统一经过该服务，不再从
  Handler 直接调用注册表。模型、单动画和 Blender 请求已由 `task_requests.py` 统一解析成
  不可变 DTO，保留原有错误文本和批量上限。`task_operations.py` 已集中四类任务的种类、
  独立构建实例、取消事件和进度回调绑定，后台闭包不再由 Handler 组装。
- 模型、Blend 和单动画任务的 DTO→Manifest 资源解析→后台提交链已集中到
  `model_task_submission_service.py`。服务保证可选动画不产生多余解析、Blend 批量输入先通过数量
  门禁、三类任务使用同一 resolver/operations 边界；Handler 只保留 Blender 能力门禁、应用错误
  到 HTTP 的映射和统一 202/no-store 响应。真实重启后，三类非法输入仍分别返回原 400 文案，
  不存在的 manifest ID 仍稳定返回 404 `file not found`，且不会创建后台任务。
- `manifest_asset_service.py` 已接管 manifest 文件来源 fallback、AssetInfo 查找、Bundle 来源
  排序与可读性选择，并通过应用错误返回原有 400/404 语义，不直接写 HTTP。合成 SQLite 测试
  覆盖失效 Persistent manifest 回退到可读来源、缺失 asset、缺失 Bundle、模型类型校验与批量
  动画去重排序。模型、可选单动画和批量动画任务已直接调用该服务，不再绕经 Handler 的 HTTP
  查询兼容方法；当前庄方宜 `451359/263486` 的真实模型任务在拆分后仍成功，非模型资源会稳定
  返回 400。同步模型预览、GLB、动画候选和 Blender 入口也已复用统一模型解析适配，移除了各
  Handler 重复的入口类型判断；同步批量动画解析复用 `resolve_many`。同步 query 的
  manifest/asset 身份及批量动画集合由 `manifest_asset_requests.py` 独立校验，Handler 仅负责
  把请求错误映射为 400。
- Blender `.blend` 构建已移入 `blender_export.py`，保留缓存新鲜度、取消时 terminate/kill、
  五分钟超时和临时文件原子发布；WEM→WAV 已移入 `audio_export.py`，失败不会留下或发布半成品。
  `server.py` 不再直接调用 `subprocess.run`/`Popen`，P2 Python 调用收口门禁完成。
- manifest 派生 SQLite 增加稳定 VFS 内容身份别名。别名只在完整读取并验证 SHA-256 缓存后原子
  发布，schema/fingerprint 不符或 JSON 损坏会回退重读。本机 46 MB manifest 首次冷读约 60 秒，
  建立别名后跨进程重启的模型任务创建降至 161 ms。该优化不替代主索引新鲜度校验：当前
  `451359` Persistent chunk 已缺失，实际回退到旧 Streaming 来源，健康状态仍需下一阶段明确
  报告 stale 并接入自动重建。
- `index_freshness.py` 已加入启动审计：对建库时标记存在的去重 chunk 重新检查实际文件，缺失
  时 `/api/health` 返回 `degraded` 与 `indexFreshness.status=stale`，示例只暴露来源、block hash
  和 chunk 文件名，不泄露本机绝对路径。本机审计 1,032 个历史可用 chunk，发现 49 个已消失，
  耗时约 1.08 秒。未发现缺失时只报告 `unverified`，因为旧索引尚未保存 `.blc` 内容摘要；自动
  重建与原子切换仍未实现，P4 对应门禁保持未完成。
- 台式机权威 `D:\Temp\endfield-re\index_endfield_vfs.py` 已按规范化文本原样纳入
  `tools/index_endfield_vfs.py`，不再把固定盘符脚本作为隐式生产依赖。合成 fixture 覆盖 BLC
  ChaCha20 解密、CRC、code version 4、chunk/file 元数据及 JSONL 逻辑身份；下一步在其上增加
  `.blc` 内容身份并接入新 run 构建、验证和原子 SQLite 切换。
- 生成器现会记录每个 `.blc` 的相对路径、长度与 SHA-256；只有全部匹配才报告 `current`。
  `index_rebuild.py` 在活动库同目录创建临时 run，生成 JSONL、构建候选 SQLite、执行
  `PRAGMA integrity_check`、验证非空记录与 BLC 身份后才以 `os.replace` 原子切换。任何阶段失败
  都清理 run、保留旧库并报告 `indexRebuild.status=failed`。真实首次启动从当前游戏数据恢复
  904,536 条源记录和 455,538 条 effective 记录，验证 1,063 个可用 chunk 与 43 个 BLC 后恢复
  `ready/current`；Persistent manifest 更新为 47,258,312 字节。后续重启不重建，manifest 预热
  后庄方宜当前 PostModel `451359/99084` 的任务创建耗时约 48 ms。旧 asset index `263486` 已不再
  指向模型，证明调用方不能跨 manifest 版本保存裸 asset index 而缺少 manifest 内容身份。
- HTTP 路由增加统一当前索引门禁。除 `/api/health`、已有任务状态/取消/产物和静态页面外，
  所有数据查询及任务创建只有在 `indexFreshness.status=current` 时执行；其他状态返回不缓存的
  HTTP 503，载荷包含 `code=index_stale`、完整 freshness 报告和 rebuild 状态，不会再从陈旧库
  回退旧来源或误报 404。
- `runtime_config.py` 已成为环境配置的单一加载入口。`VFS_BROWSER_DATA_ROOT` 统一迁移主库、
  manifest/任务缓存、AudioDialog/Wwise 索引和 Shader 归档，原有单项 override 保持兼容；默认
  JSONL 也改为 data root 内路径，删除了对相邻 Endaxis checkout 的隐式依赖。P4 配置项尚余
  端口与结构化日志入口，故总门禁仍保持未完成。
- `tool_registry.py` 统一解析 Blender、vgmstream、usm-convert 与 ffmpeg 的显式路径或 PATH
  命令。健康诊断、模型下载入口、音视频预览能力、转换缓存身份和实际适配器执行均消费同一
  能力快照；缺失工具只关闭对应派生能力。`server.py` 已不再散落这些工具的 `is_file/exists`
  判断，P4 的可选工具能力注册表子门禁完成。
- `cache_versions.py` 集中登记 14 类服务端派生缓存产物版本，`server.py` 原有模型、Bundle、
  MonoBehaviour、Projectile、Cubemap、StringPathHash、音频和视频缓存常量均改为具名查询，
  健康检查同步暴露版本快照。游戏 VFS 协议、外部对象契约和持久数据库 schema 保持各自
  所有权，不为表面统一混入缓存注册表。
- `runtime_config.py` 现同时读取 `VFS_BROWSER_HOST/PORT/LOG_LEVEL/LOG_FORMAT`，CLI 参数只覆盖
  本次进程并复用相同端口校验。`service_logging.py` 统一数据库构建、索引重建、manifest 预热、
  HTTP 请求和服务生命周期事件，默认输出可采集的单行 JSON，保留文本格式用于本地调试。
  P4 的配置、缓存版本、日志、端口与数据根目录子门禁完成。
- Projectile 任务已与模型、模型动画和 Blender 任务统一使用进度任务入口，至少发布 decode
  起点与 ready 终点；四类入口均具备持久 ID、取消事件和原子状态。注册表回归覆盖成功结果
  先落盘再发布、取消/领域失败不产生结果指针、领域错误码保留、进度持久化和服务重启后非
  终态转为 `task_interrupted`；Unity worker 与 Blender 适配器测试覆盖 terminate/kill 及进程
  回收。P4 长任务完整协议子门禁完成。
- USM 虚拟目录、源身份、工具身份、缓存命中和 MP4 原子发布已从 `BrowserHandler` 移入
  `usm_video_service.py`。缓存身份不再只比较容易碰撞的文件长度，而是包含 VFS record、
  offset、内容 MD5 以及 usm-convert/ffmpeg 的路径、大小和修改时间；失败转换会清理独占临时
  文件。对应 `usm-video` 缓存版本升至 2，合成测试覆盖目录契约、URL 解码、缓存复用、同长度
  来源变化失效及失败不发布。真实 effective USM `5154` 可列出
  `mp4/sketch_guide_video_battle_enemy_break_poise_1_ct.mp4`；首次转码生成 347,495 字节 MP4
  约 284 ms，第二次约 15 ms 命中缓存。真实验证还发现 ffmpeg 依赖输出文件保留 `.mp4`
  扩展名，该约束已进入合成回归。这是 P4 单体 Handler 拆分的下一块已完成边界。
- PCK 媒体索引与 WEM/WAV 虚拟目录已移入 `audio_package_service.py`，并直接复用已有
  `audio_package.py` 权威 AKPK/BNK 解析器；`server.py` 中一套会宽松跳过截断行的重复解析代码
  已删除。索引缓存身份由单纯文件长度升级为版本、record、offset 和内容 MD5，meta 通过独占
  临时文件原子发布，`audio-package` 缓存版本升至 2。合成回归覆盖索引复用、同长度来源变化
  失效、WEM/WAV 分层目录和严格内部路径解析。
- WEM 直读/Bank 解密、媒体级缓存身份、WEM 原子发布和 vgmstream WAV 派生也已进入
  `audio_package_service.py`。普通浏览、AudioDialog 与 Wwise 聚合入口复用同一输出服务；包内容
  身份成为缓存目录的一部分，同 record、同长度的热更不会复用旧 WEM。服务严格校验读取长度，
  短读不会发布文件；合成测试覆盖同 media ID 不同 offset、同长度不同包身份及失败清理。真实
  日语 PCK `839277` 严格解析出 `wem/34/874935228.wem`（1,626,835 字节）；首次 vgmstream
  派生 WAV 约 583 ms，产物 21,235,244 字节，第二次约 12 ms 命中缓存。热更空包 `839270`、
  初始 Bank 包 `1` 也能严格解析，没有依赖旧宽松解析器兜底。
- 已发布 AssetBundle run 的安全目录解析、递归目录统计、预览条目构造和 AssetMap 元数据回绑
  已移入 `assetbundle_browser.py`。带十六进制 PathID 后缀的导出文件会恢复有符号 64 位 ID 后
  精确匹配；同类型同名但无法唯一确定的文件不会猜测元数据。合成测试覆盖路径逃逸、歧义拒绝、
  递归统计和精确文件查找。真实缓存 `100542` 的 Sprite 与 Texture2D 分支均能递归列出各自
  `deco_bg04_p<pathId>.png`，两个文件都精确回绑到正确 AssetMap 类型；worker 调度与 run 发布
  已在下一阶段进入通用服务。
- AssetBundle、Projectile、Cubemap、MonoBehaviour 与动画共用的 Unity worker run 校验、缓存
  复验、派生文件身份和 `meta.json` 原子发布已从 `BrowserHandler` 移入
  `worker_run_service.py`。按 `meta.json` 指针隔离的发布临界区阻止同一缓存被并发重复构建或
  交错切换，同时允许不同资源并行；构建、产物验证
  或派生步骤失败时只回收本次未发布 run，上一份已发布指针保持逐字节不变。合成回归覆盖缓存
  复用、同指针单次构建、异指针并行、路径逃逸拒绝、首次失败清理和已有发布后的失败重建；
  各资源特有输入与领域验证仍留待
  后续按应用服务边界继续拆分，因此 P4 单体拆分总门禁保持未完成。
- manifest 资源的 source identity、Bundle 切片暂存、Worker 参数组装以及 Projectile、Cubemap、
  MonoBehaviour TypeTree/Raw 的领域验证已移入 `manifest_worker_service.py`；AnimationClip 通过
  同一服务的通用入口复用上述身份和发布语义。`BrowserHandler` 仅保留既有方法签名的薄转发，
  同步入口和任务调用方无需迁移。相关回归覆盖精确 container、缓存复用、Worker 身份失效、
  Cubemap 六面完整性、TypeTree 派生文件损坏重建、Raw 单产物约束和 Projectile 失败不发布。
  Projectile HTTP 路由测试也改为显式声明 current 索引，避免 P4 新鲜度门禁让领域路由测试
  偶然返回 503。
- 单 Bundle AssetMap 的 source identity、Bundle 暂存、Worker 调用、单产物约束和
  `AssetEntries` JSON 契约已移入 `assetbundle_worker_service.py`。Handler 只保留服务异常到
  既有 `mapFailed` HTTP 响应的适配；动画精确 PathID 选择和媒体预览继续复用同一返回契约。
  合成回归证明缓存仅构建一次、稳定 `sourceLabel` 与六类 included type 均未变化。
- AssetBundle 媒体类型选择、预览 Worker 调用、AssetMap 多重集合等价验证、无支持类型时的
  空 run 发布和完整返回元数据也已移入同一服务。Handler 仅先取得 Map 再调用预览服务，并将
  失败映射到 `exportFailed`。回归覆盖 Texture2D、AnimationClip、AudioClip 降级、缓存复用，
  以及 Worker 返回错误 PathID 时拒绝发布并清理未完成 run。
- AnimationClip 的 FBX 子资源名称回退、AssetMap 唯一项选择、精确 PathID/名称回验和
  `AnimeStudioAnimationClip/1.1.0` 文档验证已全部移入 `manifest_worker_service.py`。Handler
  只负责取得 AssetMap 后调用领域方法，原通用 Worker 导出兼容方法已无调用者并删除；回归
  证明 Worker 返回不同 PathID 时不会发布缓存指针并会清理未完成 run。
- 普通模型与 Avatar 模型的缓存路径、活动/显式不可变 run 解析和缓存复验已移入
  `model_run_store.py`。两条模型管线现在统一验证版本、完整 source identity、ModelDocument、
  geometry 和声明过的 texture 目录；Avatar 仍保持 geometry 必需，普通模型仅在声明 buffer
  时要求。合成回归覆盖路径逃逸、未完成 run、来源变化、缺失 geometry/texture 和无效文档。
  模型产物和完成标记写入、活动指针原子切换也已由 store 统一，替换失败会清理临时指针并
  保留旧指针。真实普通模型缓存 `132923/263486` 无需重建即可由新 store 命中，恢复 565 节点、
  44 网格和 9,971,028 字节 geometry。现存 Avatar 缓存仍是没有 `selectedRun` 的旧格式，按既有
  完成标记规则不视为可复用 run。共享 Worker 编排和各自 ModelDocument 组装是后续拆分边界，
  P4 总门禁保持未完成。
- 普通模型与 Avatar 模型重复的 Bundle 暂存、稳定 input ID、CABMap、对象快照和精确纹理
  Worker 调用已移入 `model_worker_service.py`，三类 Worker 结果均复用统一路径、大小和 SHA-256
  校验。两条管线只保留各自输入闭包、类型/container/selection 决策和 ModelDocument 组装。
  合成回归覆盖多 Bundle 顺序与身份、三类参数传递、重复/逃逸输入拒绝和错误产物摘要拒绝。
  纹理输出遇到非空目录会保留现场并明确失败，不再由通用服务递归删除任意路径。下一阶段继续
  拆普通与 Avatar 各自的领域文档组装，P4 总门禁保持未完成。
- 普通 manifest 模型的对象加载、裸快照拒绝、唯一根节点选择、层级与 geometry 构建、材质
  Texture2D 收集、精确图片回绑、缺失纹理/Bundle 诊断和最终语义校验已移入
  `ordinary_model_document_service.py`。缓存 identity 仍读取原层级 builder 的文件时间，纯重构
  不会让全部真实模型失效。回归覆盖最小完整层级、裸 JSON 拒绝和 `sourceFile + pathId` 纹理
  回绑。Avatar 模型领域组装在紧随阶段继续拆分，P4 总门禁保持未完成。
- Avatar 模型的已选对象解释、材质纹理 selection、`sourceFile + pathId` 到纹理名称/URI 回绑、
  重名拒绝以及静态 ModelDocument/geometry 构建已移入 `avatar_model_document_service.py`。
  Handler 仍负责 Avatar 资源计划与 Bundle 闭包，但不再解释 Worker 对象或拼装最终文档。合成
  回归覆盖资源传递、大小写稳定的纹理身份、最终 builder 参数和重复名称拒绝。普通与 Avatar
  两条模型领域组装边界至此均已拆出，P4 总门禁仍因 Handler 中的高层协调逻辑保持未完成。
- 普通与 Avatar 模型的 source identity 已移入 `model_source_identity.py`，集中覆盖入口 record、
  chunk mtime、asset、依赖闭包、缺失依赖、Avatar 计划/LOD、builder 和 Worker 工具身份。庄方宜
  真实普通模型 `132923/263486` 的 70 项依赖由新函数重建后与既有 `run.json` source 逐字段完全
  相等，证明模块迁移不会造成无意义缓存失效。下一阶段继续抽取 run/进度高层协调。
- 普通与 Avatar 模型的主 request ID、CAB/对象/纹理子请求 ID、run 内目录、步骤列表、进度上报
  和最终 metadata 已移入 `model_build_session.py`。既有 `model-*`/`avatar-*-lod*` 格式、阶段名、
  总步数、scope 与附加字段保持不变；run store 仍独占活动指针发布职责。合成回归固定时间和
  UUID 验证两类精确 ID、路径、步骤复制及进度载荷。Handler 中模型高层协调已显著收窄，但
  资源计划与各服务调用次序仍待形成独立应用服务，P4 总门禁保持未完成。
- 普通 manifest 模型的缓存读取、session、输入暂存、CAB/对象/纹理步骤、文档组装、URL、metadata
  和原子发布次序已整体移入 `ordinary_model_build_service.py`。`BrowserHandler` 的
  `ensure_model_hierarchy` 仅保留原签名并转发输入，任务与同步调用方无需迁移。合成回归覆盖缓存
  短路、四阶段进度、Worker 步骤顺序、最终文档校验和发布回调。下一阶段对称抽取 Avatar 高层
  应用服务，P4 总门禁保持未完成。
- Avatar 模型的计划读取、依赖闭包校验、source identity、缓存读取、五阶段 Worker/文档流水线、
  产物 URL、metadata 和原子发布已整体移入 `avatar_model_build_service.py`。Handler 仅注入必须
  访问 manifest 索引和本地 Bundle 表的三个查询能力，`ensure_avatar_mesh_model` 成为兼容转发。
  合成回归覆盖计划后缓存短路、纹理路径、container selection、步骤/进度/发布，以及缺失依赖在
  Worker 前失败。普通与 Avatar 两条模型路径的高层协调现已对称，后续可收口遗留 Handler
  兼容方法和真实 Avatar run 门禁，P4 总门禁保持未完成。
- 抽取后使用当前 `451359` manifest 的秦桔臣 AvatarMesh `191036` LOD0 从正式
  `POST /api/tasks/model` 入口完成真实冷构建和原子发布：321 节点、9 Mesh、9 Material、9 Skin、
  28 张纹理、2,252,808 字节 geometry，ModelDocument 校验为 0 错误；run 完整记录 CABMap、对象
  与纹理三步，根指针 `selectedRun` 与不可变 run 完成标记一致。该证据确认新 Avatar 应用服务、
  后台任务和 HTTP 结果链路贯通。审计时还发现误把普通 VFS record 当 `manifestId` 会令原始
  `brotli.error` 穿透请求线程；ManifestIndex 现将其归一为稳定输入错误，由资源服务返回结构化
  400，不再让客户端只看到连接提前结束。P4 模型应用服务子门禁完成。
- 已发布 ModelDocument/geometry/texture 到基础 GLB 的读取校验、纹理 URI 身份与路径边界、材质
  计划、导出器版本缓存身份、取消和原子 GLB 替换已移入 `model_glb_service.py`。普通与 Avatar
  共用相同服务；Handler 仅负责先取得对应不可变模型 run，并保留供动画和 Blender 调用的兼容
  方法。合成回归覆盖缓存复用、图片读取、record/LOD 不匹配、目录逃逸和构建前取消；重构后
  无调用者的 Avatar 缓存路径包装已删除。下一阶段收口动画 GLB 与基础 GLB 重复的派生发布逻辑。
- 动画选择键、动画 Document/geometry 到 GLB 的来源列表、材质计划、缓存身份、取消和发布也已
  并入 `model_glb_service.py`。基础与动画 GLB 不再使用会被并发请求共享的固定 `.tmp` 文件；GLB
  和 metadata 分别通过 UUID 临时文件替换发布，失败现场不会遗留临时产物。Handler 的动画循环
  只负责精确片段导出、逐项绑定和 skip-incompatible 决策。回归覆盖动画选择顺序、附加 geometry、
  缓存复用、metadata 和临时文件清理；下一阶段抽取动画请求 identity、结果清单与绑定应用服务。
- 多动画请求 identity、请求缓存恢复、逐片 AnimationJSON 导出、文档副本绑定、无兼容轨道拒绝、
  `clipExport`/`modelBinding` 诊断、全失败回退基础 GLB、动画 GLB 调用和结果清单发布已移入
  `model_animation_service.py`。`AnimationExportIssue` 与 `AnimatedModelBundle` 领域结果也迁出
  `server.py`，原模块继续导入名称以保持调用兼容。请求清单改用 UUID 临时文件原子替换；合成与
  既有路由回归覆盖排序、累计 geometry、缓存复用、两类跳过和全失败。Handler 的
  `ensure_animated_model_glb` 现为薄转发。当前秦桔臣 AvatarMesh `191036` 与通用待机动画
  `324382` 已从正式 `POST /api/tasks/model-blend` 冷路径通过：1/1 动画绑定、0 诊断，发布
  20,357,548 字节动画 GLB、17,021,947 字节 Blender 产物和一致的请求清单，任务约 7.5 秒成功。
  下一阶段收口模型 Blender 应用服务。
- 基础/动画 GLB 选择、批量时启用 skip-incompatible、全部动画失败时禁止启动 Blender、Blender
  阶段进度和私有 artifact 名称/类型/路径结果已移入 `model_blend_service.py`。既有
  `BlenderExportService` 继续独占外部进程、取消和 `.blend` 缓存发布；Handler 的任务构建方法
  成为薄转发，兼容的同步 `ensure_model_blend_file` 只取得工具能力并调用基础设施服务。合成与
  既有路由回归覆盖基础、单动画、批量诊断、全失败短路、取消和产物登记。下一阶段审计模型
  路由中剩余的结果组装与文件发送边界，再转向其他大型 Handler 领域。
- 普通/Avatar 构建选择、依赖来源协调、模型状态分类、GLB/Blender/动画候选/单动画公开 URL、
  run 摘要裁剪和模型任务阶段映射已移入 `model_preview_service.py`。Blender 能力通过注入快照
  决定，不在领域结果中散落工具路径判断；任务路径在发布成功前仍强制生成基础 GLB 并检查取消。
  Handler 的 `build_model_preview_result` 与 `build_model_task_result` 均成为薄转发。合成与既有任务
  回归覆盖普通/Avatar、三类模型状态、LOD URL、动画参数、工具缺失、进度和 GLB 门禁。模型主
  构建、动画、GLB、Blender 与预览结果的应用层至此均已拆出，下一阶段转向模型文件响应或其他
  Handler 大型领域。
- ModelDocument 的 geometry/texture 请求身份解析、record/asset/LOD 校验、活动或显式不可变 run
  定位、纹理 URL 解码和目录边界已移入 `model_artifact_resolver.py`。缺失纹理 run 与缺失文件仍
  保持既有可区分 404 文案；Handler 只负责 HTTP 错误映射和流式发送。合成回归覆盖活动/显式 run、
  geometry、编码后的纹理子路径、路径逃逸、无效 LOD 和未完成 run；既有回归继续证明请求旧 run
  不会漂移到当前指针。模型缓存结构至此不再散落于 buffer/texture 路由，下一阶段可转向单动画
  结果服务或模型候选搜索边界。
- 单动画请求的模型准备、普通/Avatar 分流、三阶段进度、取消检查、AnimationClip 精确导出与
  绑定，以及 Dialog Morph 分支已移入 `model_single_animation_service.py`。Handler 的
  `build_model_animation_result` 成为薄转发；服务通过注入接口复用现有 skeletal morph builder，
  没有重写或猜测 Morph 规则。合成与任务回归覆盖普通 clip 的 worker 取消传播、绑定参数、完整
  进度和 Morph 不调用 clip exporter。额外执行既有 skeletal morph 测试仍有仓库已记录的“期望
  1 条、实际 2 条轨道”断言失败，本次未修改相关代码或 fixture，不作为此重构放行门禁。下一阶段
  抽取动画候选搜索和 URL 组装。
- 动画候选的默认查询、显式查询、分页、Avatar LOD 校验及单动画/Blender 稳定 URL 组装已移入
  `model_animation_catalog_service.py`。Handler 只保留模型来源解析和 HTTP 错误映射；合成回归覆盖
  普通模型不携带 LOD、Avatar 携带合法 LOD、查询裁剪、分页传递、无效 LOD 提前失败和两类缓存
  版本。模型 HTTP 应用层拆分至此形成完整边界，下一阶段转向其他大型 Handler 领域。
- Wwise 的 Events/Banks/Media 虚拟目录、路径标准化、分页、虚拟文件字段及 event/bank/media
  预览文档已移入 `wwise_catalog_service.py`。Handler 的 list/preview 路由成为薄适配；真实 SQLite
  接口回归覆盖三类目录、分页边界、Event 关系媒体 URL 和 Media 下载 URL。既有测试夹具同时补上
  主索引 `current` 门禁，确保测试请求确实进入生产路由。下一阶段抽取 Wwise 媒体物理读取边界。
- Wwise Media 的参数校验、精确索引条目读取、`AudioEntry` 映射、VFS PCK fallback 来源解析及
  WEM/WAV 缓存产物协调已移入 `wwise_media_service.py`。领域结果明确携带产物、格式和下载模式，
  Handler 只保留错误映射、响应头与流式发送。合成回归覆盖完整 Bank 内媒体字段、大小写格式、
  下载模式、无效格式、缺失条目/来源和转换失败边界；下一阶段可对称收口 AudioDialog 媒体路径。
- AudioDialog 的目录分页、条目查询、重复路径 `dialogKey` 消歧、可播放状态、预览 URL、物理媒体
  映射、VFS PCK fallback 和 WEM/WAV 产物协调已统一移入 `audio_dialog_service.py`。missing、
  ambiguous 与 collision 状态仍严格禁止隐式选取；Handler 只保留错误映射和文件响应。真实 SQLite
  API 与服务回归覆盖分页、matched/missing、重复路径 409、Bank 内媒体字段和缺失 PCK 来源。
- 真实 AudioDialog 下载审计发现当前二级索引的 `pck_file_id=832796` 在重建后的主 VFS 索引中已
  指向 371 字节的非 PCK 文件，而旧媒体范围为 714,387,339；此前只会晚至范围读取时报错。共享
  音频来源门禁现会验证 `.pck` 文件身份及 external/Bank 读取范围，陈旧 AudioDialog/Wwise 引用
  统一返回 503 并要求重建二级索引。合成回归覆盖 ID 复用和范围越界。后续仍须扩展二级 schema，
  保存稳定 PCK 逻辑身份与主索引内容 identity，并把审计和原子重建接到服务启动流程。
- Wwise schema 2 原本已经保存 `wwise_packages.logical_path` 与 `file_size`，因此无需升级 schema 即可
  先消除纯数字 ID 重排：媒体读取现按该精确逻辑路径在当前主索引重定位 PCK，再复核旧 `file_size`
  与 external/Bank 范围。真实陈旧样本的旧 ID `832796` 可据路径精确迁移到当前 ID `839264`；没有
  路径的旧条目才回退 ID 且继续受门禁保护。后续 Wwise 仍需补内容摘要及启动审计，AudioDialog
  则需先扩 schema 保存同等稳定路径。
- AudioDialog schema 已从 1 兼容迁移到 2；`audio_media` 新增可空的 `pck_logical_path` 与
  `pck_file_size`。当前 `audio_package_service.py` 生成的 metadata identity 同步加入 `logicalId`
  和 `fileName`，离线构建器可把稳定路径与长度写入每个物理媒体证据。schema 1 旧行迁移时保持
  两字段为空，不伪造来源；新行运行时与 Wwise 一样按精确逻辑路径重定位并校验长度/范围。合成
  回归覆盖 schema 迁移、旧 metadata 兼容、新 identity 适配和稳定来源传递。
- 二级音频索引启动审计已进入 `secondary_audio_freshness.py` 和 `/api/health`。审计分别读取
  AudioDialog 的 distinct package identity 与 Wwise package 表，按当前 VFS `logical_id` 定位可读
  chunk 并比较长度；任一 stale/unavailable 会使健康状态降级。所有 SQLite 连接均显式关闭，避免
  Windows 上审计后锁住数据库、阻塞原子替换。当前真实审计确认 AudioDialog schema 2 为 current；
  Wwise 15 个包中 10 个 current，5 个 Hotfix 包长度变化，整体准确报告 stale。下一阶段对 stale
  Wwise 执行原子重建，并把这一步接到启动编排而非只报告。
- 已用当前主索引把 Wwise 原子重建到临时库并通过 `integrity_check` 与二级审计后切换；旧库保留为
  `data/wwise-index.pre-audit-backup.sqlite`。新库覆盖 20 个可读 PCK、20,917 Banks、117,556 Media、
  327,465 HIRC 对象，20/20 稳定路径和长度均为 current。服务重启后的 `/api/health` 为 ready，
  AudioDialog/Wwise 均为 current；随机 Media WEM 仍返回 200/8,299 字节。AudioDialog 旧 schema 1
  库同样保留为 `data/audio-dialog-index.schema1-backup.sqlite`。下一阶段把已验证的“临时构建→完整性
  与 freshness 门禁→原子替换→保留旧库”流程实现为启动时自动重建服务。
- Wwise 启动自动重建已实现于 `secondary_audio_rebuild.py` 并接入 `server.py`：stale 时在活动库同目录
  启动无窗口子进程构建候选库，依次执行 SQLite `integrity_check`、稳定路径/长度 freshness 与非空包
  集合门禁，全部通过才保留 `wwise-index.previous.sqlite` 并原子替换。构建失败、候选损坏或仍陈旧
  都不会覆盖活动库，健康文档的 `secondaryAudioRebuild` 暴露 notNeeded/rebuilt/failed 状态；
  `--no-auto-rebuild` 同时禁用主索引与二级索引修复。合成测试覆盖成功发布、构建失败和 freshness
  失败三条路径。AudioDialog 因还需要准备 TableCfg 与 PCK metadata 输入，下一阶段复用此发布门禁，
  不通过依赖偶然存在的本地中间文件实现假自动化。
- AudioDialog 启动自动重建现已完整接入。schema 3 在稳定 PCK 身份之外保存 effective
  `AudioDialog.bytes` 的逻辑路径、长度与内容 MD5，解决“PCK 未变但逻辑表已更新”仍误报 current
  的缺口。构建器直接消费 VFS 发现结果、SparkBuffer 解码和 `AudioPackageIndexService`，不依赖
  手工导出的 JSON；每种语言必须同时存在可读 banks 与 stream，孤立 hotfix 不会被猜成完整安装。
  真实启动把 schema 2 的 28,433 条中文旧库自动升级为 schema 3，发布中文、日文各 29,072 条，
  分别匹配 26,792/26,769 条，旧库保留为 `audio-dialog-index.previous.sqlite`。最终健康状态 ready，
  AudioDialog TableCfg 内容身份、7/7 构建输入 PCK 与 2/2 实际承载命中媒体的 PCK 均为 current；
  51 项音频与服务回归
  通过。英文、韩文因主体包未安装而严格跳过。
- Wwise schema 3 为 `wwise_packages` 增加 `file_data_md5`，完整构建器从主 VFS 证据直接传递内容
  身份，单包离线工具则以流式 MD5 计算避免把 GB 级 PCK 整体载入内存。启动审计与候选发布门禁
  现同时比较稳定逻辑路径、长度和内容 MD5，同尺寸内容替换也会准确 stale；schema 2 会要求原子
  重建，不进行缺少内容证据的原地伪迁移。
  Wwise 媒体读取门禁也消费该 MD5，覆盖服务启动后的同尺寸来源变化；合成回归确认在派生缓存
  构建前即拒绝陈旧内容身份。
- 二级音频启动编排已从 `server.main()` 抽到 `secondary_audio_startup.py`。协调器拥有初始审计、
  AudioDialog/Wwise 独立修复、每次成功发布后的重审计及 rebuilt/failed/notNeeded 合成；其中一个
  索引失败不会阻断另一个。Handler/启动入口只注入正式服务与日志出口，合成测试覆盖无需重建、
  双重建、单项失败继续推进和 `--no-auto-rebuild` 四条路径，继续缩小 P4 单体边界。
- 普通 `/api/list` 的目录 SQL 与响应组装已抽到 `vfs_directory_service.py`：服务统一当前目录、
  子目录、分页文件批量回填和 manifest 虚拟目录占位，ManifestIndex 资源数通过依赖注入获取；
  Handler 只保留查询参数与虚拟路径分派。合成测试覆盖第二页批量回填、虚拟目录统计和 404，
  真实重启前后根目录 22 项、Manifest 331,714 项及物理文件分页完全一致。
- 共享文件预览分类已抽到 `file_preview_service.py`，普通 VFS、Manifest 导出文件及容器内部产物
  统一使用媒体 Content-Type、UTF-8/GB18030 探测、文本控制字符门禁、截断与十六进制格式；
  TableCfg/MemoryPack 领域分支保持在其前。路径预览改为只读取上限，不再用 `read_bytes()` 先载入
  整个文件。32 项相关回归通过，真实 Lua 与二进制 `.bytes` 的 kind、编码、截断、正文/hex 长度
  和提示在重启前后完全一致。
- 普通 VFS 预览高层已进一步抽到 `vfs_file_preview_service.py`：容器提示、TableCfg 解析与 hex
  降级、文本 JSON、MemoryPack 成功文档及 binary JSON 证据统一由应用服务组装，物理读取和领域
  解码通过回调注入。`handle_preview` 从 148 行缩到文件 ID/来源定位与一次发送；真实 Lua、普通
  `.bytes` 和 4.7 MiB AudioDialog TableCfg 的 kind、编码、截断、正文/hex 长度、根名及转换 URL
  在重启前后完全一致。
- `/api/internal/list` 的 AB/PCK/USM 分支与响应组装已抽到 `internal_directory_service.py`；各领域
  导出/索引服务、内部类型分类和可选工具诊断均通过依赖传入，不在新服务中解析二进制或寻找工具。
  合成回归覆盖三类容器、AssetBundle 已报告失败和普通文件边界；真实中文 stream PCK 重启前后
  保持 30,058 条、`wem,wav` 根目录与 WAV 可用状态一致。
- BundleManifest 虚拟目录响应已抽到 `manifest_virtual_directory_service.py`：服务消费已解析的
  ManifestIndex，统一目录统计、文件分页以及 preview/model/avatar-plan 链接；Handler 只定位
  `manifest.hgmmap`、解析物理来源并映射 HTTP 错误。合成回归锁定根目录中文名、深层目录路径、
  prefab 模型入口和 AvatarMesh 的 plan/lod=0 特例；真实重启后根目录 242,222 个 bundle / 331,714
  个 asset，以及 projectile 深层目录 407 个资源和稳定预览 URL 均保持一致。
- `/api/search` 的 SQL 与响应组装已抽到 `vfs_search_service.py`，scope、路径匹配、稳定排序和文件
  元数据在应用服务中完成；`%`、`_` 与反斜杠保持字面量语义，避免搜索词意外扩大为 SQL 通配。
  Handler 只解析并约束 `scope/q/limit`。
- Manifest 资源预览文档已抽到 `manifest_asset_preview_service.py`：普通导出文件仍复用统一文本/媒体/
  十六进制分类，Cubemap 则统一六面顺序、PositiveZ 默认预览、总尺寸和逐面 raw/download URL。
  Handler 只负责定位资源、触发导出和映射失败，应用服务不读取 VFS 或调用 Worker。真实重启后
  `t_sky_cube_004.exr` 的 6 面、5,680,951 字节和默认面 URL，以及 MonoBehaviourDump 的 kind、
  1,486 字节、正文长度和链接均与迁移前一致。
- AB/PCK/USM 内部文件预览组装已抽到 `internal_file_preview_service.py`：服务消费已通过领域解析与
  路径安全检查的目标文件，统一共享预览分类、asset/audioEntry 元数据和完整内部路径 URL 编码；
  Handler 保留容器分派、导出和错误映射。真实中文 stream PCK 的 `1060201.wem` 重启后仍为
  17,843 字节 hex 预览，audio ID 与 raw/download URL 完全一致。
- `/api/raw` 与 `/api/internal/raw` 的响应描述和分块来源已抽到 `raw_file_service.py`。普通 VFS
  记录保持 offset/length 有界流式读取，不会为下载载入完整 chunk；加密记录仍经正式整段解密后
  输出，并对伪 JSON/文本 MIME 降级。Handler 只发送状态/响应头并迭代数据块。真实重启验证
  `RootConfig.lua` 仍为 44 字节 UTF-8 文本，中文 PCK 的 `1060201.wem` 仍为 17,843 字节
  `audio/x-wem`；两者 inline/attachment 文件名均保持一致。
- 本地派生产物的手写分块循环也已统一到 `raw_file_service.py`：覆盖任务 artifact、AudioDialog、
  Wwise、Manifest 普通/Cubemap raw、GLB、Blend、模型 geometry 和 texture。响应描述显式支持 MIME、
  下载名、无 Content-Disposition、历史未编码 filename，以及 Cache-Control/动画跳过数等额外头；
  Handler 中不再存在重复的 `STREAM_CHUNK_SIZE` 文件循环。真实 Wwise ordinal 40 的 WEM/WAV
  重启后仍为 8,299/81,174 字节，且 `filename=4125696.wem/.wav` 历史响应头保持不变。
- TableCfg JSON 与 Web 静态文件两个剩余响应旁路也已接入 `raw_file_service.py`。前者保留领域解析后
  的内存字节但按统一块大小发送，后者改为路径流，不再 `read_bytes()` 整体载入。Handler 中直接
  `wfile.write` 现在仅存在于 JSON API 序列化和统一 raw sender 两个传输出口。真实重启后
  AudioDialog TableCfg 仍为 14,671,090 字节及原文件名，首页仍为 3,626 字节 `text/html`、
  `no-cache` 且无 Content-Disposition。
- AvatarMesh resource-plan 的最终文档已抽到 `avatar_resource_plan_service.py`：服务统一配置摘要、
  完整资源闭包以及 dump/StringPathHash 的最小公开 run 身份，不触发 Worker 或解析 Bundle。
  现已进一步统一 AvatarMesh 类型门禁、TypeTree dump、配置解析、StringPathHash 路径附着、资源计划
  构建与公开文档组装；网页 resource-plan 与 `AvatarModelBuildService` 均注入并复用同一个 `load`
  入口，直接 bundle 与 Manifest 依赖的去重排序闭包也由该服务统一提供，不再由 Handler 保存共享
  加载/依赖流水线。Handler 仅保留来源定位、LOD 查询参数、HTTP 错误映射
  和发送。非 AvatarMesh 仍由明确领域错误映射为 HTTP 400，不与计划内部的解析/资源错误混淆。
  真实 Adaxier LOD0 重启后
  仍为 1 个 slot、各 LOD `9/9/7/7` 个 mesh、32 个引用、0 个未解析引用，plan/run 字段完全一致。
- 同步 `/api/manifest-asset/model-blend` 与后台 Blend 任务现共享 `ModelBlendService.prepare_bundle`：
  基础/动画 GLB 选择、批量兼容问题、全部动画失败判定、prepare/failure 文档和 artifact 文件名
  不再在 Handler 重复。`prepare=1` 只构建 bundle 而不启动 Blender，实际下载才调用 artifact 阶段。
  真实 Adaxier LOD0 prepare 文档逐字段一致，下载仍生成 42,553,475 字节 Blend、原文件名、0 个
  跳过动画及 private/max-age 缓存头。
- 同步 GLB/Blend 下载的公开文件响应已抽到 `model_file_response_service.py`：服务统一 MIME、从资源
  路径派生的 GLB 下载名、Blend artifact 下载名、private/max-age 缓存头与跳过动画数诊断头，
  Handler 只把领域产物交给统一 raw sender。合成回归覆盖两类文件的响应头与分块正文，Python
  门禁增至 592 项。
- `/api/health` 的公开文档已抽到 `health_service.py`：Unity worker 必需能力、主索引、Manifest 与
  二级音频门禁共同决定 ready/degraded，可选工具和缓存版本只形成诊断；Handler 继续只返回
  `no-store` JSON。既有健康回归覆盖全部降级分支和可选工具不阻塞语义。
- MemoryPack 解码值到普通文件预览的格式转换已抽到 `memorypack_preview_service.py`：服务统一
  `__meta`、完整消费标志、union tag 字符串化与稳定排序、Unicode JSON 和正文截断，并将领域
  解码错误翻译为现有 binary JSON 回退所消费的错误。Handler 不再拼装 MemoryPack 预览；Python
  门禁增至 594 项。
- MemoryPack 成功预览现提供 `/api/memorypack/json` 完整打开/下载入口；完整文档与 2 MiB 截断预览
  共享同一份 UTF-8 `__meta + value` 序列化结果，缺少根 schema 或领域解码失败均返回可区分的 422，
  不会把 binary JSON 原文冒充解码结果。路由、预览链接、响应正文和两类错误均有回归，Python
  门禁增至 599 项。
- 网页普通文件预览现为 `memorypack-json` 显示独立的 `MemoryPack → JSON` 徽标、根类型和
  consumed/bytes 完整消费摘要；SparkBuffer 转换使用同一视觉语言但保持独立颜色。真实弭弗
  SkillData 在原有预览面板宽度内自然换行，完整结果操作和正文空间未受影响。
- TableCfg 完整 JSON 导出也已收口进 `tablecfg_service.py`：服务在严格 file-ID 来源之上统一
  SparkBuffer 解析、JSON 字节、配置根名文件名和格式错误翻译；Handler 不再导入或解释
  SparkBuffer/`struct` 异常，只映射服务错误并发送统一 raw response。合成回归覆盖服务导出、
  格式失败和下载响应，Python 门禁增至 602 项。
- P4 服务端产品化门禁已完成。最终 AST 审计确认 51 个 HTTP 请求入口不直接调用 VFS 解密、
  SparkBuffer/MemoryPack reader 或二进制 unpack，`server.py` 也不直接启动 subprocess；格式、
  外部工具、缓存、任务和领域构建均经显式服务/适配器。任务注册表既有回归继续证明结果先写
  独占临时文件，再原子发布 `result.json` 与 succeeded 状态，取消/失败不会留下成功指针。
  两条架构边界已成为自动化测试，Python 门禁增至 604 项。
- P3 生产调用迁移已按真实消费者边界完成。根入口、运行时配置和唯一 worker 适配器均由回归
  禁止重新出现 `AnimeStudio.CLI`、旧环境变量或 `data/research/AnimeStudio` 探测；AudioClip
  继续返回结构化 unsupported 诊断，Shader 二进制包/反编译保持离线研究线，二者都没有生产调用者，
  不为清空清单增加虚假协议。Python 门禁增至 605 项。
