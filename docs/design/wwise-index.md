# Wwise 音频索引与虚拟目录

## 目标

这一层把本地游戏 PCK 中的物理 SoundBank、Media 和 HIRC 关系转换为可查询的 SQLite
索引。网页只读取索引元数据；WEM/WAV 在用户预览或下载时才从原 PCK 按范围读取。

它不猜测 Wwise 工程中的作者命名。数字 ID、物理位置、关系图和来自 TableCfg 的语义名称
属于不同证据层，不能互相覆盖。

## 模块边界

- `audio_package.py`：读取 AKPK/PCK 目录，保留 Bank、外置 Media 和 Bank 内嵌 Media。
- `wwise_hirc.py`：切分 SoundBank chunk 与 HIRC 对象，提取有结构证据的关系。
- `wwise_store.py`：保存 Package、Bank、Media、对象、关系与解析诊断，并提供目录查询。
- `tools/build_wwise_index.py`：直接从本地 VFS SQLite 索引读取所有可用 PCK。
- `tools/index_wwise_pck.py`：从已经提取出的单个 PCK 建立或替换索引。
- `wwise_catalog_service.py`：组装 `/api/wwise/list` 虚拟目录与 `/api/wwise/preview` 文档。
- `server.py`：映射 HTTP 错误，并提供 WEM/WAV 物理读取接口。

## 数据模型

```text
wwise_packages
  -> audio_banks
       -> wwise_objects
       -> wwise_relations
       -> wwise_diagnostics
  -> wwise_media
```

`wwise_media.media_id` 使用 16 位十六进制文本保存。外部 Media ID 可能超过 SQLite 的有符号
64 位范围，不能使用 `INTEGER`。HIRC 中的 Event、Action、Sound 等 ID 明确为 32 位无符号值，
仍按 SQLite 整数保存。

已确认的关系包括：

```text
Event -> Action -> Random/Sequence Container -> Sound -> Media
Actor Mixer -> child
```

Switch、Blend 与 Music 对象会完整保存对象边界，但专用分支尚未全部展开。若已知对象类型出现
未支持的布局，解析器保存 `wwise_diagnostics`，不会扫描字节猜关系，也不会丢弃整个 Bank。
SoundBank chunk 或 HIRC 对象边界本身损坏时仍会直接报错。

## 网页虚拟目录

侧栏的 `Wwise Audio` 视图目前包含：

```text
Events/   Event 关系链和可播放 Media 候选
Banks/    Bank 的对象类型、关系数和未解析诊断
Media/    按 Media ID 末字节分桶的 WEM/WAV 预览与下载
```

`AudioDialog` 继续作为独立逻辑路径视图。后续的 `Music`、`SoundEffects` 应以 TableCfg、
关卡配置或 Lua 中的语义引用为来源，不能仅凭 HIRC 对象类型自动命名。

## 构建

从本地游戏与 VFS 索引完整重建：

```powershell
python tools/build_wwise_index.py --reset
```

默认输入和输出分别是：

```text
data/endfield-vfs-index.sqlite
data/wwise-index.sqlite
```

可用 `--vfs-index`、`--output` 改路径，或重复传入 `--pck-file-id` 只更新指定 PCK。
完整扫描时，索引中存在但本机 Chunk 缺失的语言包会明确输出“跳过”诊断；指定 ID 模式则
把缺失源视为错误。

从一个已提取 PCK 更新索引：

```powershell
python tools/index_wwise_pck.py path/to/default_banks.pck `
  data/wwise-index.sqlite --pck-file-id 123
```

## 已验证样本

当前本机安装中 15 个可读 PCK 的完整构建结果包括：

- 19,693 个 Bank；
- 21,705 个 Event；
- 主音频、中文语音、审计、Hotfix 与 Init 的物理 Media；
- 主 Bank 中 144 个、审计 Bank 中 2 个显式未解析关系诊断。

中文 Event `3537164` 已恢复：

```text
Event 3537164
  -> Action 515896377
  -> Random/Sequence Container 958839230
  -> Media 327131060, 402315396, 513470926
```

三项 Media 均能回到 `default_chinese_stream.pck` 的具体条目并按需转换为有效 WAV。

## 尚未完成

- Switch、Blend、Music Segment/Track/Playlist 的完整分支和时间结构；
- TableCfg/Lua/关卡配置到 Event 的语义引用索引；
- `Music` 与 `SoundEffects` 分类目录；
- 事件级组合播放，尤其是多轨、随机、循环和状态切换；
- 未安装英语、日语、韩语资源包时的对应物理 Media。
