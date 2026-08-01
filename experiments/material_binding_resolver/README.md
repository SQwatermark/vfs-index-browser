# Material Binding Resolver 原型

本目录验证“Unity Material 实例值 + 指定版本 Shader 定义”能否稳定归一化为材质绑定事实。
它是独立研究原型，不读取或修改 ModelDocument、GLB、Blender 节点和动画数据，也未接入
生产链。

## 解决的问题

Unity Material 只保存实例覆盖值，未覆盖的属性需要回到对应版本 Shader 的默认值。
同时，纹理绑定还包含几个不同来源：

- ShaderLab：属性类型、默认值、Attribute 和 Toggle 关键字；
- Material `sourceMaterial`：纹理资源、Float、Int、Color、UV Scale/Offset；
- Texture 资产元数据：颜色空间和纹理自身的采样设置；
- Shader/HLSL 规则：实际 UV Set 和显式 SamplerState。

Resolver 不根据 `_BaseMap`、`Mask` 等名称猜测语义。无法识别或互相矛盾的数据进入
`diagnostics`，旧 Shader 遗留在 Material 中的属性进入
`unmatchedInstanceProperties`，不会被静默删除。

## 输入边界

### ShaderLab

解析器当前严格支持归档中常见的一行一个属性格式：

- `Float`、`Int`、`Range(min, max)`；
- `Color`、`Vector`；
- `2D`、`Cube`、`3D`；
- 连续 Attribute，例如 `[HDR] [Gamma]`、`[Toggle(KEYWORD)]`。

未知类型、损坏的默认值、重复属性和非预期语法直接抛出 `ShaderLabParseError`。当前不解析
SubShader、Pass、Blend、Stencil 或预处理控制流；这些属于 Shader 程序/变体解析层。

归档版本并不存在于 ShaderLab 文本中，必须通过 `ShaderSource.archive_version` 显式提供，
避免从文件名或当前游戏版本猜测。

### 纹理规则

Material 的 TexEnv 能提供 Scale/Offset，但不能证明 Shader 使用哪个 UV Set 或哪个显式
SamplerState。因此这些信息通过 `texture_rules` 传入：

```json
{
  "_BaseMap": {
    "uvSet": "uv0",
    "sampler": {
      "name": "sampler_LinearMirror",
      "filter": "linear",
      "wrapU": "mirror",
      "wrapV": "mirror"
    }
  }
}
```

有效采样器的优先级是：

1. Shader/HLSL 显式采样器；
2. Texture 资产采样设置；
3. 无法确认，输出 `TEXTURE_SAMPLER_UNRESOLVED`。

两份原始采样信息都会保留，优先级只决定 `sampling.effective`，不会覆盖证据。

## 输出语义

每个数值属性同时保留：

- `default`：当前 Shader 版本声明的默认值；
- `instanceOverride`：Material 是否保存了覆盖值；
- `effective`：最终值以及来自 `shaderDefault` 还是 `materialInstance`。

纹理属性额外保留资源或内置默认纹理、UV Set、Scale/Offset、颜色空间、Shader 采样器、
Texture 采样器及最终采样器。Mask 纹理存在不会自动启用高级丝袜模式；
`_SilkStockingsAdvance` 仍作为独立数值绑定解析。

## 命令行验证

在仓库根目录执行：

```powershell
python -m experiments.material_binding_resolver.resolve_material `
  experiments/material_binding_resolver/examples/character-npr-subset.shader `
  experiments/material_binding_resolver/examples/stockings-material.json `
  --archive-version 1.4.4 `
  --texture-metadata experiments/material_binding_resolver/examples/texture-metadata.json `
  --texture-rules experiments/material_binding_resolver/examples/texture-rules.json
```

运行测试：

```powershell
python -m unittest tests.test_material_binding_resolver
```

## 尚未处理

- Shader Pass、变体选择和 Runtime Keyword；
- 从反编译 HLSL 自动恢复 UV Set 与 SamplerState；
- Shader 默认纹理对应的真实内置资源；
- TextureImporter 与平台覆盖设置；
- Range 值域策略；当前保留真实覆盖值，不擅自截断；
- Binding Resolution 到 MaterialSemanticIR 的转换；
- 任何 Blender 节点生成。

后续若接入，应先让 Shader 变体解析层产生 `texture_rules`，再由适配器把
`ModelDocument.materials[].sourceMaterial` 传入 Resolver。Resolver 本身不应依赖上述
生产结构。

实验性的下游转换现位于
`experiments/material_semantic_ir/character_npr_mapping.py`。二者仍保持单向依赖：
Binding Resolver 不导入 IR，也不知道 CharacterNPR、丝袜或 Blender；语义映射层读取
Resolver 的公开 JSON 结果。这样未来替换 ShaderLab 前端时不需要修改领域规则。
