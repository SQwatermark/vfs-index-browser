# Endfield VFS Index Browser

一个独立的小工具，用浏览逻辑文件目录的方式查看 Endfield VFS 完整索引。

它不会修改 Endaxis，也不依赖 npm。后端使用 Python 标准库提供本地 HTTP 服务，并把 JSONL/TGZ 索引构建成 SQLite，前端按目录懒加载。

工具定位是本地游戏文件浏览器：完整预览和下载依赖本机存在终末地游戏资源，不把 AKEDB 或其他远程 CDN 当作运行时数据源。

## 使用方式

默认读取当前仓库中已经生成的索引：

```powershell
python server.py --rebuild
```

然后打开：

```text
http://127.0.0.1:8765
```

后续已经生成过数据库时：

```powershell
python server.py
```

指定索引或端口：

```powershell
python server.py `
  --index C:\Users\sqwat\Projects\zmd\Endaxis\zmd-research\analysis\database\facts\endfield-vfs-index-20260727-234026.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 127.0.0.1 `
  --port 8765 `
  --rebuild
```

远程主机直接对局域网开放时：

```powershell
python server.py `
  --index D:\Temp\endfield-re\vfs-index\20260727-234026\endfield-vfs-index-20260727-234026.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 0.0.0.0 `
  --port 8765 `
  --rebuild
```

如果只想重建数据库、不启动 HTTP 服务：

```powershell
python server.py --index D:\Temp\endfield-re\vfs-index\20260727-234026\endfield-vfs-index-20260727-234026.jsonl.tgz --db data\endfield-vfs-index.sqlite --rebuild --build-only
```

远程常驻服务可以用 `run-server.cmd` 作为计划任务入口；该脚本会从自身所在目录启动服务。

`.ab` 内部导出依赖 AnimeStudio。默认查找：

```text
D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe
```

如路径不同，可通过环境变量 `ANIMESTUDIO_CLI` 覆盖。

`.pck` 音频预览依赖 `vgmstream-cli.exe` 将 Wwise WEM 转成浏览器可播放的 WAV。默认查找：

```text
vfs-index-browser/tools/vgmstream/vgmstream-cli.exe
```

如路径不同，可通过环境变量 `VGMSTREAM_CLI` 覆盖。内部导出和转码缓存默认在：

```text
vfs-index-browser/data/internal-cache/
```

## 浏览视图

页面提供四个 scope：

| scope | 含义 |
| --- | --- |
| `Effective` | 按 `Persistent -> StreamingAssets` 的静态优先级折叠，每个 `BlockName/fileName` 只显示一个有效候选 |
| `Persistent` | 只看热更目录中的元数据 |
| `StreamingAssets` | 只看底包目录中的元数据 |
| `All Sources` | 顶层按 `Persistent` / `StreamingAssets` 分开，完整展示索引中所有记录 |

目录结构来自索引中的逻辑路径，而不是物理磁盘路径。典型路径形如：

```text
Table/Data/TableCfg/SkillConditionTable.bytes
Audio/Data/Audio/PCK/Windows/Main/default_stream_0.pck
Bundle/Data/Bundles/Windows/main/a53af5bd74e329b80f12a7f4.ab
```

## 预览和下载

点击文件行后，服务会根据索引记录中的物理 chunk 路径、offset 和 length 读取真实文件片段。

- 加密文件会按当前已知的 VFS ChaCha20 规则解密后再预览或下载。
- `.json` / `.lua` / `.md` / `.txt` 等文本文件会显示文本预览。
- 常见图片、视频、音频扩展名会交给浏览器原生预览。
- 其他二进制文件显示前段十六进制内容。
- `.ab` / `.pck` / `.usm` / `.hgmmap` 会识别为二级容器，并提供原始文件下载。
- `.ab` 可以按需切片为临时 AssetBundle，再调用 AnimeStudio 导出 `Texture2D`、`Sprite`、`TextAsset`、`AudioClip`、`VideoClip` 等浏览器可预览文件。
- `.ab` 导出时会额外生成 `AssetMap`，并把导出文件和 Unity 资源身份关联起来。`Container` 是目前最接近“原始 asset 路径”的字段，通常形如 `assets/beyond/.../texture.tga`；`Source` 更接近 AB/CAB 内部源文件，不应当当作资源路径使用。
- `.pck` 会按 AKPK/PCK/BNK 结构解析 WEM 条目，并暴露为虚拟目录：`wem/<前两位 hex>/<wem id>.wem` 和 `wav/<前两位 hex>/<wem id>.wav`。点击 `wem` 会下载解密后的 WEM，点击 `wav` 会按需调用 `vgmstream-cli` 转成 WAV 供浏览器预览。
- `Data/TableCfg/*.bytes` 目前会显示 VFS 解密后的二进制表前段内容；下一步需要接入本地 SparkBuffer 解析器，把它转换为 JSON。
- `.usm` 目前仍只支持原始文件下载，单条视频流需要继续接入对应二级解析器。

## API

```text
GET /api/manifest
GET /api/list?scope=effective&path=Table/Data/TableCfg&page=1&pageSize=100
GET /api/search?scope=effective&q=SkillConditionTable&limit=100
GET /api/preview?id=123
GET /api/raw?id=123
GET /api/raw?id=123&download=1
GET /api/internal/list?id=123
GET /api/internal/preview?id=123&path=Texture2D/example.png
GET /api/internal/raw?id=123&path=Texture2D/example.png
GET /api/internal/list?id=123&path=wem
GET /api/internal/preview?id=123&path=wav/10/269385047.wav
GET /api/internal/raw?id=123&path=wem/10/269385047.wem
```

## 数据库说明

SQLite 数据库默认生成在：

```text
vfs-index-browser/data/endfield-vfs-index.sqlite
```

它是索引的本地派生产物，可以随时通过 `--rebuild` 重新生成。

## 当前限制

VFS 层已经能够定位和读取逻辑文件，`.ab` 已接入按需导出和 AssetMap 元数据，`.pck` 已能按需列出、解密和转码 WEM。图片、角色语音、视频仍可能继续封装在 Unity AssetBundle、Wwise PCK 或 CRI USM 里。要继续完善“内部文件夹浏览”，下一步需要：

1. 移植本地 SparkBuffer 解析，支持 `Data/TableCfg/*.bytes` 转 JSON。
2. 对齐 AnimeStudio 的 AKPK/WEM 解析实现，补全音频包、音效、角色语音和剧情语音索引。
3. 接入 USM 抽流或转码流程，让视频能在浏览器中预览。
4. 解析 `.hgmmap` / bundle manifest，建立资源逻辑路径到 `.ab` 的映射，而不只按包名浏览。
5. 把前端改造成目录树、文件表、固定预览面板的三栏布局，避免文件多时预览区域被挤到页面底部。
