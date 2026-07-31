# 资源恢复整合路线

本文用于统一当前分散在资源浏览、角色模型、NPC、材质 Shader、动画和音频等方向上的
研究与实现。它回答三个问题：

1. 当前已经具备哪些可靠能力；
2. 哪些任务存在严格依赖，哪些可以并行；
3. 下一阶段以什么稳定接口组织代码和产物。

## 总体目标

项目分为四层：

```text
本地游戏文件
  -> 定位与解密
  -> 语义恢复与装配
  -> 稳定领域文档
  -> 网页预览 / GLB / Blender / API / 下载
```

上层消费者不得重新解析 VFS、AssetBundle 或 Wwise 私有结构。预览格式也不得反过来
成为源数据格式。

模型对外只使用一种完整格式：`EndfieldModelDocument`。可操控角色 Prefab、PostModel、
通用 NPC AvatarMesh、怪物和未来的其他组合模型都应通过输入适配器进入同一个装配与
构建流程。GLB、`.blend` 和网页场景只是派生产物。

音频不适合塞进 ModelDocument。它使用独立的逻辑音频索引，但遵循相同原则：逻辑身份、
物理位置和派生预览分层保存，WAV 只按需生成。

## 当前能力

| 领域 | 当前状态 | 可信边界 |
| --- | --- | --- |
| VFS | 可按逻辑路径定位、解密和读取单文件 | 已由索引、offset、length 和 IV 验证 |
| Bundle manifest | 可把 `manifest.hgmmap` 展开为逻辑资源树 | 已恢复 Bundle、依赖和 AssetInfo |
| AssetBundle | 可按 container 和 PathID 按需导出 | 不需要预扫描全部 `.ab` |
| TableCfg | 可读取 SparkBuffer 配置 | 未知类型仍应显式报错 |
| JsonData | 已分类文本 JSON、MemoryPack 等格式 | MemoryPack 仍依赖版本对应的 schema |
| Prefab 角色 | 可恢复层级、网格、骨架、蒙皮、材质和纹理 | 已进入 ModelDocument、GLB 和 Blender |
| 通用 NPC | AvatarMesh 已接入统一 ModelDocument、GLB 与网页预览 | Andrew LOD0 已验证完整着色模型；仍需更多外观样本 |
| 可操控角色 AvatarMesh | Pelica、Deepfin 的资源计划均完整命中 | 与 NPC 共用在线导出管线，尚需逐个验证外观和材质 |
| 动画 | ACL Transform 曲线可独立导出并绑定稳定节点 ID | 浮点曲线、Root Motion、面部运行时驱动未完成 |
| 材质 | 原始 TexEnv/Int/Float/Color 已保存在 ModelDocument | `previewPbr` 仍混有部分过早近似 |
| Shader | 已定位面部、身体和丝袜关键变体及公式边界 | 完整 CharacterVolume 和运行时光照尚未恢复 |
| PCK 音频 | 可索引 WEM，按需解密、转 WAV、预览和下载 | 当前主要按数字 Media ID 浏览 |
| 对话语音 | 已验证 AudioDialog 逻辑路径到 WEM ID 的哈希映射，并接入逻辑目录、预览与下载 | 尚未自动发现本地 AudioDialog 与语言 PCK |
| 其他音频 | 可从 SoundBank 的 HIRC/DIDX 建立部分关系 | 缺少完整作者命名，不能强行重命名 |

## 统一模型管线

### 对外格式

唯一完整模型产物为 `EndfieldModelDocument`，包含：

- 节点层级与变换；
- 网格、顶点属性和材质槽；
- 骨架、蒙皮和 BindPose；
- 原始材质与纹理引用；
- 动画或独立动画对稳定节点的引用；
- 来源、依赖和结构化诊断。

GLB 只选择网页预览需要的 LOD、材质和纹理。Blender 文件从 GLB 与
ModelDocument 语义派生，不是新的事实来源。

### 内部装配计划

不同模型来源先解析为内部的 `ResolvedModelAssembly`。它是构建过程的数据契约，
不是第二种公开模型格式。其职责是记录：

- 资产身份与入口来源；
- 选中的部件、变体、槽位和 LOD；
- 每个 Renderer 的 Mesh 和有序材质槽；
- 骨架、Avatar、根节点和坐标约定；
- 依赖资源的逻辑路径、Bundle、PathID 或路径哈希；
- 解析证据、回退项、缺失引用和歧义诊断。

Prefab 适配器从 Unity 对象引用图生成装配计划；AvatarMesh 适配器从配置、路径哈希和
选择规则生成同样的装配计划。后续共享对象图构建、几何编码、材质提取、纹理收集、
ModelDocument 校验和派生导出。

### 通用 NPC 拼装

通用 NPC 不能按“一个配置等于一个完整模型文件”处理。建议拆成四步：

1. **配置解析**：读取 AvatarMesh 的部件槽、候选 Mesh、材质列表、LOD 和骨架来源。
2. **选择解析**：根据 NPC 实例、外观变体或调用参数确定每个槽实际选择的部件。
3. **引用解析**：将 StringPathHash、逻辑路径和 Unity 引用解析到具体资源。
4. **装配验证**：检查材质槽数量、骨骼名、BindPose、根变换和 LOD 是否兼容，再生成
   `ResolvedModelAssembly`。

引用解析已由 `npc_avatar_resources.py` 建立严格资源计划：它不打开 Bundle，先用 manifest
唯一确定 Mesh、按槽位排序的 Material、Avatar 和直接 Bundle。在线适配器随后展开传递
依赖闭包，并以逻辑资源路径和 AnimeStudio `container` 精确选择对象，再送入统一模型
构建器。Andrew LOD0 已完成 ModelDocument、GLB、纹理和 Three.js 真实验证；Deepfin 与
Pelica 仍需在同一管线上完成外观核对。

未知选择规则不得静默取第一个候选。工具可以允许显式指定部件以便研究，但必须把该选择
标记为调用方输入，而非游戏默认值。

### 材质数据分层

材质必须分成三层：

1. `sourceMaterial`：Shader 身份、关键字或开关、全部原始属性、纹理采样与颜色空间；
2. `previewPbr`：用于通用 glTF 和快速网页预览的有损近似；
3. `blenderMaterialBackend`：选择 `preview`、`formula-validation` 或
   `cycles-reference` 后端所需的显式配置。

ModelDocument `2.0.0` 已将源 Material 明确保存为 `materials[].sourceMaterial`；
`materials[].previewPbr` 只保存有损预览映射。GLB 分别以
`extras.endfieldSourceMaterial` 和 `extras.endfieldPreview` 传递两层数据。丝袜的
`_SilkStockingsMaxAffect` 不再提前写回 `baseColorFactor`，因为真实影响随视角和覆盖度
逐片元变化；Blender 专用预览会从源材质读取该参数并只应用一次。

## 音频管线

对话语音和其他 Wwise 音频采用不同的语义入口：

- `AudioDialog` 提供可靠的逻辑路径和语言身份，可以构建稳定虚拟目录；
- SFX、音乐和环境音缺少完整作者文件名，应保留 Media ID，并附加 Bank、Event、
  Action、容器和分类关系。

详细接口和虚拟目录设计见 [audio-index.md](audio-index.md)。

## 优先级

### P0：冻结稳定契约

这是所有后续工作的共同前置条件。

1. 明确 ModelDocument 是唯一完整模型格式。（已完成）
2. 定义装配契约，让 Prefab 与 AvatarMesh 共享后续流程。（已完成基础结构）
3. 明确 `sourceMaterial`、`previewPbr` 和 Blender 后端的边界。（已完成基础结构）
4. 补齐原始纹理采样、颜色空间、Shader 开关和材质诊断。
5. 将 AudioDialog 逻辑路径、Media ID 和 PCK 物理条目建成独立索引契约。（已完成）
6. 对不兼容的 ModelDocument 或缓存结构提升版本，禁止旧缓存伪装成新产物。（已提升至 `2.0.0`）

验收标准：

- 同一个导出器能消费 Prefab 和 AvatarMesh 产生的 ModelDocument；
- 精确材质后端不读取预烘 PBR 值作为事实；
- AudioDialog 目录查询不需要解包整个 PCK；
- 未解析引用均产生结构化诊断。

### P1：完成材质源数据与 AudioDialog 目录

这两条线互不依赖，可以并行。

**模型/材质线**

- 用佩丽卡真实材质核对 `_BaseColor`、全部 `_SilkStockings*`、高级 Mask 和纹理采样；
- 移除或隔离丝袜颜色的过早预烘；
- 让 GLB extras 或 Blender 输入可读取完整源材质；
- 为原始属性、预览近似和缓存版本增加回归测试。

**音频线**

- 从本地 TableCfg 读取 AudioDialog；
- 按语言和逻辑路径建立虚拟目录；（已完成）
- 叶节点映射到 PCK 中的 Media ID、offset 和 size；（已完成）
- 点击后按需解密 WEM，按需转码并缓存 WAV；（已完成）
- 显示路径、语言、Media ID、物理来源和匹配状态。（已完成）
- 自动发现本地 AudioDialog TableCfg 与对应语言 PCK。（待完成）

### P2：统一 NPC 与角色装配

1. 抽出 `ResolvedModelAssembly` 和共享构建器。
2. 把现有 Deathgirl 静态拼装从专用导出路径迁入共享管线。
3. 为 Prefab、PostModel 和 AvatarMesh 建立相同的来源与诊断字段。
4. 增加至少一个可操控角色、两个不同结构 NPC 和一个怪物样本。
5. 验证骨架兼容、材质槽顺序、LOD 和可选部件选择。

这一步可与 Shader 数值验证并行，但必须建立在 P0 材质契约之上。

### P3：Shader 数值验证与 Blender 分层后端

1. 将丝袜 b391 和面部 b138 的已确认公式整理为纯函数。
2. 对 HLSL 中间值建立固定输入测试。
3. 保留快速 Eevee `preview` 后端。
4. 新增默认关闭的 `formula-validation` 后端。
5. 在确有需要时建立 Cycles/OSL `cycles-reference` 后端。
6. 继续恢复 CharacterVolume、主光两份颜色载体、附加灯光、阴影和曝光。

动画恢复可以作为独立侧线继续做样本验证，但在材质和统一装配稳定前不应成为主线。

### P4：其他音频语义与高级聚合视图

- 构建 Wwise Bank/Event/Action/Container/Media 图；
- 按可证明的规则分类语音、音乐、环境音和 SFX；
- 对一对多、多对一关系保留全部边，不用“第一个事件名”覆盖 Media ID；
- 再建设角色、任务、场景等跨资源聚合页面。

## 并行安排

| 工作线 | 可以立即开始 | 依赖 | 建议产物 |
| --- | --- | --- | --- |
| A. 模型契约 | 是，主线 | 无 | schema、构建器接口、迁移说明 |
| B. 材质源数据 | 是 | A 的字段边界 | 真实样本、测试、缓存升级 |
| C. AudioDialog | 是 | 仅依赖现有 VFS/PCK | SQLite 索引、虚拟目录和 API |
| D. NPC 装配 | 设计可开始，迁移稍后 | A | ResolvedModelAssembly 适配器 |
| E. Shader 公式 | 纯函数研究可开始 | B 后才能接正式导出 | 数值测试、实验后端 |
| F. 动画 | 可做样本验证 | 稳定节点 ID 已具备 | 独立动画文档和播放器 |
| G. 其他音频 | 可做 HIRC 调研 | C 的音频索引契约 | 语义图和置信度 |

主线人员应处理 A，并审核 B、C 的接口。B 与 C 最适合独立并行；D 在 A 的接口冻结后
进入实现；E、F、G 都不应阻塞 P0/P1。

## 代码与文档归属

| 内容 | 当前入口 | 后续归属 |
| --- | --- | --- |
| VFS/HTTP 编排 | `server.py` | 逐步拆为服务和容器适配器 |
| Prefab 对象图 | `animestudio_model.py` | 模型输入适配器与共享构建器 |
| NPC 拼装 | `npc_avatar_model.py` | AvatarMesh 适配器 |
| ModelDocument | `model_document.py`、`schemas/model-document.schema.json` | 唯一完整模型契约 |
| GLB | `gltf_export.py` | ModelDocument 派生后端 |
| Blender | `tools/blender_import_model.py`、`blender_materials.py` | 分层材质后端 |
| Shader 实验 | `experiments/endfield_blender_shader/` | 公式证据与验证，不直接成为生产源 |
| 音频容器 | `server.py` 的 PCK 相关代码 | 独立 audio package 适配器 |
| 格式研究 | `docs/research/` | 只记录证据、结论与未知项 |
| 设计决策 | `docs/design/` | 稳定接口、数据流和路线 |

## 下一轮执行顺序

1. 写出 `ResolvedModelAssembly` 与材质三层契约的最小 schema 草案。
2. 用现有佩丽卡和 Deathgirl 产物验证草案能否覆盖两类入口。
3. 修正丝袜预烘和完整源材质传递。
4. 同时实现 AudioDialog 索引构建器及只读目录 API。
5. 两条线各自通过真实样本后，再接 UI 与缓存迁移。
