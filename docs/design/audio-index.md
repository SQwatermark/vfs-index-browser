# 音频语义索引与虚拟目录

本地 VFS 输入的自动定位规则、只读查询工具和真实游戏验证边界见
[AudioDialog 本地自动发现](audio-dialog-discovery.md)。发现层只查询 SQLite 元数据，
不扫描或解包全部 PCK。

## 目标

音频浏览必须继续以本地游戏文件为唯一资源来源，不依赖 CDN，也不预先解码全部音频。
索引负责回答“这段音频是什么、存在哪里”，读取器只在用户预览或下载时提取内容。

## 已确认事实

- PCK/AKPK 索引能够提供 WEM Media ID、offset、size、语言、Bank 和嵌入位置。
- WEM 可按需解密，并由 vgmstream 按需转成浏览器可播放的 WAV。
- `AudioDialog.json` 包含对话语音的逻辑路径。
- 中文语音的哈希输入为小写 `voice/chinese/<AudioDialog.path>`。
- 已抽样验证 20 条 AudioDialog 路径，计算所得 64 位 ID 与 PCK Media ID 20/20 一致。
- AudioDialog 外层的有符号整数键不是 Media ID。
- 语言 PCK 会把外置语音媒体标记为 Wwise `sfx`；这里的 `sfx` 是媒体分类，
  语种由所属 PCK 提供，因此不能把这些条目排除在 AudioDialog 匹配之外。
- `AudioDialog.path` 中没有目录和扩展名的 `chr_xxxx_*_sv` 条目不是已恢复的
  原始媒体路径，需要后续通过 Wwise Event/Bank 关系定位。
- Bank 中 DIDX 的数字名称是 Wwise 32 位 Media ID。
- HIRC 可恢复 Event、Action、Sound/Container 与 Media ID 的关系。
- 当前游戏资源中未发现完整的 `SoundbanksInfo.xml/json` 或 `Wwise_IDs.h`。

因此，对话语音可以恢复可靠逻辑名；其他音频目前只能恢复关系和用途线索，不能保证原始
作者文件名。

## 索引模型

当前由 `audio_dialog_index.py` 负责严格解析、路径哈希和匹配，由
`audio_dialog_store.py` 负责 SQLite 持久化与逻辑目录查询。索引使用以下三组核心表；
后续 Wwise 关系图再单独扩展。

现有 PCK 解析器生成的 `audio_meta.json` 不需要重复解析：
`media_entries_from_audio_package_meta()` 会校验元数据版本、条目数和必需字段，再转换为
统一物理媒体记录。PCK 文件 ID 保留在每条记录中，供后续预览 API 回到原 VFS 文件。

### `audio_media`

记录可读取的物理媒体：

```text
language
media_id
pck_file_id
offset
size
bank_id?
bank_offset?
bank_size?
bank_wem_offset?
encrypted
```

唯一性不能只依赖 `media_id`。不同语言包或 PCK 可能复用数字 ID，应以语言和物理来源
共同判定。

### `audio_dialog`

记录可靠逻辑身份：

```text
language
logical_path
normalized_hash_input
media_id
dialog_key?
speaker_or_context?
match_status
```

`match_status` 至少区分：

- `matched`：唯一命中物理媒体；
- `missing`：配置存在但 PCK 中未找到；
- `ambiguous`：命中多个不能安全选择的媒体；
- `collision`：不同逻辑路径产生同一哈希且无法证明等价。

同一 Media ID 被多个逻辑路径引用是允许的，不能用唯一文件名覆盖关系。

`audio_dialog_media` 保存逻辑条目与全部物理候选之间的有序多对多关系。不能只在
`audio_dialog` 上保存一个 PCK 位置，否则 `ambiguous` 会在持久化时丢失证据。

`audio_dialog_directories` 是由逻辑路径派生的目录统计表，保存直接父目录以及各匹配状态
数量。分页查询只读取当前目录；目录内容不需要在请求时扫描全部逻辑路径。

### `wwise_relation`

记录其他音频的语义图：

```text
source_kind
source_id
relation
target_kind
target_id
bank_id
confidence
evidence
```

典型边为 `event -> action -> sound/container -> media`。分类和显示名称属于上层派生字段，
不得覆盖 Media ID。

## AudioDialog 虚拟目录

AudioDialog 目录按以下层级浏览：

```text
AudioDialog/
  chinese/
    <AudioDialog.path 的目录>
      <文件名>.wem
```

页面上的同一叶节点同时提供：

- 浏览器播放：按需解密 WEM，并按需转码 WAV；
- 下载原始媒体：解密后的 `.wem`；
- 下载通用音频：缓存后的 `.wav`；
- 元数据：语言、逻辑路径、Media ID、PCK、offset、size 和匹配状态。

目录是数据库中的逻辑视图，不复制媒体文件。WAV 缓存键应包含：

- PCK 文件身份；
- Media ID、offset 和 size；
- 解密/转码实现版本；
- vgmstream 工具身份。

这样游戏更新、索引变化或转码器升级后不会误用旧缓存。

## API 建议

虚拟目录应尽量复用普通目录响应结构。首轮可采用独立 API，稳定后再挂入统一资源树：

```text
GET /api/audio-dialog/list?language=chinese&path=&page=1&pageSize=100
GET /api/audio-dialog/preview?language=chinese&path=...
GET /api/audio-dialog/raw?language=chinese&path=...&format=wem
GET /api/audio-dialog/raw?language=chinese&path=...&format=wav
```

服务端解析逻辑路径后，从 `audio_dialog` 找 Media ID，再从 `audio_media` 定位 PCK 物理
条目。不得根据路径重新扫描所有 PCK。

## 其他音频

其他音频先提供以下虚拟入口：

```text
Wwise/
  banks/
  events/
  media/
  categories/
```

- `banks/` 按 SoundBank 查看其 Event、容器和 Media；
- `events/` 展开 Event 到 Action、Sound/Container 和 Media 的完整关系；
- `media/` 继续以 `<mediaId>.wem` 为稳定叶节点；
- `categories/` 只展示有证据的用途分类，并显示置信度。

不要把 Media 直接重命名为第一个 Event。一个 Event 可能随机选择多个 Media，一个 Media
也可能被多个 Event、容器或语言上下文复用。

音乐可以在发现 Music Segment、Playlist Container、Switch Container 和 State/Switch
关系后建立专用聚合视图。环境音和战斗 SFX 同理；在关系不完整前保留数字身份比生成错误
名称更可靠。

## 实现阶段

### 阶段一：AudioDialog

1. 读取本地 TableCfg 的 AudioDialog。
2. 计算语言前缀和路径哈希。（已完成）
3. 与现有 PCK 索引连接并写入 SQLite。（已完成显式 PCK 元数据适配与构建 CLI）
4. 实现目录分页、预览和下载 API，并接入浏览器固定预览区。（已完成）
5. 增加空目录、重复引用、缺失媒体和哈希冲突测试。
6. 在页面上使用固定预览区，避免长目录把预览推到页面底部。（已完成）

当前实现会严格区分 `matched`、`missing`、`ambiguous` 和 `collision`，无符号 64 位
Media ID 使用固定 16 位十六进制文本保存，避免 SQLite 有符号整数溢出。索引 schema
不做隐式迁移；遇到未知版本会明确要求重建。

已有导出数据可以直接构建索引：

```powershell
python tools/build_audio_dialog_index.py `
  path/to/AudioDialog.json `
  data/audio-dialog-index.sqlite `
  --language chinese `
  --package 832795=path/to/chinese-banks/audio_meta.json `
  --package 832796=path/to/chinese-stream/audio_meta.json
```

重复运行会只替换指定语言，保留数据库中的其他语言；`--reset` 才会删除整个旧库。当前
CLI 接受显式输入，下一步由本地游戏发现层自动定位 AudioDialog TableCfg 和对应语言 PCK，
但不会改变哈希、匹配、SQLite 或 HTTP 接口。

预览和下载不会重新按 Media ID 选择第一个条目，而是使用索引中保存的 PCK 文件 ID、
offset、size 和 Bank 位置读取唯一候选。同一 Media ID 在不同物理位置出现时使用不同缓存
身份；缓存键同时包含 PCK 内容指纹、解析实现版本和 vgmstream 文件身份。

### 阶段二：Wwise 关系图

1. 从 Bank 解析 DIDX 和 HIRC。
2. 保留所有 Event/Action/Container/Media 边。
3. 给每条推导关系记录证据和置信度。
4. 实现 Bank、Event 和 Media 三种浏览入口。

### 阶段三：聚合语义

1. 将任务台本、角色、场景配置中的音频引用连接到语义图。
2. 建立角色语音、任务语音、音乐和 SFX 聚合视图。
3. 提供 API 导出索引与单个媒体，不默认批量解码全部音频。

## 验收标准

- 给定 AudioDialog 逻辑路径，可以不扫描 PCK 直接定位并播放对应音频；
- 目录分页只读取当前层元数据；
- WEM 与 WAV 数量相同是同一媒体的原始和派生视图，不重复计为两份资源；
- 缺失、歧义和冲突均可见；
- 其他音频在没有可靠名称时保持 Media ID，不制造虚假文件名。
