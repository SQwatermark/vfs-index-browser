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

1. 原始/TypeTree MonoBehaviour、Projectile、AbilityEntity；
2. AssetMap、CABMap、ObjectJSON 和通用 AssetBundle 导出；
3. Texture2D、Sprite、Cubemap、模型层级和 AvatarMesh；
4. AnimationClip、Humanoid、ACL；
5. Shader 二进制包、程序映射和反汇编输入。

完成门禁：旧入口已无生产调用者，代表性真实样本和合成错误样本全部通过等价测试。

### P4：服务端产品化

- [ ] 将单体 `server.py` 拆成请求层、应用服务、任务系统、缓存和基础设施适配器。
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
- [ ] 完成安装、升级、故障诊断、开发和发布文档。
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

当前阶段：**P3 生产调用迁移**。P0 已完成，P1 的首个领域能力已经形成可调用闭环。

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

截至 2026-08-29，当前工作树的可交付边界为：

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

1. AudioClip 出现真实样本后再设计协议，不为清空列表引入 FMOD，且禁止退回任意类型
   `Convert`；
2. 继续移除发布配置和文档中残留的旧 CLI 假设，生产服务已无旧 CLI 调用点；
3. 将持续增长的 `server.py` 按请求、资源服务和后台任务边界逐步拆分，拆分过程中保持当前
   同步兼容入口和任务协议不变；
4. 为 LODGroup 增加普通角色、NPC 和怪物多样本审计；资源没有完整 TypeTree 时必须明确失败，
   不得重新引入只有对象外壳的伪快照。

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

本轮验证：.NET `36` 项通过、`4` 项未配置的外部证据测试跳过，其中对象与纹理真实闭包测试已
显式启用并通过；Python worker、健康检查、模型 run、AvatarMesh 与 ModelDocument 聚焦测试 `35` 项
通过；模型生产路径另以临时缓存完成端到端验证。全量 Python discovery 的 267 项仍有 3 失败、
5 错误，集中在动画版本旧 fixture、两项已知 MemoryPack override、runtime probe 导入、Humanoid
oracle 与 skeletal morph 既有断言，不属于本次对象迁移的放行结果。Release 构建 0 警告、0 错误，产品自有文件的
`git diff --check` 通过。锁定导入的上游 vendor 保留其原始尾随空白，不以格式化改写破坏
来源比对。真实 fixture 位于被忽略的研究目录，不进入提交。

## 变更记录

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
  Worker 构建、ModelDocument 组装与发布写入仍是下一阶段拆分边界，P4 总门禁保持未完成。
