# 组合模型恢复与导出设计

## 目标

模型恢复不是导出单个 `Mesh`，而是从 Prefab 入口恢复场景层级、网格、骨架、蒙皮、材质、纹理和 LOD 关系。解析结果先写入与输出格式无关的 `ModelDocument`，再由独立导出器生成 GLB 或其他交付格式。

当前首个真实样本是佩丽卡展示模型：

```text
assets/beyond/dynamicassets/gameplay/actors/postmodels/characters/chr_0004_pelica_postmodel.prefab
```

## 数据流

```text
manifest.hgmmap
  -> 逻辑路径、入口 Bundle 和传递依赖闭包
  -> AnimeStudio CABMap 与 Unity 对象快照
  -> ModelDocument v16 + geometry.bin + textures/
  -> 自包含 GLB
  -> Three.js 网页预览 / 下载
```

各层职责如下：

1. `manifest_index.py` 定位入口 Bundle，并查询三类 Bundle 依赖的传递闭包。
2. `server.py` 按需暂存相关 AB，驱动 AnimeStudio 导出 CABMap、对象 JSON 和引用纹理。
3. `animestudio_model.py` 根据显式 `sourceFile + pathId` 和 PPtr 恢复组合模型，禁止靠文件名猜引用。
4. `model_document.py` 定义并校验稳定的中间协议，Schema 位于 `schemas/model-document.schema.json`。
5. `gltf_export.py` 从 ModelDocument 选择预览资源并生成自包含 GLB，不重新解释 Unity 对象。
6. 前端通过 Three.js 加载 GLB，普通文件浏览行为不受模型预览入口影响。

## ModelDocument 边界

ModelDocument 保存完整恢复结果，而不是某个预览格式的镜像：

- `nodes`：GameObject/Transform 层级、组件关联和 LOD 标注；
- `meshes`：Primitive、顶点属性、索引和二进制 accessor；
- `skeletons`：骨骼层级；
- `skins`：关节集合、根骨骼和逆绑定矩阵；
- `materials`：Unity Shader 身份、原始 TexEnv/Int/Float/Color 以及单独的预览映射；
- `textures`、`images`：实际引用纹理和导出图片；
- `dependencies`、`source`、`diagnostics`：来源追踪、依赖状态和不能静默丢失的问题。

几何数据使用 `buffers -> bufferViews -> accessors` 引用 `geometry.bin`。该结构借鉴 glTF，但内部 ID、原始 Shader 参数和诊断不受 glTF 表达能力限制。

## GLB 导出策略

GLB 用于浏览器预览和通用工具下载，不取代 ModelDocument：

- 文件自包含几何、蒙皮矩阵和 PNG 纹理；
- 有 `LODGroup` 的 Renderer 只导出 `LOD0`；未归组 Renderer 默认保留，但命名明确的 `shadowProxyDesktop` 只用于阴影代理，不进入可见 GLB；
- 根据选中的 Mesh 反向裁剪 Skin、Accessor、BufferView、Material、Texture 和 Image；
- 二进制 Buffer 在导出时重新紧凑排列，不携带未使用的低 LOD 几何；
- 根节点增加 `scale: [-1, 1, 1]` 的 Unity 到 glTF 坐标系包装节点；
- CharacterNPR 按属性签名划分 `skin`、`hair`、`eye`、`cloth` 和 `overlayShadow`；衣物保留标准 PBR 受光，皮肤、头发、眼睛和覆盖阴影暂以 `KHR_materials_unlit` 保住原始色彩关系；游戏特有 Shader 字段仍完整保存在 ModelDocument 中；
- GLB 材质通过 `extras.endfieldPreview` 携带预览扩展元数据；标准查看器会安全忽略，本站预览器可据此选择 CharacterNPR、丝袜等专用处理，不再依赖材质名猜测；
- 已启用的 Diff Ramp、Spec Ramp、SDF Lightmap、SDF Mask、Shadow LUT、面部高光和丝袜 Mask 会随 GLB 携带，并以稳定纹理 ID 写入 `extras.endfieldPreview`；宿主可以渐进实现专用材质而无需重新解析 Unity 对象；
- 网页预览可选用沿顶点法线外扩、仅绘制背面的轮廓副本；透明覆盖层不参与描边，下载的 GLB 本身仍保持标准且不包含重复轮廓网格；
- `_UseGrayAsAlpha` 覆盖材质在打包时转换为白色 RGB、原 R 通道写入 Alpha 的标准 PNG；
- `_MetallicGlossMap` 的 `R=Metal`、`A=Smoothness` 在打包时转换为 glTF 金属粗糙贴图的 `B=Metallic`、`G=1-Smoothness`；原 `G=Spec`、`B=Shadow` 不强行映射为标准 PBR 语义，仍保留在 ModelDocument；
- HGRP 导出的双通道 DirectX 切线空间法线在打包时翻转 G，并由 RG 重建 Z；不能把 `B=0` 的源图直接交给 glTF Normal Texture；
- `_SilkStockings` 当前只近似基础色压暗，尚未复刻专用 Mask、各向异性高光和干湿响应。

逆绑定矩阵是一个已验证的关键约束：AnimeStudio 的 `Matrix4x4` JSON 采用行向量表示，平移位于 `M30/M31/M32`。写入 glTF 的列主序数组时必须保持 JSON 的行顺序，等价于完成约定转换。测试必须使用非对称矩阵；单位矩阵无法发现行列颠倒。

## HTTP 接口

```text
GET /api/manifest-asset/model?manifestId=<manifest文件ID>&assetIndex=<资源索引>
GET /api/manifest-asset/model-glb?manifestId=<manifest文件ID>&assetIndex=<资源索引>
GET /api/manifest-asset/model?manifestId=<manifest文件ID>&assetIndex=<资源索引>&animationAssetIndex=<动画资源索引>
GET /api/manifest-asset/model-glb?manifestId=<manifest文件ID>&assetIndex=<资源索引>&animationAssetIndex=<动画资源索引>
GET /api/manifest-asset/model-buffer?recordId=<VFS记录ID>&assetIndex=<资源索引>
GET /api/manifest-asset/model-texture?recordId=<VFS记录ID>&assetIndex=<资源索引>&path=<纹理路径>
```

模型 JSON 返回 `glbUrl`。GLB 缓存同时观察 `model.json`、`geometry.bin`、引用纹理和导出器源码的修改时间，避免只改导出规则却继续命中旧文件。
指定 `animationAssetIndex` 时，服务复制基础文档并追加动画，不修改模型快照；
派生 GLB 按动画资源索引单独缓存，并观察动画 JSON 与适配器源码的修改时间。

前端支持直接链接：

```text
/?modelManifestId=<manifest文件ID>&modelAssetIndex=<资源索引>
/?modelManifestId=<manifest文件ID>&modelAssetIndex=<资源索引>&animationAssetIndex=<动画资源索引>
```

## 佩丽卡验证结果

2026-07-30 的真实样本恢复结果：

- 64 个传递依赖 Bundle，均能从 Effective VFS 定位；
- 454 个模型层级节点，51 个 Renderer 节点；
- 44 个去重 Mesh、18 个 Material；
- 1 个 Skeleton、276 根骨骼、51 个 Skin；
- 37 张实际引用纹理完成按需导出；
- `LODGroup` 明确恢复 4 层，分别引用 12、12、10、10 个 Renderer；另有 7 个 `shadowProxyDesktop` Renderer，不作为可见表面导出；
- ModelDocument 状态为 `texturedSkinnedModel`。

LOD0 GLB 经过依赖裁剪后的结果：

| 项目 | 数量 |
| --- | ---: |
| Node | 455，包含一个坐标系包装节点 |
| Mesh | 12 |
| Skin | 12 |
| Material | 11 |
| Image / Texture | 23 / 23 |
| Accessor / BufferView | 96 / 119 |
| 文件大小 | 37,526,108 字节 |

浏览器验证确认模型完整、姿态和构图正常，控制台无错误。这证明 Manifest 依赖闭包、跨 Bundle PPtr、几何、骨架、蒙皮、材质、纹理、LOD 和 GLB 预览链路已经连通。

## Blender 预览后端

Blender 4.3 的 glTF 导入器会把材质 `extras.endfieldPreview` 保留为 `Material["endfieldPreview"]` 自定义属性，因此 Blender 后端可以直接消费 GLB，不需要再次读取 ModelDocument。`tools/blender_import_model.py` 当前负责：

- 导入 GLB 并保留骨架、蒙皮、材质分区和纹理；
- 只转换 `materialFamily == "characterNpr"` 的材质，其他材质保持 glTF 导入结果；
- 保留导入器生成的基础色纹理与颜色乘算节点，将输出接入 Eevee `Diffuse -> Shader to RGB -> Color Ramp` 分段受光链；
- 从转换后的 ORM 贴图 Alpha 读取 HGRP 原始 Spec 通道，为衣物 Principled 材质恢复逐像素高光强度；
- 将 `CharacterNPR_OverlayShadow` 的乘算混合语义近似为透明黑层衰减；这是 Eevee 无法读取目标帧缓冲时的灰度近似，不是原 Shader 的逐通道精确复刻；
- 建立验证相机、双区域光和 World；传入 `--lighting` 时用角色 Cubemap 和生效的 `HGCharacterVolume` 环境光参数替代硬编码输入；
- 通过 `--framing full|portrait` 切换全身和上半身验证构图；
- 可选启用 Freestyle 外轮廓，并输出可继续编辑的 `.blend` 和验证 PNG。

光照数据通过版本化的 `character-lighting.json` 进入 Blender，而不是由 Blender 脚本解析 TypeTree 文本。该文档保留 Profile 与 Cubemap 来源、环境光原始值、六面相对路径、贴图编码和派生的等距柱状贴图路径，格式由 `schemas/character-lighting.schema.json` 固定。`character_lighting.py` 负责严格校验和坐标换算，`tools/build_character_lighting.py` 负责六面投影，Blender 后端只消费稳定语义。这样将来换成 HDR EXR 或修正方向约定时，不需要改动模型恢复层。

该后端首先验证统一材质语义能否跨宿主复用。Color Ramp 和 Freestyle 参数目前是预览默认值，不是从游戏 Shader 反编译得到的常量。Freestyle 会把眼睛、发丝等独立网格边界识别为轮廓，精度低于后续计划中的材质分类反面外扩方案。

角色 Prefab 中的 Animator 没有绑定 Controller。佩丽卡真实样本的面部与虹膜网格均无 BlendShape，渲染器默认权重也为空；骨骼顺序、BindPose 和静止姿态矩阵可以回到一致模型空间。因此，眼位与表情差异不能通过“补导默认 BlendShape”解决，而应作为运行时面部控制或中性姿态输入单独恢复。预览后端不应把单个角色的经验偏移写入通用模型解析器。

## Shader 变体筛选

FractalMiner 导出的 Shader 会把每个编译变体写成单独的 HLSL include。`tools/select_shader_variants.py` 根据 Shader 的 `[Toggle(KEYWORD)]` 声明和 ModelDocument 中的材质浮点属性，筛选与材质局部关键字完全一致的片元变体：

```powershell
python tools/select_shader_variants.py `
  path/to/characterNpr_skin.shader `
  path/to/model-document.json `
  "MaterialName"
```

输出保留候选 include 以及每个候选要求启用或禁用的运行时关键字。材质可以确定 `_SDFLIGHTMAP`、`_DIFF_RAMP_ON` 等局部分支，但不能确定屏幕空间阴影、运行时溶解等全局状态；工具会报告这些差异，不会擅自选择。

## 当前限制

- 当前预览已区分衣物 PBR 与面部/头发风格化渲染，并恢复衣物 Spec 通道和覆盖阴影的近似语义；Toon Ramp、眼睛高光/散射、头发高光、覆盖阴影逐通道乘算和丝袜各向异性仍与游戏存在差异。游戏画面没有显眼描边，因此轮廓只保留为可选诊断效果，不作为默认还原目标。
- Blender 后端已能按 `materialRole` 选择处理路径并为 Skin/Hair 读取真实 `_DiffRampMap`；当前用世界法线与 Profile 方向的点积近似 Ramp 横坐标，并用环境光参数近似明暗范围。方位角暂按 Blender `+Y` 为 `0°`、绕 `Z` 轴旋转解释，这是根据角色正面受光样本校准的预览约定，尚不是已证明的 HGRP shader 公式。
- 当前 Cubemap 从 BC6H 解码为六面 LDR PNG，再投影为等距柱状 PNG；顺序和朝向已经真实样本验证，但 HDR 范围与 mip 采样尚未保留。Eye、OverlayShadow 和 SilkStockings 仍无完整专用节点组。
- manifest 中的 AnimationClip 已能按逻辑路径定位到所属 Bundle，并按唯一子资源名和 PathID
  解析 AnimeStudio 导出文件；ACL 2.1 压缩曲线现已能转换为标准 Unity YAML 关键帧，
  但尚未进入 ModelDocument 和模型预览的姿态应用链路。
- BlendShape、AnimationClip 姿态应用、AnimatorController、运行时面部控制和物理骨骼尚未进入最终预览链路。
- 当前只验证了一个角色展示 Prefab，仍需用更多角色、怪物和非角色 Prefab 验证协议边界。
- GLB 当前保留完整节点层级，因此低 LOD Renderer 节点仍存在，但不会引用被裁剪的 Mesh 和 Skin。

## 实现原则

- 原始值与派生预览值分开保存，预览映射不得覆盖 Unity Shader 原始参数。
- 不支持的 Unity 类型、意外字段和断裂引用必须形成诊断或错误。
- GLB、glTF、FBX 等输出都应从 ModelDocument 生成，不能各自重复解析 Unity 对象。
- Schema 发生不兼容修改时提升主版本，读取器明确拒绝未知主版本。
- 真实样本用于端到端验证；坐标、矩阵、LOD 和资源裁剪规则还必须有小型合成回归测试。

## 动画恢复阶段结论（2026-07-31）

终末地正式版 `AnimationClip` 使用独立的 `m_AclCompressedBuffer`。当前已确认：

- `TransformBufferData` 是 ACL 2.1 `qvvf` 轨道；
- `FloatBufferData` 是 ACL 2.1 `float1f` 轨道；
- 佩丽卡待机动画包含 272 条 Transform 轨道、152 条浮点轨道、121 帧，
  采样率 60 Hz；
- 272 条 Transform 轨道与绑定表中按顺序排列的骨骼路径一一对应，
  所有路径哈希均可由模型骨架相对路径的 Unity CRC32 还原；
- 272 组位置、272 组旋转和 152 组浮点绑定共生成 696 条曲线、
  84,216 个关键帧，没有遗漏普通绑定；
- 该待机动画第 0 帧和循环内采样的眼球、虹膜局部变换基本等于 Prefab
  静态姿态。因此，当前眼部视觉差异不能仅归因于待机动画，仍需继续检查
  运行时面部控制和 Eye Shader。

AnimeStudio 研究分支新增了独立的 `acl_endfield` 原生桥。它使用固定版本的
ACL/RTM 头文件解压帧数组，C# 层负责 Unity path/attribute 绑定和曲线生成。
该实现不替换已有的 `acl.dll`，避免影响其他游戏的旧 ACL 路径。

`RootMotionBufferData` 也是 ACL 数据，但不属于普通绑定曲线。佩丽卡待机样本
的 `RootPosIndex`、`RootRotIndex`、`RootScaleIndex` 均为 `65535`，没有声明
实际根变换。转换器会在遇到声明了根变换的样本时明确报错，待后续单独实现
Root Motion 映射，避免静默丢失。

当前已完成第一层消费链路：

1. AnimeStudio 可导出带共享时间轴和原始 `pathHash` 的紧凑动画 JSON。
2. 动画适配器将 Transform 曲线追加到 `ModelDocument`，GLB 导出器生成标准
   glTF 动画通道。
3. 佩丽卡样本的 272 条位移和 272 条旋转通道全部映射成功，零缺失、零歧义；
   152 条材质或组件浮点曲线暂不进入 GLB，并形成明确诊断。

下一阶段按以下顺序推进：

1. 将动画资产按需挂载到模型服务接口，并在浏览器预览中播放或指定采样时间。
2. 用身体骨骼位移明显的待机帧验证
   坐标系、局部变换和采样顺序。
3. 收集 Animator Controller、面部控制和附加运行时参数，区分普通骨骼动画与
   眼球注视、表情等运行时驱动。
4. 姿态链路稳定后继续还原身体 PBR、面部/头发 NPR、Eye Shader 和丝袜材质，
   不用角色专用骨骼偏移掩盖渲染问题。
