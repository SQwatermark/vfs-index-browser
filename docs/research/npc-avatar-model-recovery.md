# NPC Avatar 模型恢复

本文记录普通 NPC 模型与可玩干员 prefab 的结构差异，并以 Deathgirl 为样本说明恢复流程。

## 结论

`assets/beyond/dynamicassets/gameplay/actors/postmodels/npc/` 主要保存 `chr_*` 可玩干员在 NPC 场景中的 postmodel，并不是普通 NPC 的统一入口。

Deathgirl 的入口是：

```text
assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/data_npc_avatarmesh_deathgirl.asset
```

该 MonoBehaviour 声明了编辑器 prefab 路径：

```text
Assets/Beyond/Designer/PostModels/Npcs/npc_8001_deathgirl_postmodel.prefab
```

但这个 prefab 没有作为 Manifest 中可直接提取的独立资产存在。运行时通过 AvatarMesh 数据选择网格、材质、LOD 和挂接骨骼，再与骨架 Avatar 组合。因此，现有以 `GameObject + Transform` prefab 为入口的模型恢复流程不能直接处理它。

## 数据链

```text
data_npc_avatarmesh_deathgirl.asset
  ├─ partSubMeshsLOD0..3
  │   ├─ meshPathHash
  │   ├─ materialPathHashes
  │   ├─ rootBoneName / rootBoneID
  │   └─ 平台与渲染开关
  ├─ StringPathHash.bin
  │   └─ 运行时哈希 -> Mesh/Material 逻辑路径
  └─ sk_npc_major_deathgirl_01.fbx
      ├─ Avatar：骨架层级、默认姿态、骨骼路径哈希
      └─ Mesh：顶点、索引、UV、法线、蒙皮权重、绑定姿态
```

逻辑 prefab 路径用于描述原始编辑器组织方式；真正可恢复的运行时模型由后三部分闭合。

## Deathgirl 样本

AvatarMesh 中只有一个 slot，名称为 `npc_8001_deathgirl_postmodel`，`partType = 2`。

| LOD | 网格数 | 组成 |
| --- | ---: | --- |
| 0 | 9 | body、brow、cloth_03、cloth_04、eyeshadow、face、hair、hairshadow、iris |
| 1 | 9 | 与 LOD0 同类，切换为低精度网格和部分 LOD 材质 |
| 2 | 7 | 移除 eyeshadow 与 hairshadow |
| 3 | 7 | 与 LOD2 同类 |

共解析 32 个网格引用。所有 `meshPathHash`、`materialPathHashes` 与备用材质哈希均可通过 `StringPathHash.bin` 还原，没有未解析引用。

LOD0 的典型引用包括：

```text
S_npc_major_deathgirl_body_01_lod0
  Mesh: Assets/Beyond/Arts/Entity/NPC/Major/Girl/Deathgirl/Models/
        S_npc_major_deathgirl_body_01_lod0.asset
  Material: .../Deathgirl/Materials/M_npc_major_deathgirl_body_01.mat

S_npc_major_deathgirl_face_01_lod0
  Mesh: .../Deathgirl/Models/S_npc_major_deathgirl_face_01_lod0.asset
  Material: .../Deathgirl/Materials/M_npc_major_deathgirl_face_01.mat

S_npc_major_deathgirl_hair_01_lod0
  Mesh: .../Deathgirl/Models/S_npc_major_deathgirl_hair_01_lod0.asset
  Material: .../Misc/Death/Materials/M_npc_major_death_hair_01.mat
```

部分网格以独立 `.asset` 出现，部分以
`sk_npc_major_deathgirl_01.fbx##子资产名` 出现。两种形式最终都指向 AnimeStudio 可导出的 Mesh 对象。

## 骨架与蒙皮验证

对 Deathgirl 骨架 bundle 进行 AnimeStudio 对象导出后得到：

- 1 个 Avatar；
- 32 个 Mesh；
- 0 个 GameObject；
- 0 个 Transform。

Avatar 含 338 个骨架节点、338 份 Avatar pose、338 份默认 pose，以及 338 条骨骼哈希到完整路径的 `m_TOS` 映射。

每个 Mesh 均含顶点、法线、UV0、索引、蒙皮权重、骨骼哈希和 bind pose。样本内每个 Mesh 的骨骼哈希都能在 Avatar 的 `m_TOS` 中解析到唯一完整骨骼路径。这证明缺少 prefab 并不意味着缺少模型恢复所需的骨架信息。

## StringPathHash.bin

`StringPathHash.bin` 是运行时 64 位路径哈希到逻辑资产路径的映射表。当前样本格式为：

```text
Header
  uint32 stringBase
  uint32 count
Hash slots
  count * 8 bytes
Mapping records
  count * 16 bytes
  int64 hash
  int32 stringOffset
  int32 reserved = 0
String region
  int32 utf16ByteLength
  byte[utf16ByteLength] utf16LeText
```

映射记录区应恰好结束于 `stringBase`。同一哈希理论上可以映射到多个路径，因此读取接口返回路径元组而不是任意选择一个结果。

可使用以下命令检查 AvatarMesh：

```powershell
python tools/inspect_npc_avatar_mesh.py `
  path/to/data_npc_avatarmesh_deathgirl.txt `
  --string-path-hash path/to/StringPathHash.bin `
  --output deathgirl-avatar-mesh.json
```

工具会严格检查数组声明长度、必要字段和二进制边界。无法解析的网格或材质引用会显式保留为空路径，并计入 `summary.unresolvedReferenceCount`。

## 已实现的预览装配

`tools/build_npc_avatar_preview.py` 已提供不依赖 GameObject prefab 的装配路线：

1. 按 AvatarMesh 选择一个 LOD；
2. 从 AnimeStudio Mesh JSON 加载对应网格；
3. 复用现有 `attach_mesh_geometry()` 生成 ModelDocument 几何；
4. 在公共根节点上将 NPC Mesh 的 Z-up 转换为 glTF 的 Y-up；
5. 可选读取 Avatar，为每个 Mesh 写入 skin 与 inverse bind matrices；
6. 生成可由现有 Three.js 预览器读取的 GLB。

Deathgirl LOD0 实测结果：

- 9 个 Mesh 节点；
- 338 个完整 Avatar 骨架节点；
- 9 个 skin；
- 生成的 GLB 约 3.36 MB；
- 浏览器中模型直立，九个部件对齐；
- 加入 skin 前后轮廓一致，控制台没有 glTF 或 Three.js 警告。
- 对全部 skin 验证 `inverse(meshWorld) × jointWorld × inverseBindMatrix`，
  相对单位矩阵的最大误差约为 `3.7e-7`。

裸 Mesh 本身使用 Z 轴向上，因此不能逐个修改顶点。适配器在所有网格和骨骼共同的根节点施加 `-90° X` 旋转，既满足 glTF 的 Y-up 约定，也不会破坏后续动画坐标。

```powershell
python tools/build_npc_avatar_preview.py `
  deathgirl-avatar-mesh.json `
  path/to/animestudio/Mesh `
  deathgirl-lod0.glb `
  --avatar path/to/SK_npc_major_deathgirl_01Avatar.json `
  --model-document deathgirl-lod0-model.json
```

## 骨架层级恢复

Deathgirl LOD0 的九个 Mesh 共使用 254 根实际蒙皮骨骼，其中 23 根被多个 Mesh 共用。同一骨骼在不同 Mesh 中的 bind pose 完全一致。

Avatar 默认姿态与 Mesh 绑定姿态的大部分局部变换相同，但手臂和手指存在明显差异，因此不能把 Avatar 默认姿态直接作为最终绑定姿态。当前恢复规则是：

1. 以 254 根 Mesh bind pose 的逆矩阵作为权威世界变换；
2. 使用 Avatar 的父节点索引恢复完整层级；
3. 对未参与蒙皮的节点，用 Avatar 默认局部姿态向上或向下补齐世界变换；
4. 再由父子世界矩阵反算每个节点的局部 TRS。

直接缺失的祖先只有 5 个。`face_Head` 可以从 70 个已知子骨骼分别反推，候选世界矩阵最大差异约 `5e-6`，说明补齐过程数值稳定。

## 下一步

几何、骨架和引用关系已经闭合。后续工作集中在：

1. 将 AvatarMesh 装配器接入服务器的 Manifest 资产预览流程；
2. 按解析出的 Material 路径加载材质和纹理；
3. 验证现有 AnimationClip 能否直接按骨骼路径映射到 NPC 完整层级；
4. 将 NPC 材质参数映射到现有近似 Shader。
