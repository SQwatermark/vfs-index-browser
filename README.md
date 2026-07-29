# Endfield VFS Index Browser

本项目是一个独立的《明日方舟：终末地》本地资源浏览器。它读取本机游戏资源索引，不依赖远程 CDN，也不属于 Endaxis。

## 能力

- 按 `Effective`、`Persistent`、`StreamingAssets` 和 `All Sources` 浏览 VFS 逻辑路径。
- 根据索引中的 chunk、offset、length 和 IV 读取并解密单个文件。
- 将 `manifest.hgmmap` 作为虚拟目录，浏览 manifest 中的完整 AssetInfo 逻辑树。
- 按需解析单个 AssetBundle，不预扫描全部 `.ab` 文件。
- 预览文本、图片、音频和视频，并下载原始或转换后的文件。
- 解析 TableCfg/SparkBuffer，并实验性解析 JsonData/MemoryPack 二进制配置。

## 架构原则

资源定位和资源内容解析分为两层：

1. `manifest.hgmmap` 提供逻辑资源路径、所属 Bundle 和大小，是全局目录索引。
2. 用户打开具体资源时，服务才读取对应 `.ab`，调用 AnimeStudio 导出可预览内容。

因此，包含 `.ab` 的普通文件夹不会生成额外虚拟目录，也不需要运行耗时的全量 AnimeStudio 扫描。详细设计见 [docs/design/architecture.md](docs/design/architecture.md)。

## 启动

安装 Python 依赖：

```powershell
python -m pip install brotli
```

首次从 JSONL/TGZ 索引构建 SQLite 并启动：

```powershell
python server.py --rebuild
```

已有 `data/endfield-vfs-index.sqlite` 时直接启动：

```powershell
python server.py
```

默认地址为 `http://127.0.0.1:8765`。局域网访问可添加 `--host 0.0.0.0`。

常用参数：

```powershell
python server.py `
  --index D:\path\to\endfield-vfs-index.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 0.0.0.0 `
  --port 8765 `
  --rebuild
```

`.ab` 按需导出默认调用：

```text
D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe
```

路径可通过 `ANIMESTUDIO_CLI` 覆盖。内部索引和导出缓存位于 `data/internal-cache/`。

## 主要模块

| 路径 | 职责 |
| --- | --- |
| `server.py` | VFS SQLite、HTTP API、文件读取以及各容器适配入口 |
| `manifest_index.py` | HGM manifest 解析、派生 SQLite 缓存和逻辑目录查询 |
| `sparkbuffer.py` | TableCfg/SparkBuffer 解码 |
| `usm.py` | CRI USM 视频处理 |
| `public/` | 无构建步骤的浏览器前端 |
| `tools/` | 格式探测、索引提取和离线解码工具 |
| `schemas/` | MemoryPack 已知类型与 union 映射 |
| `docs/design/` | 当前设计和演进方向 |
| `docs/research/` | 已验证的格式研究记录 |

## API

```text
GET /api/manifest
GET /api/list?scope=effective&path=&page=1&pageSize=100
GET /api/search?scope=effective&q=SkillConditionTable&limit=100
GET /api/preview?id=123
GET /api/raw?id=123&download=1
GET /api/internal/list?id=123&path=assets&page=1&pageSize=100
GET /api/internal/preview?id=123&path=Texture2D/example.png
GET /api/internal/raw?id=123&path=Texture2D/example.png
GET /api/tablecfg/json?id=123
```

对 `.hgmmap` 调用 `internal/list` 时，`path` 是 manifest 内部逻辑目录；响应中的资源通过稳定的 `assetIndex` 标识。对 `.ab`、`.pck` 和 `.usm` 调用同一组 internal API 时，则浏览其按需生成的内部视图。

## 研究工具

- `tools/parse_hgmmap.py`：离线验证 BundleManifest 结构。
- `tools/extract_indexed_file.py`：按 VFS 文件 ID 提取并解密文件。
- `tools/scan_jsondata_formats.py`：批量统计 JsonData 的真实编码格式。
- `tools/probe_binary_json.py`：对单个二进制 JSON 做结构探测。
- `tools/extract_memorypack_schema.py`：从 IL2CPP dump 提取 MemoryPack schema。
- `tools/decode_memorypack_json.py`：使用已知 schema 解码二进制配置。

格式结论和未完成事项以 `docs/research/` 中的文档为准，不应从临时终端输出推断。

## 当前限制

- manifest 已能建立完整路径到 Bundle 的映射，但尚未对所有 Unity 类型提供预览。
- 打开 manifest 资源条目时目前先显示定位信息；将其自动衔接到对应 AB 的导出结果是下一阶段工作。
- MemoryPack 解码仍依赖从当前客户端 IL2CPP 数据提取的 schema，游戏升级后需要重新验证。
- 音频用途、角色模型、任务台本等聚合视图尚未建立。
