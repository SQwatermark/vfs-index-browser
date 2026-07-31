# NPC Avatar 模型恢复

本文记录普通 NPC 模型与可玩干员 prefab 的结构差异，并以 Deathgirl 为样本说明恢复流程。

## 结论

### 资源身份不能由 `m_Name` 单独决定

- 独立 `.asset`、`.mat` 以 AssetBundle container 逻辑路径作为身份。
- `fbx##子对象` 先以 FBX container 定位，再以子对象名消歧。
- Unity 对象的 `m_Name` 只是内部对象名，可能与逻辑文件名不同。例如
  `m_actor_bounda_body_01.mat` 内部仍可名为 `M_actor_chen_body_01`。
- 模型装配使用逻辑名称维持稳定引用，同时保留 `m_Name` 作为显示名。

### 非蒙皮父骨骼可能没有唯一 bind pose

多个实际蒙皮子骨骼可以反推出同一个未蒙皮父骨骼，但候选矩阵并不总是一致。
装配器会先汇总全部候选：一致时使用反推结果；不一致时以 Avatar 默认姿态补齐
该父节点，并写入 `NPC_SKELETON_BIND_AMBIGUOUS` 诊断。直接参与蒙皮的骨骼仍以
Mesh bind pose 为权威值，因此歧义不会阻止静态模型预览，也不会被静默掩盖。

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

## Manifest 资源计划

`npc_avatar_resources.py` 在配置解析和 Unity 对象提取之间建立了可独立验证的资源边界：

1. 按指定 LOD 遍历所有 AvatarMesh 槽位；
2. 将路径哈希解析出的候选路径与 manifest 做大小写不敏感的精确匹配；
3. 为每个 Mesh 保留有序且允许重复的材质槽；
4. 从所选 Mesh 的 `FBX##子资产` 路径推导同源 Avatar；
5. 输出精确资产、Bundle 列表和根骨名称，供后续按需提取。

候选路径可能来自哈希碰撞。只有最终恰好命中一个 manifest 资产时才视为成功；缺失、重复资产、多个 FBX 来源或无法推导 Avatar 都会明确报错。调用方也可以显式传入 Avatar 路径，但不会静默选择候选项。

远程本地游戏环境已通过 `/api/manifest-asset/avatar-plan` 完成三个 LOD0 样本验证：

| 样本 | 类型 | Mesh | 直接 Bundle | 未解析引用 |
| --- | --- | ---: | ---: | ---: |
| Andrew | 通用 NPC | 7 | 13 | 0 |
| Deepfin | 可操控角色 | 10 | 18 | 0 |
| Pelica | 可操控角色 | 12 | 21 | 0 |

Andrew 的眼镜部件挂接到 `glass:glass_jnt`；Deepfin 与 Pelica 还覆盖了脊柱、头部和衣物控制骨。三个样本均从 FBX 子资产唯一推导出 Avatar，说明 NPC 与可操控角色可以复用同一资源计划。首次请求因 manifest schema 升级重建派生索引约耗时 59 秒，缓存后另外两个样本各约 0.5 秒。

浏览器会在 AvatarMesh 资产行显示“资源”按钮，并将计划渲染为完整性摘要、Avatar、LOD 部件和直接 Bundle 四部分。也可以使用查询参数直接打开指定样本：

```text
http://HOST:8765/?avatarPlanManifestId=451359&avatarPlanAssetIndex=157272&lod=0
```

人工验证时应检查：页面状态是否为“引用完整”，部件数是否与目标 LOD 一致，每个部件是否都有 Mesh、材质和根骨，以及直接 Bundle 数是否合理。未解析 Mesh 或 Avatar 会以错误状态显示；停用部件与 `mainPrefabHash` 未还原则作为独立提示，不会伪装成完整模型导出失败。

AvatarMesh 已进一步接入统一网页模型预览。资产行同时显示“3D”和“资源”：前者按指定
LOD 展开 Bundle 传递依赖，导出精确 Mesh、Material、Avatar 和 Texture2D，再生成现有
`ModelDocument` 与 GLB；后者继续用于检查装配计划。Andrew LOD0 真实样本已验证为
247 个节点、7 个网格、7 个蒙皮、6 个材质和 17 张纹理，浏览器 Three.js 视口能够正常
显示完整着色模型。直接打开方式为：

```text
http://HOST:8765/?modelManifestId=451359&modelAssetIndex=157272&lod=0
```

对象选择不能依赖 `m_Name`。Andrew 的独立 `.asset` Mesh 与 FBX 子对象存在同名项；
在线提取会使用资源计划中的逻辑路径与 AnimeStudio 的 `container` 身份进行精确匹配，
避免从依赖 Bundle 中误选同名对象。

资源计划列出的 Bundle 只表示 Mesh、Material 与 Avatar 的直接存储位置。实际对象导出还必须加入这些 Bundle 的传递依赖闭包，材质引用的 Texture2D、Shader 等资源可能位于依赖 Bundle；不能把“直接资源已唯一定位”误解为“模型所需输入已经全部闭合”。

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

几何、骨架、引用关系和 LOD0 在线预览已经闭合。后续工作集中在：

1. 为网页增加 AvatarMesh LOD 和外观部件选择；
2. 验证现有 AnimationClip 能否直接按骨骼路径映射到 NPC 完整层级；
3. 将 NPC 材质参数映射到现有近似 Shader；
4. 用更多通用 NPC、可操控角色与同名资源样本扩展回归测试。
