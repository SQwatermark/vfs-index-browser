# 编译 Shader 包桥接层

## 动机

AnimeStudio 已能从终末地正式版 Shader 对象中无损导出 ShaderProgram 记录，并进一步拆出
DXBC 与 SMOL-V 程序片段。此前 `vfs-index-browser` 的材质语义管线只读取归档的
ShaderLab/HLSL 文本，无法稳定消费游戏资源自身的编译程序。

`shader_binary_packages.py` 为两者之间增加一层只读接口。它不反编译程序，也不猜测 Pass、
Stage 或运行时全局关键词，只负责把导出包变成具有稳定身份的数据对象。

## 数据边界

读取器接受 AnimeStudio 生成的 `*.shader.binary/manifest.json`，并保留以下层级：

```text
Shader package
  -> blob
    -> platform + segment
      -> subprogram + keywords + declared program kind
        -> GPU program table snippet
```

文件路径必须位于导出包内部，声明尺寸必须与实际文件一致。未知包版本、未知 GPU 容器编码、
缺失片段和重复 table index 会直接报错，避免后续转换在残缺数据上产生貌似合理的结果。

`programContainerStatus = raw-only` 的记录仍属于有价值的原始证据，但尚不能提供可消费的
程序片段，因此不会出现在 `subprograms` 中。

## 查询语义

`ShaderBinaryPackage.find_subprograms()` 支持三类约束：

- `required_keywords`：候选必须包含；
- `forbidden_keywords`：候选不得包含；
- `declared_program_kind`：例如 `endfield-d3d11` 或 `spir-v`。

查询返回候选集合而不是单个“正确变体”。材质本地关键词、运行时全局关键词、Pass 和 Stage
尚未形成完整映射；在证据不足时强制唯一选择会把错误猜测写进 Blender 材质。

对于 DXBC 片段，桥接层还会严格读取容器的 `SHDR/SHEX` 版本 token，给出
`vertex`、`pixel`、`geometry`、`hull`、`domain` 或 `compute` stage 以及 Shader Model。
这一信息来自程序本身，不依赖片段在终末地自定义二项表中的排列顺序。

## 后续接入

1. 使用真实 CharacterNPR 导出包核对 ShaderLab 归档中的 blob 编号与 subprogram 关键词。
2. 从 DXBC 的 ISGN/OSGN/RDEF 继续反射资源绑定和常量缓冲区布局。
3. 把可验证的资源槽位映射写入材质语义 IR 的 `source` 证据，不直接写 Blender 节点。
4. 只有当 Pass、Stage、关键词和资源绑定均能唯一对应时，才允许 Blender 后端消费该程序。
