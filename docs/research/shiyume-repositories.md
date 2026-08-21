# ShiyumeMeguri 相关仓库研究

本文记录截至 2026-08-02 对 [ShiyumeMeguri](https://github.com/ShiyumeMeguri) 公开仓库的结构性调研。目标是学习可验证的数据结构、坐标变换和资源组装流程，不复制 AGPL/GPL 实现。

## 模型恢复主链

### RuriRipperImporter

[RuriRipperImporter](https://github.com/ShiyumeMeguri/RuriRipperImporter) 将 Unity YAML 中的 Prefab、Mesh、骨架、蒙皮、材质和动画组装为 Blender 对象。其组装顺序值得参考：建立资源数据库、恢复 Transform 层级、发现 Renderer、处理 LOD 与静态合批窗口、解码 Mesh、应用 Bind Pose、绑定材质，最后从 AnimatorController 发现并绑定动画。

它还显式诊断“Prefab 只引用二进制 FBX、自身没有几何”的情况。我们的恢复器也不能把空模型误报为成功。

2026-08-02 对照作者的[终末地 Blender 直读演示](https://www.bilibili.com/video/BV1SnKg6cEkQ)与最新代码后，确认其 Humanoid 路线已经发生重要变化：肌肉曲线不再留给 Blender 侧临时解释，而是在 C# 资产处理阶段结合 Avatar referential、swing-twist、TwistSolve 和根运动语义转换为普通逐骨骼 Transform 曲线。Blender 导入层只消费统一后的 generic clip。这说明我们此前把 Humanoid 解算混入最终导出层的路线边界过重；后续应把“原始曲线解码”和“Avatar 相关的肌肉重定向”放在宿主无关层，并以普通骨骼曲线作为 GLB/Blender 的稳定输入。

演示中的终末地 AssetBundle 直读依赖未公开的游戏专用 Hook，不能替代本项目已经实现的本地 VFS、解密和按需读取。公开仓库仍可作为动画数学、资源闭包与导出结果的独立对照。

### RuriRipperPyBridge

[RuriRipperPyBridge](https://github.com/ShiyumeMeguri/RuriRipperPyBridge) 是 Blender 与 Substance 插件共用的宿主无关层，采用 `math3d <- unity <- runtime/session` 的单向依赖。其设计验证了 `ModelDocument` 应独立于最终宿主：

- 坐标系是集中定义的数据，而不是散落的条件分支；
- 磁盘 YAML 与内存依赖闭包实现相同的资源数据库接口；
- Unity class ID 使用稳定数字身份；
- 网格、Renderer、材质和动画发现彼此分层；
- 缺失 Mesh、外部 StreamData 和未知材质字段均产生诊断。

我们采用这些边界与原则，不采用其解析器或 RipperHook 桥代码。

### RuriRipperImporterSubstance

[RuriRipperImporterSubstance](https://github.com/ShiyumeMeguri/RuriRipperImporterSubstance) 证明同一依赖闭包可以服务于不同输出宿主。它自行生成 GLB，再将终末地材质映射到 Substance 通道。

直接可验证的注意项包括：

- Unity 到 glTF 同时涉及 X 轴反射、三角形绕序和 V 翻转；
- 切线手性由坐标反射与 UV 翻转共同决定；
- BufferView 和 Accessor 必须满足字节对齐与范围约束；
- 静态合批 Renderer 可能只引用 Mesh 的一个 SubMesh 窗口；
- 原始 Shader 参数与预览材质映射必须分开保存；
- 目标宿主不支持的骨骼、BlendShape 等内容应报告而非静默丢弃。

### Ruri.ShaderDecompiler

[Ruri.ShaderDecompiler](https://github.com/ShiyumeMeguri/Ruri.ShaderDecompiler) 将 DXBC、DXIL 或 SPIR-V 统一到 SPIR-V 中间层，再把 Unity/UE 元数据中的符号映射注入中间表示。

Shader 恢复因此需要两类证据：GPU 字节码描述运算，Unity Shader/Material 元数据提供变量名和绑定槽。我们的材质模型应保留 Shader 名称、原始属性、纹理槽和程序身份，为后续符号化反编译留出接口。

### Ruri.RipperHook

[Ruri.RipperHook](https://github.com/FractalTools/Ruri.RipperHook) 的公开部分把 AssetRipper 作为只读上游，通过 AOP Hook 插入自定义 VFS、TypeTree、Humanoid 转换和 Shader 导出步骤。与本项目当前 Shader 副线直接相关的实现包括 Unity 编译程序包读取、Endfield 特有的平台/绑定索引处理、DXBC/DXIL/SPIR-V 识别以及 ShaderLab 元数据与 GPU 字节码的重新关联。

它验证了当前“先严格提取原始程序，再建立 Pass/Stage/keyword/资源绑定语义 IR，最后转换为 Blender 材质”的顺序。由于仓库采用 AGPL，项目仅对照公开格式、输入输出和独立验证结果，不复制实现；终末地专用解密 Hook 也不在公开源码中。

### FractalMiner 的终末地 Shader 归档

[FractalMiner/Assets/Project/EndField](https://github.com/ShiyumeMeguri/FractalMiner/tree/main/Assets/Project/EndField) 保存了多个客户端版本的 ShaderLab 与反编译 HLSL。2026-07-31 以 `AllShader_1.3.3.7z` 对照佩丽卡材质，确认：

- 角色主体使用 `HGRP/CharacterNPR` 家族，眼睛、头发、皮肤、覆盖阴影和 Proxy LOD 各有独立变体；
- 1.3.3 的 `_MetallicGlossMap` 明确声明 `RGBA: Metal, Spec, Shadow, Smooth`。标准 glTF 只能无损承接其中的金属度与光滑度，因此当前转换为 `B=Metallic`、`G=Roughness=1-Smoothness`，Spec 与 Shadow 等待专用材质实现；
- `CharacterNPR_OverlayShadow` 的 `_UseGrayAsAlpha` 会把贴图 RGB 视为白色，并把原贴图 R 通道作为透明度；佩丽卡两张 32×32 阴影贴图也只有 R 通道携带有效灰度；
- OverlayShadow 使用 `Blend Zero SrcColor, One One`、关闭深度写入，并通过 `_ShadowOverIris` 对 stencil 做条件测试，不能完整等价为普通 alpha blend；
- CharacterNPR 的丝袜分支包含颜色、干湿状态、各向异性高光和四通道 `_SilkStockingsMask`，不是简单的基础色乘黑；
- 1.2.4 仍使用 `_PANTYHOSE` 分支及 `_PantyhoseSpecularInt`、`_PantyhoseSpecularValue`、`_PantyhoseAnisotropyDirection`；1.3.3 新增 `_SILK_STOCKINGS` 分支、干湿颜色、四通道 Mask 和 `_SilkStockingsAlbedoDarker`，证明该材质模型在版本间发生过结构性变化；
- 当前佩丽卡材质同时保存 1.3.3 风格字段和归档属性表中不存在的 `_SilkStockingsMaxAffect`、`_SilkStockingsMinAffect`。Unity Material 会保留已保存的旧属性、却不一定保存沿用 Shader 默认值的新属性，因此“材质中存在字段”不等于当前 Shader 仍会读取它。公式迁移必须同时验证 Shader 声明、启用关键字、材质值和真实画面。

当前实现只采用已被材质数据和 Shader 同时证实的可移植语义：R 通道转 alpha、覆盖层透明混合、金属/光滑贴图转换，以及按属性签名识别材质角色。衣物进入标准 PBR，皮肤、头发、眼睛和覆盖阴影暂保留为风格化/无光照预览。丝袜暂以边缘色和旧的最大影响量做启发式整体压暗，该值只为避免白模，不代表已经还原当前 Shader 公式；各向异性与干湿效果仍未复刻。研究归档只位于临时目录，不复制进项目源码。

## 动画与运行时表现

### AnimationRetarget

[AnimationRetarget](https://github.com/ShiyumeMeguri/AnimationRetarget) 以纯数学方式处理 Rest Pose、世界旋转、参考姿态、Root Motion、IK 与继承缩放。它提醒我们：“AnimationClip 已解析”和“动画可正确播放在另一骨架上”是两个阶段。`ModelDocument` 首先保存原始曲线和 Avatar 身份，重定向属于派生处理。

### PhysicsBoneBlender 与 KawaiiPhysicsBlender

[PhysicsBoneBlender](https://github.com/ShiyumeMeguri/PhysicsBoneBlender) 和 [KawaiiPhysicsBlender](https://github.com/ShiyumeMeguri/KawaiiPhysicsBlender) 不参与基础模型导出，但说明完整角色表现还可能依赖物理骨链、碰撞体、阻尼、角度限制、风以及物理与动画的叠加顺序。这些属于后续运行时组件语义，不应阻塞第一版 GLB，但相关原始组件不能静默丢弃。

## 其他 2026 年仓库

- [FractalAnimator](https://github.com/ShiyumeMeguri/FractalAnimator)：与角色动画无直接关系；可参考其二进制格式实现逐项对照原始记录定义与读取顺序的审计方法。
- [FractalMiner](https://github.com/ShiyumeMeguri/FractalMiner)与 [FractalParadise](https://github.com/ShiyumeMeguri/FractalParadise)：ShaderLab/GPU 图形项目，可用于 Shader 工程实践对照，不参与资源组装。
- [NormalMapToMesh](https://github.com/ShiyumeMeguri/NormalMapToMesh)、[MeshSurfaceConformer](https://github.com/ShiyumeMeguri/MeshSurfaceConformer)、[BakeNormalForAll](https://github.com/ShiyumeMeguri/BakeNormalForAll)、[TextureProjector](https://github.com/ShiyumeMeguri/TextureProjector)：模型加工工具，不能作为原始资源恢复依据。
- [ShiyumeBlender](https://github.com/ShiyumeMeguri/ShiyumeBlender)、[LoopToolsPlus](https://github.com/ShiyumeMeguri/LoopToolsPlus)：Blender 工作流工具，与浏览器后端关系较低。
- `Unity-Fractal-Voxel-Terrain-GPU`、`DontStarveTogetherMods`、`ShikiActionGame` 和模型许可证仓库与本任务无直接关系。

## 对本项目的结论

1. 保持“定位、Unity 对象、ModelDocument、输出宿主”四层结构。
2. 第一版输出 LOD0、骨架、蒙皮、基础材质与依赖诊断，不等待动画和高保真 Shader。
3. 坐标空间、UV 原点、三角绕序和切线手性必须作为整体转换策略。
4. 原始材质参数与 PBR 预览映射并存，后者不能覆盖前者。
5. 动画曲线解析、Avatar 重定向和物理骨骼恢复是三个不同阶段。
6. 所有降级都形成结构化诊断。
7. 相关仓库多为 AGPL-3.0 或 GPL-3.0；本项目只记录结构与验证结论，不引入源代码。
