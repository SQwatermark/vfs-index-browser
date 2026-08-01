# Unity Shader 到 Blender 材质节点的自动转换可行性

## 结论

可以为《终末地》角色材质建立一套有实际价值的自动/半自动转换管线，但不应把目标定义为
“任意 Unity Shader 一键转换成 Blender 节点图”。更合理的目标是：

1. 自动选择材质实际使用的 Shader 变体；
2. 自动恢复该变体中与表面材质相关的数据流；
3. 把可移植语义降级为 Blender 原生节点；
4. 把依赖终末地运行时渲染管线的部分显式建模为外部输入、近似后端或未支持项；
5. 对面部、头发、丝袜等高价值分支提供少量经过验证的领域节点。

这条路线不是逐行翻译 HLSL。它更接近一个“材质语义编译器”：反编译 HLSL 是证据来源，
中间表示负责隔离游戏版本和目标渲染器，Blender 节点只是其中一个后端。

## 现有基础

当前仓库已经具备适合作为该管线入口的结构：

- `sourceMaterial` 完整保留 Shader 身份、纹理槽、整数、浮点和颜色；
- `previewPbr` 保存可移植的预览近似，不覆盖原始材质数据；
- `shader_variants.py` 能从 ShaderLab 的 Toggle 声明和变体条件中筛选与材质开关相容的片元变体；
- `trace_hlsl_dataflow.py` 已能对反编译 HLSL 的单赋值临时变量建立保守依赖图；
- Blender 后端已经实现通用 PBR、Character NPR、面部 SDF、丝袜、衣物和 OverlayShadow 的
  专用节点构造；
- FractalMiner 的 1.4.4 Shader 归档包含 ShaderLab、HLSL/GLSL 变体、关键字组合和部分恢复后的
  资源/常量缓冲名称。

上述分层方向正确。后续不应把越来越多 Shader 公式直接堆入
`tools/blender_import_model.py`，而应先建立独立的材质 IR 和后端。

## 证据概况

FractalMiner 1.4.4 的 CharacterNPR 归档包含 8 个角色 Shader、4011 个 HLSL 文件和
849 个 GLSL 文件。仅通用 `CharacterNPR` 目录就包含 2214 个 HLSL 变体，Hair 为 870 个，
Skin 为 652 个。通用前向 Pass 声明了法线、发光、金属高光、各向异性、透明、写实光照、
Spec Ramp、丝袜、Clear Coat、视差、腐蚀、毛发、Shadow LUT 等大量局部关键字。

以丝袜样本 `Sub0_Pass0_Fragment_b391.hlsl` 为例，归档已经明确记录其关键字组合：

```text
_METALLICSPECGLOSSMAP
_NORMALMAP
_SILK_STOCKINGS
_SPEC_RAMP_ON
```

但同一文件也读取 Transform、全局环境、屏幕空间阴影、角色参数、曝光、雾等大型常量缓冲。
这说明“材质选择的静态数据流”可以恢复，而“游戏完整帧环境”不能从 Material 单独推导。

## 可机械转换的边界

### 可以自动精确转换

以下操作在类型、坐标空间和色彩空间信息齐全时，可以机械映射为材质 IR，并进一步生成
Blender 节点：

- 标量、向量和颜色常量；
- Material 中的浮点、整数、颜色和纹理绑定；
- UV 选择、缩放、偏移、镜像和常见采样模式；
- 纹理采样、通道拆分/重组和法线贴图 RG 重建 Z；
- `add/sub/mul/div/min/max/abs/pow/saturate/clamp/dot/normalize` 等纯函数；
- `lerp`、比较和静态条件选择；
- Ramp、Matcap、铺平 3D LUT、遮罩混合；
- Base Color、Metallic、Roughness、Normal、Emission、Alpha、Coat、Sheen、Anisotropy
  等 Blender 可表达的标准表面参数；
- 已知且稳定的终末地专用函数，例如面部左右 SDF 采样、Shadow LUT 地址计算和丝袜遮罩通道。

“精确”只表示该局部表达式等价，不表示 Blender 与游戏最终像素完全相同。纹理过滤、
导数、BRDF、阴影和色彩管理仍可能产生后端差异。

### 可以自动生成，但需要语义适配器

以下内容可以保留数据流，却必须通过终末地/Blender 专用适配器解释：

- object/world/view/tangent 空间之间的变换；
- 主光、角色主光、环境光、SH、Cubemap 和附加光；
- GGX、皮肤高光、头发各向异性、丝袜透肉等游戏专用 BRDF 项；
- `_CharacterParams0..16`、CharacterVolume 和角色专用曝光；
- 线性/sRGB 转换以及游戏后处理前后的颜色域；
- Shader 中的采样器状态与 Blender Image Texture 设置之间的映射；
- Alpha Clip、双面、混合、Cull、深度写入等材质和渲染状态。

这类节点应在 IR 中保留明确名字和输入，不能在解析阶段直接替换成经验常数。

### 不能直接转换为普通 Blender 材质节点

- 屏幕空间阴影、深度、场景颜色、运动矢量和 GBuffer 读取；
- 多 Pass、Stencil、独立 Pre-Z、ShadowCaster、OverlayShadow 的跨对象关系；
- Clustered/Forward+ 附加光列表和游戏专用阴影图；
- TAA、曝光、雾、天气、角色 Volume 混合和其他后处理；
- 顶点动画纹理、几何膨胀描边、运行时蒙皮等非表面片元逻辑；
- 动态循环、动态数组索引、原子操作、UAV、Compute 和平台内建函数；
- 缺少符号名、纹理绑定、常量缓冲布局或运行时取值的反编译片段。

这部分应分别进入场景配置、几何修改器、合成器、离线烘焙或“不支持”诊断，不能用普通
材质节点假装完成。

## 为什么不能直接做 HLSL 到节点图

DXC 能把 HLSL 编译为 DXIL，SPIR-V 工具可以在着色语言之间转换，但它们解决的是可执行
Shader 代码转换，不会自动恢复适合美术编辑的材质语义。MaterialX 则从节点图生成目标
Shader，也不是将任意低层 HLSL 反编译为高层节点图。

反编译后的终末地 HLSL 已接近 SSA 风格，适合建立依赖图，但仍存在三个信息损失：

1. 高层函数和节点边界已经内联；
2. 多个物理意义不同的量可能只剩相同的 `float3` 类型；
3. 材质、相机、灯光、屏幕缓冲和全局 Volume 的输入混在同一程序中。

因此推荐“语法自动化 + 语义模板 + 人工确认”，而不是宣称通用反编译。

## 推荐的中间表示

建议新增独立的 `MaterialSemanticIR`，不要直接复用 Blender 节点类型，也不要把它塞进
`previewPbr`。一个材质实例应引用一个已解析的 Shader 程序，并绑定自己的参数：

```text
MaterialInstance
  shaderIdentity
  shaderArchiveVersion
  passIdentity
  variantKeywords
  bindings
    textures / samplers / uniforms
  surfaceProgram -> GraphOutput
  renderState
  diagnostics
```

IR 节点至少分为以下几类：

| 类别 | 示例 |
| --- | --- |
| Value | constant、uniform、texture、vertexAttribute、runtimeInput |
| Math | add、multiply、dot、normalize、pow、clamp、compare、select |
| Texture | sample2D、sampleCube、sampleLut3DFlattened、unpackNormal |
| Space | tangentToWorld、objectToWorld、viewDirection、facing |
| Material | standardSurface、emission、alphaClip、normal、coat、anisotropy |
| Endfield | faceSdf、shadowLut、characterLighting、hairSpecular、silkStockings |
| Pipeline | screenShadow、additionalLights、fog、exposure、stencil、passOutput |

每个节点还应携带：

- 数据类型和颜色域；
- 坐标空间；
- 来源文件、行号或反编译临时变量；
- 证据等级：`exact`、`recovered`、`approximated`、`external`、`unsupported`；
- 游戏版本和 Shader 变体身份。

推荐内部使用 JSON 可序列化 DAG。MaterialX 可作为后续交换后端，但不应直接作为第一版
内部 IR：终末地专用运行时输入和多 Pass 状态仍需要扩展语义，而且 MaterialX 的主要方向是
从图生成 Shader，并不会替我们完成逆向恢复。

## 推荐转换流水线

```text
Unity Material + Shader archive + texture metadata
  -> 解析 ShaderLab 属性、Pass、关键字和渲染状态
  -> 根据材质 Toggle/Keyword 选择实际变体
  -> 解析反编译 HLSL，规范化为 typed SSA/CFG
  -> 从片元输出反向切片，剔除无关路径
  -> 恢复纹理/采样器/常量缓冲绑定
  -> 模式识别：普通算术、PBR、Ramp、SDF、LUT、游戏专用函数
  -> MaterialSemanticIR
  -> 能力检查与诊断
  -> Blender 原生节点 / Cycles OSL / 纹理烘焙 / 调试报告
```

### HLSL 前端

第一版应只支持 FractalMiner 反编译器当前生成的 HLSL 子集。可以用正式语法分析器生成 AST，
但表达式降级和语义识别仍由项目代码负责。现有正则依赖追踪器适合研究验证，不适合作为
长期编译器前端。

控制流建议先限制为：

- 静态关键字已经消除的分支；
- 可转成 `select` 的局部 `if`；
- 固定次数、可展开的小循环；
- 不接受未知副作用和动态资源索引。

遇到不支持语法必须报错并指出输出受影响范围，不能静默删除。

### Blender 后端

建议同时维护三种输出模式：

1. `preview`：Blender 原生节点，兼容 EEVEE/Cycles，优先交互性能；
2. `research`：尽可能展开公式的原生节点或节点组，便于逐项检查；
3. `cycles-osl`：复杂纯函数的参考实现，仅用于 Cycles 验证。

OSL 不适合作为默认输出。Blender 官方文档明确其为 Cycles 功能，且不同设备后端存在限制；
它适合验证复杂数学，不适合网页预览或通用 `.blend` 交付。`Shader to RGB` 依赖 EEVEE，
也不应作为跨渲染器的核心 IR。

## 终末地角色材质的支持优先级

### P0：数据与变体正确性

- 真实 Shader 身份与归档版本；
- 材质关键字、Pass 和实际变体选择；
- 纹理槽、采样器、UV Transform、颜色空间；
- Packed Map 通道语义与 DirectX Normal 重建；
- 不支持项和缺失运行时输入的明确诊断。

这是后续所有视觉恢复的基础，优先级高于继续增加手调节点。

### P1：通用 CharacterNPR 表面

- BaseMap/BaseColor；
- Metallic/Specular/Shadow/Smoothness Packed Map；
- Normal、Emission、Alpha Clip、双面；
- DiffRamp、SpecRamp、Shadow LUT；
- Clear Coat、基础各向异性；
- 场景受光与角色最低环境光分开建模。

### P2：角色专用分支

- Skin/Face：SDF Lightmap、SDF Mask、Emotion、Highlight、Shadow LUT；
- Hair：Split Normal、Stroke Map、双层各向异性、透明裁剪；
- Eye：眼部 Tint、Matcap/高光和透明语义；
- Cloth：PBR、Clear Coat、各向异性；
- Silk Stockings：透肉、视角色、湿润高光和各向异性；
- OverlayShadow：独立材质/对象关系，不并入普通 Base Color。

### P3：场景和运行时一致性

- CharacterVolume 到 `_CharacterParams` 的精确映射；
- 主光、角色主光、环境 Cubemap、SH 和曝光；
- 游戏阴影、附加光、雾和天气；
- 多 Pass、Stencil、描边和 VFX。

P3 决定最终截图能否接近游戏，但不能阻塞 P0-P2 的可编辑材质恢复。

## 分阶段实施计划

### 阶段 A：建立可验证样本集

- 固定游戏版本和 Shader 归档版本；
- 选择通用衣物、面部、头发、眼睛、丝袜、OverlayShadow 各一个材质；
- 保存 Material、所选变体、纹理、运行时未知输入清单和游戏参考截图；
- 为每个样本记录“当前预览、目标分支、已知缺口”。

验收：任何一次转换都能追溯到确定的 Shader、Pass、变体和材质参数。

### 阶段 B：MaterialSemanticIR 与绑定层

- 定义 IR schema、类型、颜色域、空间和证据等级；
- 把现有 `sourceMaterial -> previewPbr` 逻辑改造为独立的绑定/分类步骤；
- 输出 IR 检查报告，不生成新节点。

验收：P0 样本的纹理、通道、关键字和渲染状态无静默丢失。

当前实验进度：独立 Material Binding Resolver 已能合并 Shader 默认值与 Material
实例值，并保留纹理 UV、颜色空间及 Shader/Texture 两级采样器。CharacterNPR b391
映射原型会把全部解析属性保留为 IR Binding，只对已确认的丝袜材质局部公式建立领域节点；
随后生成不依赖 `bpy` 的 `BlenderNodeParameterPlan`。该计划尚未接入生产导入器。

### 阶段 C：受限 HLSL 数据流恢复

- 用 AST 替代正则赋值解析；
- 建立 typed SSA/CFG 和片元输出反向切片；
- 恢复资源绑定并识别基础算术、采样、Ramp、LUT、法线模式；
- 对动态控制流、未知缓冲和不支持内建函数生成诊断。

验收：可自动重建 b391 丝袜主干和面部变体的已确认 SDF/LUT 主干，并与现有手工公式逐项对照。

### 阶段 D：Blender 原生节点后端

- IR 到版本化 Node Group；
- 共用子图去重，不为每个材质展开数千节点；
- 输出节点标注来源和证据等级；
- 保留 preview/research 两种模式。

验收：基础衣物、头发、面部、丝袜在 EEVEE 和 Cycles 中均可打开；未知输入不会被悄悄替换。

### 阶段 E：自动校验

- 给纯函数 IR 建 CPU 参考求值器；
- 对随机输入比较 HLSL 摘取公式、IR 和 Blender/OSL 输出；
- 建立固定灯光、相机、色彩管理的离线截图回归；
- 最后再接游戏运行时抓帧做像素级对照。

验收：数学误差、纹理误差、光照缺口和后处理差异可以分开报告。

## 工程建议

- Shader 程序按 `archiveVersion + shader + pass + keywordSet` 缓存，材质只保存绑定，避免
  为相同变体重复建图；
- 版本化领域节点，例如 `EF_FaceSDF_v1`，避免修改节点组后破坏旧 `.blend`；
- 保留原始 Material 和 IR，Blender 节点始终是可再生派生物；
- 不把自动生成的巨大节点图作为唯一事实来源；
- 解析器遇到未知标签、资源类型、语法、采样方式或运行时输入时沿用项目现有原则：立即
  形成可见诊断，不静默丢弃；
- 主线模型/动画恢复与 Shader 副线只通过稳定的 ModelDocument/Material IR 契约连接。

## 推荐的近期最小实验

不要先写完整编译器。先以 b391 丝袜和 1.4.4 面部 b138 为两个端到端样本：

1. 从 ShaderLab 和 Material 自动选出变体；
2. 从片元输出切出与 Base Color、Roughness、Specular 相关的依赖子图；
3. 把纹理采样和普通算术写入最小 JSON IR；
4. 对已知丝袜/SDF 模式折叠成领域节点；
5. 自动生成 Blender 节点，并与当前手工节点组做数值 A/B；
6. 统计仍依赖的 runtime inputs，作为下一轮 CharacterVolume 研究清单。

若这两个样本成功，说明 IR 和后端方向成立；若失败，也能明确失败发生在语法解析、绑定恢复、
语义识别还是 Blender 表达能力，而不会继续靠视觉调参掩盖问题。

## 参考资料

- Unity Shader variants：<https://docs.unity3d.com/Manual/shader-variants.html>
- Microsoft DirectX Shader Compiler：<https://github.com/microsoft/DirectXShaderCompiler>
- Khronos SPIR-V resources：<https://www.khronos.org/spirv/resources>
- MaterialX 项目：<https://github.com/AcademySoftwareFoundation/MaterialX>
- MaterialX Specification：<https://github.com/AcademySoftwareFoundation/MaterialX/blob/main/documents/Specification/MaterialX.Specification.md>
- Blender Shader Node Tree API：<https://docs.blender.org/api/current/bpy.types.ShaderNodeTree.html>
- Blender Open Shading Language：<https://docs.blender.org/manual/en/4.2/render/shader_nodes/osl.html>
- Blender Shader to RGB 限制：<https://docs.blender.org/manual/en/latest/render/eevee/materials/nodes_support.html>
- FractalMiner EndField Shader 归档：<https://github.com/ShiyumeMeguri/FractalMiner/tree/main/Assets/Project/EndField>
