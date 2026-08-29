# AudioDialog 本地自动发现

## 目标与边界

`audio_dialog_discovery.py` 只从现有 VFS SQLite 索引中发现构建 AudioDialog
逻辑音频索引所需的输入位置：

- `AudioDialog.bytes` TableCfg；
- 中文、英文、日文、韩文语音的默认 banks、stream 和语言 hotfix PCK 候选。

发现阶段只查询 SQLite 元数据，不读取 chunk，不解析 AKPK，也不扫描或解包 PCK
内容。`audio_dialog_rebuild.py` 消费发现结果，直接读取 effective TableCfg，并通过正式
`AudioPackageIndexService` 为候选 PCK 生成或复用身份校验后的 metadata，再构建临时 SQLite；
显式输入的 `tools/build_audio_dialog_index.py` 仍用于离线分析和复现。

## VFS SQLite 契约

模块依赖现有 `files` 和 `entries` 表：

- `files` 提供 VFS 文件 ID、逻辑路径、chunk 位置、范围、加密状态和建库时可用性；
- `entries(scope = 'effective')` 指明同一逻辑文件在多来源中的生效版本；
- `idx_files_logical`、`idx_files_file_name` 和 `idx_entries_path` 支持小范围查询。

模块会在查询前校验所需列。传入其他 SQLite、旧 schema 或不完整测试库时会明确
报错，不会把空结果误判成“游戏中没有语音”。

## 已确认的路径证据

当前本地 VFS 索引中，AudioDialog 表位于精确逻辑路径：

```text
Table/Data/TableCfg/AudioDialog.bytes
```

四语 PCK 使用以下已观察到的命名形态：

```text
Data/Audio/PCK/Windows/<Language>/default_<language>_banks.pck
Data/Audio/PCK/Windows/<Language>/default_<language>_stream.pck
Data/Audio/PCK/Windows/<Language>/default_<language>_stream_<n>.pck
Data/Audio/PCK/Windows/Hotfix/hotfix_<language>.pck
```

其中 `<Language>` 为 `Chinese`、`English`、`Japanese`、`Korean`，小写语言名必须
与目录一致。发现器只接受这些已经由本地索引证实的形式。`Main`、`Initial`、
`Audit`、`hotfix_main.pck` 以及看似相近但不符合规则的文件会进入
`unclassifiedAudioPcks`，不会被猜测为某种语言。

2026-08-29 的当前本地索引与实际 chunk 状态得到：

| 语言 | banks | stream | hotfix |
| --- | ---: | ---: | ---: |
| 中文 | 1 可读 | 1 可读 | 1 可读 |
| 英文 | 1 缺失 | 1 缺失 | 1 可读 |
| 日文 | 1 可读 | 2 可读 | 1 可读 |
| 韩文 | 1 缺失 | 2 缺失 | 1 可读 |

这说明“已发现”“建库时存在”和“本机当前可读取”必须分开
表达，不能因为 `chunk_exists = 0` 就丢弃候选，也不能仅凭
`chunk_exists = 1` 断言旧路径此刻仍然存在。

## 查询接口

Python 调用：

```python
import sqlite3

from audio_dialog_discovery import discover_audio_dialog_inputs

with sqlite3.connect("data/endfield-vfs-index.sqlite") as conn:
    result = discover_audio_dialog_inputs(conn)
    print(result.preferred_tablecfg)
    print(result.packages["chinese"])
```

命令行摘要：

```powershell
python tools/discover_audio_dialog_inputs.py data/endfield-vfs-index.sqlite
```

机器可读输出：

```powershell
python tools/discover_audio_dialog_inputs.py `
  data/endfield-vfs-index.sqlite `
  --json
```

输出保留所有来源候选，并用 `effective` 标记 VFS 建库时选定的生效版本；
`preferred_tablecfg` 优先遵循 effective 选择，再参考建库时的 chunk 状态。
PCK 按 `banks`、`stream`、`hotfix` 标注角色，但发现器不宣称某个 PCK 一定包含
目标 Media ID，最终仍以 PCK 元数据匹配结果为准。

## 性能策略

发现器使用精确 `logical_id` 查询定位 TableCfg，并只从
`Data/Audio/PCK/Windows/` 路径范围读取 PCK 元数据。命中候选后，再通过有索引的
`entries(scope, path)` 点查询确定 effective 文件。它不会加载全部 44 万条
effective 记录；当前索引上的完整发现查询约为 0.2 秒。

## 自动重建门禁与真实验证

自动构建以语言为隔离单元，必须同时发现 effective 且当前可读的 banks 与至少一个 stream；
hotfix 只作为补充输入，不能单独证明语言已安装。2026-08-29 真实启动自动重建得到中文、日文
各 29,072 条逻辑记录，分别匹配 26,792 与 26,769 条，候选通过 SQLite 完整性、PCK freshness
和 TableCfg 内容身份门禁后原子发布。英文、韩文因主体包缺失被明确跳过。

## 尚需真实游戏验证

1. 在完整安装四语资源的当前游戏版本上确认所有默认包的 `chunk_exists = 1`，并
   检查是否出现新的分片命名形式。
2. 对每个发现到的 PCK 只解析 AKPK 目录元数据，确认 AudioDialog 哈希命中分布；
   特别要确认体积很小的语言 hotfix 包是否为空包或只在更新后承载媒体。
3. 提取 effective `AudioDialog.bytes`，复用现有 TableCfg 解密与 JSON 解码链路，确认
   最新 schema 仍包含严格解析器要求的 `path` 字段。
4. 比较 Persistent 与 StreamingAssets 的同逻辑 TableCfg 内容指纹，确认 effective
   选择与游戏当前覆盖规则一致。
5. 在完整四语安装上验证一次构建可同时发布四种语言，并确认新增分片仍符合严格命名规则。
