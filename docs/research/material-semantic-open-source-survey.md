# 材质语义解析与转换开源组件调研

本文承接 `unity-shader-to-blender-feasibility.md`，回答两个工程问题：哪些开源组件值得
复用，以及它们应放在终末地材质恢复管线的哪一层。调研日期为 2026-08-01。

## 结论

不存在能够把任意 Unity Shader 或反编译 HLSL 自动还原成可编辑 Blender 材质的成熟
开源库。可复用组件只能可靠解决语法、字节码、资源反射、标准材质交换和 Blender 节点
创建；“某段计算代表丝袜透肉、面部 SDF、Shadow LUT 还是运行时雾”仍须由本项目恢复。

推荐组合如下：

1. **当前 HLSL 入口**：维护一个很薄的 `tree-sitter-hlsl` 语法补丁，生成带源位置的 CST；
   本项目负责符号绑定、类型、CFG/SSA 和反向切片。
2. **未来首选入口**：修改 Shader 导出过程，保留生成 HLSL 之前的 SPIR-V。使用
   SPIRV-Tools 做校验、反汇编和 IR 分析，使用 SPIRV-Cross 做反射和人工审计文本。
3. **内部表示**：使用项目自己的 `MaterialSemanticIR`，显式记录类型、颜色空间、坐标空间、
   来源证据和近似等级。
4. **正式 Blender 后端**：直接使用 `bpy` 生成版本化 Node Group；不经由 OSL，也不依赖
   第三方 Blender 插件。
5. **交换后端**：MaterialX 只作为以后可选的标准图导出格式，不作为第一版内部 IR。

首版不建议引入 DXC、Slang、HlslTools、ShaderConductor 或 AssetRipper 作为运行时依赖。
它们有参考价值，但对当前 SPIRV-Cross 生成文本的覆盖、集成重量或许可证边界不如上述组合。

## 当前输入的真实性质

当前 `AllShader_1.4.4.7z` 中的 `Sub0_Pass0_Fragment_b391.hlsl` 不是美术或程序员编写的
高层 HLSL，而是 SPIRV-Cross 从 SPIR-V 生成的文本，证据包括：

- 文件入口使用 `SPIRV_Cross_Input` 和 `SPIRV_Cross_Output`；
- 临时变量接近 SSA 风格，名称为 `_2848`、`_4112` 等；
- 资源和常量缓冲区已经降低为显式 `register`、`packoffset` 和 `Load<T>`；
- 高层函数边界、Shader Graph 节点边界和多数业务符号已经丢失。

因此从 HLSL 再编译到 DXIL/SPIR-V 不会恢复高层语义，只会增加一次有损降低。只要能在
原始导出环节保留 SPIR-V，就应直接以 SPIR-V 作为低层事实源；HLSL 前端是对现有归档的
兼容方案。

## 候选组件总表

“活跃度”依据仓库在调研日附近的提交或发行状态，只用于工程选型，不代表长期承诺。

| 组件 | 许可证 | 活跃度 | 输入 -> 输出 | 能解决的层级 | 不能解决的缺口 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| [tree-sitter-hlsl](https://github.com/tree-sitter-grammars/tree-sitter-hlsl) | MIT | 2025-06 有提交，0.2.0 | HLSL -> CST | 语法节点、范围、容错解析，Python 接入轻 | 无类型和语义；原语法不支持样本中的全部 register 形式 | **首版采用，小幅维护补丁** |
| [HlslTools](https://github.com/tgjones/HlslTools) | Apache-2.0 | 2026-04 有提交 | HLSL -> Roslyn 风格 AST/语义模型 | 预处理、完整语法树、符号和类型，架构值得参考 | .NET 侧车较重；实测不支持 SPIRV-Cross 的 `Load<T>` 子集 | 参考，不作为首版依赖 |
| [DXC](https://github.com/microsoft/DirectXShaderCompiler) | University of Illinois/NCSA | 2026-07 活跃，2026-02 有发行 | HLSL -> DXIL/SPIR-V；DXIL -> 反射 | 官方 HLSL 编译、验证、DXIL 容器和绑定反射 | 不把编译结果恢复为材质语义图；公开稳定接口不提供适合本任务的源 AST JSON | 验证工具，可选依赖 |
| [Clang HLSL](https://clang.llvm.org/docs/HLSL/HLSLSupport.html) | Apache-2.0 WITH LLVM-exception | 2026-08 活跃 | HLSL -> Clang AST/LLVM IR | 长期官方化的 HLSL 语法、类型和诊断方向 | HLSL 支持仍在演进；集成 LLVM 过重；仍不恢复材质含义 | 长期观察 |
| [Slang](https://github.com/shader-slang/slang) | Apache-2.0 WITH LLVM-exception | 2026-08 活跃，2026.5.2 | Slang/多数 HLSL -> 多后端与反射 | 模块化编译、准确布局反射、多目标生成 | 反射面向源参数布局，不导出可供我们改写的完整 HLSL AST/材质图 | 不引入，参考反射设计 |
| [SPIRV-Tools](https://github.com/KhronosGroup/SPIRV-Tools) | Apache-2.0 | 2026-08 活跃，v2026.1 | SPIR-V -> 校验、汇编/反汇编、优化 IR | 字节码真实性、指令、CFG、def-use 和规范化 | 无 Unity Material 绑定值；无 PBR/NPR 领域语义 | **未来 SPIR-V 主前端基础** |
| [SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross) | Apache-2.0 | 2026-07 活跃 | SPIR-V -> HLSL/GLSL/MSL/JSON 反射 | 资源反射、可读文本、跨语言审计；已有 C API | 输出仍是低层 Shader，不是节点图；C API 的主要承诺是反射和转换 | **采用作反射/审计后端** |
| [SPIRV-Reflect](https://github.com/KhronosGroup/SPIRV-Reflect) | Apache-2.0 | 2026-07 活跃 | SPIR-V -> 轻量 C/C++ 反射 | 描述符、接口变量、推送常量，依赖小 | 不提供表达式 IR 或反编译；与 SPIRV-Cross 反射重叠 | 仅在需要更轻 sidecar 时采用 |
| [dxil-spirv](https://github.com/HansKristian-Work/dxil-spirv) | MIT | 2026-07 活跃 | DXIL/DXBC -> SPIR-V | 若以后只取得 D3D 字节码，可统一到 SPIR-V 管线 | 目标是运行兼容而非可读反编译；不能恢复源符号和材质语义 | 条件性入口，不是当前依赖 |
| [ShaderConductor](https://github.com/microsoft/ShaderConductor) | MIT | 2023 已归档 | HLSL -> DXIL/SPIR-V -> GLSL/MSL | 展示 DXC 与 SPIRV-Cross 的组合方式 | 已归档；本项目无需再包一层同类转换器 | 不采用 |
| [glslang](https://github.com/KhronosGroup/glslang) | BSD-3-Clause 风格 | 2026 活跃 | GLSL/部分 HLSL -> AST/SPIR-V | Khronos 参考 GLSL 前端，附带有限 HLSL 支持和反射 | 官方明确 HLSL 前端是 partial；当前输入本就来自 SPIR-V | 不采用作 HLSL 主前端 |
| [MaterialX](https://github.com/AcademySoftwareFoundation/MaterialX) | Apache-2.0 | 2026-07 活跃，1.39.x | 标准节点图 <-> 文档/目标 Shader | 标准类型、NodeDef、NodeGraph、Standard Surface、Python API | 方向是“图生成 Shader”，不会把低层 HLSL 反推成高层图；难表达终末地多 Pass 运行时状态 | **后续交换后端** |
| [Blender Python API](https://docs.blender.org/api/current/bpy.types.ShaderNodeTree.html) | Blender GPL-3.0 | 2026-08 活跃 | IR -> Blender 节点/材质 | 原生 EEVEE/Cycles 节点、Node Group、可编辑 `.blend` | 屏幕空间纹理、多 Pass、Stencil 和终末地运行时光照不能直接表达 | **正式 Blender 后端** |
| [Unity Graphics / ShaderGraph](https://github.com/Unity-Technologies/Graphics/tree/master/Packages/com.unity.shadergraph) | Unity Companion License | 2026-07 活跃 | `.shadergraph` -> Unity Shader | 可参考 Unity 图序列化、属性、关键字和生成结构 | 玩家构建通常不含原始 ShaderGraph；许可绑定 Unity-dependent project | 只参考，不复制实现 |
| [UnityCsReference](https://github.com/Unity-Technologies/UnityCsReference) | Unity Reference Only License | 2026-07 活跃 | Unity 编辑器对象 -> Shader/Material API | 可确认 `ShaderUtil`、Material 属性和编辑器调用语义 | 仅参考许可，不适合复用；离线玩家资源没有编辑器对象 | 只作行为参考 |
| [AssetRipper](https://github.com/AssetRipper/AssetRipper) | GPL-3.0 | 2026-07 活跃，1.3.14 | Unity 序列化资源 -> Unity 工程/对象 | 版本化 Unity 类型、Material/Shader 读取经验 | GPL 传播边界；Shader 导出仍有 Dummy 等降级；与 AnimeStudio 边界重叠 | 只对照，不链接 |
| [AssetStudio](https://github.com/Perfare/AssetStudio) | MIT | 2023 归档 | Unity 资源 -> 对象/导出文件 | 简洁的旧版 Shader/Material 序列化参考 | 最高官方支持停在 Unity 2022.1，项目已归档 | 历史参考 |

许可证名称来自各仓库许可证文件；本文不是法律意见。Unity Reference Only、Unity Companion
和 GPL 组件尤其不应在未确认分发边界前复制或链接到主程序。

## 两个 HLSL 解析器的样本实测

测试对象为 1.4.4 归档的角色 `Sub0_Pass0_Fragment_b391.hlsl`，共 103,835 字节。

### tree-sitter-hlsl

Python 绑定可直接载入。原版 0.2.0 生成完整 CST，但报告 8 个 missing/error 节点，均位于：

```hlsl
cbuffer ... : register(b12, space0)
```

这表明它对主体表达式、模板 `Load<T>` 和控制流覆盖较好，缺口集中且可以通过扩展
`register` 语法及回归样本修复。它只提供 CST，因此类型推导和 def-use 必须由项目实现。

### HlslTools

直接引用其 `ShaderTools.CodeAnalysis.Hlsl` 项目后，同一样本产生 35 个诊断，主要来自：

```hlsl
_VertexSkinMatrices.Load<float4>(...)
_GlobalBinningBuffer.Load<uint>(...)
```

HlslTools 的 Roslyn 风格 API、预处理器和语义模型设计很好，但为适配该生成器子集仍需修改
解析器，同时引入 .NET sidecar。相较之下，修补 tree-sitter 的范围更小。

## 各层职责

### ShaderLab 外壳

当前 `shader_variants.py` 用正则读取 `[Toggle(KEYWORD)]` 和预处理分支。短期可保留，
但正式实现应改为一个专门的平衡括号/Token 解析器，至少输出：

- Properties 的属性名、类型、默认值和 Attribute；
- Pass 身份、阶段和变体 include；
- 每个分支的完整布尔条件，而不是仅收集 `defined()`；
- Blend、Cull、ZWrite、ZTest、Stencil 等渲染状态；
- 未识别语法的硬错误和源位置。

没有成熟、宽松许可且覆盖现代 Unity ShaderLab 的独立解析库值得引入。Unity ShaderGraph
源码也不是 ShaderLab 通用解析器。这里自行实现小而严格的外壳解析器，比复制 Unity 编辑器
代码更清晰。

### HLSL/SPIR-V 低层程序

首版 HLSL 路线：

```text
SPIRV-Cross HLSL
  -> patched tree-sitter CST
  -> 声明/表达式/语句 AST
  -> 名称解析和类型推导
  -> CFG + typed SSA
  -> 从片元输出反向切片
```

未来 SPIR-V 路线：

```text
原始 SPIR-V
  -> SPIRV-Tools validate/parse
  -> 指令、类型、CFG、def-use
  -> SPIRV-Cross reflection
  -> 与 HLSL 路线相同的 typed graph lowering
```

两条路线必须在“typed graph lowering”处汇合，不能让 Blender 后端知道输入来自 HLSL 还是
SPIR-V。

### 材质绑定

`ModelDocument.materials[].sourceMaterial` 已保留 Shader、TextureEnv、Int、Float 和 Color，
它应继续作为材质实例事实源。Shader 反射只描述槽位和布局，不包含某个 Material 实例绑定的
真实值。绑定阶段负责：

- 用属性名和常量缓冲区偏移连接 Material 值与程序输入；
- 记录纹理 ID、采样器、UV 变换、颜色空间和默认纹理；
- 把 `_CharacterParams*`、屏幕阴影、雾等标成 `runtimeInput`，不得填猜测常数；
- 对无法匹配的槽位双向报错：程序需要但材质没有、材质提供但变体未使用。

### 语义恢复

语义恢复不是 parser 的工作。它由可测试的 pattern recognizer 完成：

- 普通数学和纹理采样保持展开节点；
- 稳定且已验证的子图折叠为版本化领域节点，例如 `endfield.silkStockings.v1`；
- 每次折叠保存原始节点集合和匹配规则版本；
- 不满足完整模式时不做“差不多”的折叠；
- 无法表示的屏幕空间、多 Pass 或运行时依赖进入 diagnostics。

## MaterialSemanticIR 的定位

本次新增的实验草案位于：

```text
experiments/material_semantic_ir/
  material-semantic-ir.schema.json
  examples/silk-stockings-b391.json
  README.md
```

IR 不复用 Blender 节点名称，核心字段包括：

- `source`：材质、Shader、Pass、变体和前端身份；
- `bindings`：材质值、纹理、顶点属性和运行时输入；
- `graph.nodes`：有类型的 DAG；
- `type`：数值宽度、颜色空间等；
- `space`：UV、切线、物体、世界、视图或屏幕空间；
- `evidence`：`exact/recovered/approximated/external/unsupported`；
- `outputs`：surface、baseColor、normal、alpha 等命名输出；
- `diagnostics`：不支持或未绑定内容，不能静默丢弃。

JSON Schema 只验证跨算子的结构。每个 `op` 的输入、输出和参数契约应由后续独立的算子
注册表验证，避免在一个巨大 JSON Schema 中维护所有领域节点分支。

## 与现有代码的最小集成方案

### 阶段 1：不改变生产输出

1. 保留 `shader_variants.py`，为 b391 和面部样本固定选中结果。
2. 在实验目录接入 patched `tree-sitter-hlsl`，输出声明、语句和表达式 JSON。
3. 用现有 `trace_hlsl_dataflow.py` 的结果做回归 oracle，而不是继续扩展其正则。
4. 从指定片元输出建立 typed dependency graph，输出 IR 检查报告，不生成 Blender 节点。

### 阶段 2：绑定和领域折叠

1. 将 `sourceMaterial` 归一化为 IR bindings；不改 `ModelDocument` 现有结构。
2. 首先支持 constant、uniform、texture、sample、swizzle、基础算术、比较、select、dot、
   normalize、clamp、pow 和 lerp。
3. 对 b391 建立 `endfield.silkStockings.v1` 模式；对面部样本建立 SDF/LUT 模式。
4. 未解析节点导致相关输出标记为 incomplete，禁止宣称精确恢复。

### 阶段 3：Blender 后端

1. 新建 IR -> `bpy` 后端，复用 `blender_materials.py` 的版本化 Node Group 习惯。
2. 将 `tools/blender_import_model.py` 收缩为导入和编排层，不再直接堆 Shader 公式。
3. 后端先支持 `preview` 和 `research` 两种模式；复杂纯函数可增加 Cycles OSL 验证后端。
4. MaterialX 导出放在 Blender 后端稳定之后，以免内部 IR 被交换格式能力反向限制。

### 阶段 4：保留原始 SPIR-V

1. 定位 FractalMiner/Shader 导出链中调用 SPIRV-Cross 的位置。
2. 在转换前保存每个 Stage/Blob 的原始 SPIR-V 和资源反射 JSON。
3. 建 SPIRV-Tools sidecar，输出与 HLSL 前端一致的 typed graph 输入。
4. 对同一 b391 变体比较 SPIR-V 与 HLSL 两条路径，确认节点、绑定和输出切片等价。

## 明确不做的事情

- 不把 DXC/Slang 的“编译成功”当成材质恢复成功；
- 不从低层临时变量名猜 PBR 含义；
- 不把 MaterialX 当反编译器；
- 不复制 UnityCsReference 或 ShaderGraph 的受限许可实现；
- 不让 Blender 节点图成为唯一事实源；
- 不用默认颜色、默认灯光或自发光静默替代缺失的运行时输入；
- 不为每个材质展开数千个不可维护节点，公共子图必须折叠为版本化 Node Group。

## 推荐的近期实验

1. fork/pin `tree-sitter-hlsl 0.2.0`，只补 `register(..., space...)` 等已观察缺口；
2. 解析 b391，生成带源位置的声明和表达式树，保证零 ERROR/MISSING；
3. 将 `_15` 的颜色输出反向切片，与 `trace_hlsl_dataflow.py` 的依赖集合比较；
4. 只实现 b391 所需的首批算子，并输出 `MaterialSemanticIR`；
5. 用纯 Python evaluator 对已恢复的纯函数节点做数值测试；
6. 再生成 Blender Node Group，与现有手写丝袜节点做中间值 A/B，而不是只看最终截图。

这条实验能最快判断瓶颈位于语法、绑定、语义模式还是 Blender 表达能力，同时不会把大型
编译器依赖提前固化进项目。

