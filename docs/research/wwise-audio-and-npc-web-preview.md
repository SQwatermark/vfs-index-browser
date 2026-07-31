# Wwise 音频索引与 NPC 网页预览研究

## 结论

终末地的非对话音频同样可以建立按需读取索引，但它不是一张完整的“原始文件名表”。
目前能够可靠组合四类证据：

1. PCK/AKPK 目录负责定位物理 Bank 和 Media 的偏移、大小与加密信息；
2. SoundBank 的 HIRC 对象负责恢复 Event、Action、容器、Sound、Music 和 Media 之间的关系；
3. TableCfg、关卡 JSON 和 Lua 配置负责补充音乐、音效的游戏语义与部分事件名；
4. AudioDialog 负责提供角色对话语音的逻辑路径。

NPC 网页预览不应产生第二套模型格式。Prefab 和 AvatarMesh 只是两种不同的输入方式，
最终都应转换为现有 `ModelDocument`，再复用同一套 GLB、缓冲区、纹理和 Three.js 预览接口。

## SoundBank 样本验证

### 中文语音 Bank 包

`default_chinese_banks.pck` 包含 1,216 个加密 SoundBank，没有外置 Media 目录。
所有 Bank 解密后都包含 `BKHD` 和 `HIRC`。现有 PCK 解析器此前只收集嵌入式 WEM，
因此把这些纯关系 Bank 静默忽略了。

AudioDialog 中没有目录和扩展名的 `chr_xxxx_*_sv` 条目，其有符号整数键可按
32 位无符号数解释为 Bank/Event ID。例如 `3537164` 对应的 Bank 中可以恢复：

```text
Event 3537164
  -> ActionPlay 515896377
  -> container 958839230
  -> Sound/Media 327131060, 402315396, 513470926
```

因此当前 1,641 个这类未匹配条目并非缺少媒体，而是缺少 HIRC 图解析。

### 主音频 Bank 包

`default_banks.pck` 包含 17,846 个 SoundBank。HIRC 对象至少包括：

| 对象 | 数量 |
| --- | ---: |
| Sound | 134,463 |
| Actor Mixer | 38,634 |
| Random/Sequence Container | 27,873 |
| Action | 24,265 |
| Event | 19,755 |
| Music Track | 50 |
| Music Segment | 43 |
| Music Playlist Container | 24 |
| Music Switch Container | 3 |

音乐对象集中在少数 Bank 中，但媒体流仍由 PCK 物理目录保存。成熟的 Wwise 解析器
`wwiser` 已能读取这些 Bank，并生成包含状态分支、循环、裁剪、分层和播放顺序的
TXTP 描述。这证明当前 HIRC 版本可以正向解析，不需要通过试听猜测关系。

音乐事件不能简化为“一个事件对应一个 WEM”。一个事件可能经过：

```text
Event -> Action -> Music Switch -> Playlist -> Segment -> Track -> Media
```

同一个分支可能同时播放多个 Media，也可能按状态选择不同片段。

## 游戏侧语义来源

以下本地 TableCfg 已确认包含可用于命名和分类的音频信息：

- `SpaceshipMusicTable`：曲目 ID、时长、完整音乐事件名、试听片段事件名；
- `SpaceshipAlbumMusicTable`：专辑与曲目组织；
- `AudioCueTable`：场景音乐和触发行为字符串；
- `AudioCollection`：采集等玩法音效事件名；
- `AudioFactory`、`AudioBattleBuildings`：带字段语义的数值事件引用；
- 关卡音频 JSON 和 Lua：场景、任务、状态切换与事件的上下文。

Wwise ShortID 使用小写事件名的 32 位 FNV-1。样本中：

- `au_music_dijiang_player_map01_lv001_theme` 的 ShortID 与主 PCK Bank ID 一致；
- 对应 `chorus` 试听事件同样可以直接命中 Bank；
- `au_music_hongshan_002_raft_A`、`au_item_flower_collect`、
  `au_item_flower_drop` 均可以从配置名称命中主 Bank。

部分 `trigger` 字符串不会直接命中 Bank，它们可能是游戏逻辑侧的 Cue，而不是 Wwise Event。
索引必须保留证据来源和置信度，不能把所有配置字符串都强行解释为 Event。

当前资源中尚未发现完整的 `SoundbanksInfo.xml/json` 或 `Wwise_IDs.h`。因此：

- 有配置引用的音效和音乐可以恢复可靠的逻辑名称与用途；
- 没有配置引用的对象仍可恢复完整关系，并以稳定数字 ID 浏览；
- 不能保证恢复 Wwise 工程中的全部原始作者命名。

## 建议的音频索引

索引应同时保存物理层、关系层和语义层，不把名称覆盖到物理对象上：

```text
audio_package / audio_bank / audio_media
wwise_object / wwise_relation
audio_semantic_ref
```

建议提供以下虚拟目录：

```text
Audio/
  Dialog/
  Events/
  Banks/
  Media/
  SoundEffects/
  Music/
```

- `Dialog` 继续使用 AudioDialog 的逻辑路径；
- `Events` 展示完整 HIRC 路径和所有候选 Media；
- `Media` 按 Media ID 提供原始 WEM、WAV 预览和下载；
- `SoundEffects` 只收录有配置语义证据的分类视图；
- `Music` 同时提供单个音轨预览和按状态生成的组合预览。

音乐组合播放第一阶段可以由服务端生成 TXTP 等价描述并交给 vgmstream 转码；
后续再考虑浏览器内交互式切换状态。原始 Media 预览和事件组合预览应明确区分。

### 当前实现状态

`Wwise Audio` 网页视图已经实现 `Events / Banks / Media`：

- Event 显示已确认的 HIRC 关系链和所有物理 Media 候选；
- Bank 显示 HIRC 对象类型分布、关系数量和未支持布局诊断；
- Media 按 ID 分桶，可按需预览 WAV、下载 WEM/WAV；
- PCK、Bank、Media 的偏移与加密边界保存在 SQLite，网页不预解码全量媒体。

`SoundEffects / Music` 尚未建立，因为这两个目录必须先接入 TableCfg、Lua 和关卡配置的
语义证据。HIRC 类型只能说明播放结构，不能单独证明游戏内用途或作者命名。

## NPC 网页预览

### 当前能力

Prefab 入口已支持：

```text
/api/manifest-asset/model
/api/manifest-asset/model-glb
/api/manifest-asset/model-buffer
/api/manifest-asset/model-texture
/api/manifest-asset/model-animation
```

AvatarMesh 入口现已支持：

```text
/api/manifest-asset/avatar-plan
/api/manifest-asset/model
/api/manifest-asset/model-glb
/api/manifest-asset/model-buffer
/api/manifest-asset/model-texture
```

文件浏览器中的“资源”按钮展示 AvatarMesh 的 LOD、部件、Mesh、材质、Avatar 和 Bundle
闭包；“3D”按钮使用相同资源计划生成 `ModelDocument` 与 GLB。Andrew LOD0 已完成真实
在线验证，包含 247 个节点、7 个网格、7 个蒙皮、6 个材质和 17 张纹理。

### 接入方式

AvatarMesh `.asset` 的网页预览应按以下流程接入：

1. 调用现有 AvatarMesh 解析器生成指定 LOD 的资源计划；
2. 从 manifest 定位计划中的 Mesh、Avatar、按槽位排序的 Material；
3. 沿依赖闭包定位材质引用的 Texture2D，而不只读取直接 Bundle；
4. 用 AnimeStudio 适配层导出结构化 Mesh、Avatar、Material 和纹理对象；
5. 调用现有 `build_static_avatar_mesh_document()` 生成 `ModelDocument`；
6. 复用现有 ModelDocument、GLB、buffer、texture 和 Three.js 预览 API；
7. 按 AvatarMesh 资产、LOD、形态选择、源文件指纹和工具版本缓存结果；
8. 文件浏览器为可组装的 AvatarMesh 同时显示“3D”和“资源”按钮。

“资源”仍用于诊断缺失部件和依赖，“3D”用于最终预览。服务端应把 Prefab 和 AvatarMesh
实现为两个输入适配器，不能在前端或 API 中维护两套渲染模型。

首个在线样本建议使用已经验证过资源计划的 Andrew 或 Deathgirl LOD0。完成后再处理
包含形态、部件选择或多套外观的 NPC，并把选择结果写入 `asset.assembly` 诊断信息。

## 后续顺序

1. 补全 Switch、Blend 和 Music 对象的结构关系，并继续保留不支持布局的显式诊断；
2. 用 Event 图补齐 AudioDialog 中 Event/Bank 型逻辑条目；
3. 接入 TableCfg、关卡 JSON 和 Lua 的音频语义引用；
4. 建立有证据来源的 `SoundEffects / Music` 分类目录；
5. 实现事件级多轨、随机、循环和状态组合预览；
6. 在共享模型格式上继续补 NPC 形态选择、动画与材质语义。
