# VFS Unity Worker

该目录保存 VFS 产品内置的 Unity/AssetBundle 读取 worker。它是内部实现，不是
AnimeStudio CLI 的兼容层；Python 服务只能通过这里定义的版本化协议调用 Unity 能力。

当前已实现 `handshake`、`exportMonoBehaviourRaw`、
`exportMonoBehaviourTypeTreeDump`、`decodeProjectileComponent`、`buildAssetMap` 和
`buildCabMap`、`exportObjectSnapshots`。
能力列表只声明已经接入并通过契约测试及真实样本验证的操作，不得为了兼容旧调用而提前
声明尚未实现的能力。

## 构建与验证

```powershell
./Initialize.ps1
dotnet build Vfs.UnityWorker.slnx -c Release
dotnet test Vfs.UnityWorker.slnx -c Release --no-build
dotnet run --project src/Vfs.UnityWorker -c Release --no-build -- handshake
dotnet run --project src/Vfs.UnityWorker -c Release --no-build -- request request.json
```

SDK 由本目录的 `global.json` 锁定。构建必须从本仓库完成，不得引用相邻 AnimeStudio
源码目录或 `data/research` 下的忽略文件。

初始化会按 `dependencies.lock.json` 下载并校验明确开源的第三方源码，保存在被 Git 忽略的
`.deps/`。服务运行时不会隐式联网。VFS 和定制 AnimeStudio 源码仍随仓库保存；发布过程
负责把运行所需二进制及许可证装入发布包。

## 当前边界

- 协议所有权：VFS；
- 进程边界：单次 worker 进程，后续按性能证据决定是否改为常驻；
- 权威 AnimeStudio 来源提交：`8cdec963c4e187ea0a4a339b8969844a9574638b`；
- worker 版本：`0.6.0`；
- 已实现能力：`handshake`、`exportMonoBehaviourRaw`、`exportMonoBehaviourTypeTreeDump`、
  `decodeProjectileComponent`、`buildAssetMap`、`buildCabMap`、`exportObjectSnapshots`；
- Raw 导出要求本次请求独占的空输出目录，成功响应返回 PathID、container、字节长度和
  SHA-256，不会覆盖旧模拟/导出结果；
- TypeTree Dump 只读取对象内嵌 TypeTree，并同时返回 `serializedByteCount`、
  `consumedByteCount` 和 `complete`；不完整读取仍可作为诊断文本使用，但不会被标记为完整；
- `ManagedReferenceRegistryScanner` 已从领域解码中独立出来，只负责从 Raw 中恢复 registry
  及 payload 边界。常规测试使用合成 registry；设置 `VFS_WORKER_PROJECTILE_RAW_FIXTURE`
  可启用不随仓库分发的真实资源边界回归；
- `ManagedReferencePayloadReader` 提供严格有界的基础类型、bool32、ASCII/UTF-8 对齐字符串
  读取，并要求每次读取携带字段路径；领域解码器不能绕过它直接游走 Raw 字节；
- `ProjectileComponentPrefixDecoder` 已迁移并验证从组件起点到 `moveSegments` 结尾的固定
  前缀；它显式返回尚未消费的 tail 偏移和长度，不会把阶段性结果标记为完整组件；
- `ProjectileMoveModeDictionaryDecoder` 已恢复字典边界与每个 124 word 记录的已知前缀，
  其余 115 word 原样保留；`ProjectileMainEffectFinishDecoder` 支持“枚举 + 距离”和仅距离
  两种证据已知形态；
- `decodeProjectileComponent` 要求精确 container 和 `expectedProjectileId`，只在唯一组件且
  ID 相等时输出聚焦 JSON。未理解的特效/声音尾部完整保存为 Raw words，结果明确为
  `decodeStatus: partial`；
- `buildAssetMap` 只负责单个 Bundle 的对象索引，不生成跨 Bundle CABMap。请求必须显式给出
  输出类型和稳定 `sourceLabel`；临时输入文件的绝对路径不会进入 `AssetEntries[].Source`；
- `buildCabMap` 接收显式 `{ inputId, inputPath }` 多输入列表，输出 CAB 名、稳定输入 ID、
  SerializedFile 偏移与外部 CAB 依赖。重复输入或 CAB 名碰撞会明确失败，不再静默选中首项；
- `exportObjectSnapshots` 在单次请求中显式接收物理输入闭包、CABMap、主输入、允许选择的
  input ID、类型与精确 container。它验证 CAB 名、input ID 和 SerializedFile 偏移一致后才
  导出 `sourceFile + pathId` 身份的快照；公共 JSON 只附加稳定 input ID，不保存物理路径；
- 旧 CLI 的通用 `JSON` 只序列化 MonoBehaviour 外壳，不是 managed-reference 领域解码。
  Projectile 的聚焦解码器主体存在于被忽略的旧研究副本提交 `03336c4`，其上另有 85 行
  未提交修正；当前已迁移可由三份真实样本验证的结构，并保留未理解尾部。对象快照 worker
  已能直接消费 CABMap，`server.py` 的模型和 AvatarMesh 对象阶段也已切换；旧 `UseCABMap`
  只剩 IdentifiedTexture 等尚未迁移的转换入口，因此仍不能宣称它已无调用者。

真实样本边界回归示例：

```powershell
$env:VFS_WORKER_PROJECTILE_RAW_FIXTURE = "D:\evidence\projectile.dat"
dotnet test Vfs.UnityWorker.slnx -c Release --filter TestCategory=LocalEvidence
```

AssetMap/CABMap 的本地真实 Bundle 审计另使用
`VFS_WORKER_ASSET_MAP_FIXTURE`；测试验证资源条目非空、AssetMap 的 `Source` 为稳定逻辑标识，
且 CABMap 不包含物理输入路径。

对象快照模型闭包审计使用 `VFS_WORKER_OBJECT_SNAPSHOT_FIXTURE_ROOT`，目录中应包含旧模型缓存的
`inputs/entry.ab`、依赖 Bundle 和 `objects/`。测试核对旧快照身份是新快照的子集、共同对象的
container 完全一致，并检查所有物理输入路径都未写入新 JSON。

完整阶段计划见 [VFS 产品化与 AnimeStudio 内嵌计划](../docs/design/vfs-productization.md)。
