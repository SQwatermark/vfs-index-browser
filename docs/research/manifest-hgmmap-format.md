# Bundle Manifest (`manifest.hgmmap`) 格式笔记

## 当前结论

`Persistent/BundleManifest/Data/Bundles/Windows/manifest.hgmmap` 的 VFS 内容先按
VFS ChaCha20 规则解密，再整体进行 Brotli 解压。当前样本解压后为 137,818,624
字节，可以完整恢复 Bundle 与 AssetInfo 两类索引。

解析脚本：

```powershell
python tools/parse_hgmmap.py `
  --decompressed data/manifest.decompressed.bin `
  --include-assets `
  --output data/reports/manifest.json
```

也可以用 `--id` 直接从 VFS SQLite 索引读取和解密原文件。

## 文件头

- `HEAD1`: `0xFF11FF11`
- 版本字符串：当前为 `5f521eb8-5202-dcdf-2412-02d992d0d771`
- `HEAD2`: `0xF1F2F3F4`
- Manifest hash
- Perforce CL 字符串
- 当前样本正文起点：`0x9c`

## AssetInfo

当前共有 327,584 条记录，每条固定 24 字节：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `pathHashHead` | `int64` | 逻辑资源路径的查询哈希 |
| `path` | `uint32` | 相对数据区的 `RefCompressString` 偏移 |
| `bundleIndex` | `int32` | 所属 Bundle 索引 |
| `assetSize` | `int32` | 资源大小；部分资源允许为 0 |
| padding | `uint32` | 固定为 0 |

`RefCompressString` 的内容为：

```text
uint32 compressedByteCount
byte[compressedByteCount] brotliPayload
```

Brotli 解压结果是 UTF-16LE 路径，例如：

```text
assets/beyond/arts/effects/commonassets/arts/t_texture/mask/graph/t_fx_graph_03_m.png
```

路径可能带 `##subAssetName`，表示 Unity 资源中的子资源。

## Bundle

当前共有 237,800 条记录，每条固定 48 字节：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `bundleIndex` | `int32` | Bundle 索引 |
| `name` | `uint32` | `RefString` 偏移 |
| `dependencies` | `uint32` | 完整依赖索引数组 |
| `directReverseDependencies` | `uint32` | 直接反向依赖索引数组 |
| `directDependencies` | `uint32` | 直接依赖索引数组 |
| `bundleFlags` | `int32` | 当前样本全部为 0 |
| `hashName` | `int64` | Bundle 名称查询哈希 |
| `hashVersion` | `int64` | Bundle 版本/内容哈希 |
| `category` | `int32` | 根资源分类 |
| padding | `int32` | 固定为 0 |

固定 Bundle 数组结束后有 4 字节哨兵，紧随其后是引用数据区。所有 Bundle
引用都相对该数据区，而不是相对引用字段本身。

`RefString`：

```text
int32 utf16ByteCount
byte[utf16ByteCount] utf16LeString
```

`RefArray<int>`：

```text
int32 count
int32[count] values
```

## 全量验证

当前样本已经完成以下检查：

- 237,800 个 Bundle 名称全部能解码为 `main/*.ab` 或 `initial/*.ab`。
- 三类依赖数组共解析出 2,135,314 个索引，全部处于 Bundle 范围内。
- 327,584 个 AssetInfo 路径全部成功 Brotli 解压并通过 UTF-16LE 解码。
- 所有 AssetInfo 的 `bundleIndex`、`assetSize` 和 padding 均通过边界检查。
- 现有 VFS 索引中可以按 Bundle 名称找到对应 `.ab`；通常同时存在
  `StreamingAssets` 和 `Persistent` 两份，由 Effective 规则选择实际版本。

## 下一步

SQLite 派生索引现在保存三类 Bundle 依赖，并提供直接依赖与传递依赖闭包查询。浏览服务的模型层级快照接口已使用该闭包暂存依赖 AB，并通过 AnimeStudio CABMap 解析跨 Bundle PPtr。

佩丽卡 `chr_0004_pelica_postmodel.prefab` 的真实验证得到 64 个传递依赖 Bundle，全部可以从 Effective VFS 定位。扩大对象导出类型并排除 GameObject 的便利镜像字段后，恢复出 454 个层级节点和 3532 条去重引用边，仅剩 35 条脚本组件引用目标未进入快照。这确认当前闭包方向能够支撑 Prefab 层级、静态几何、骨架、蒙皮、材质、纹理和 LOD 恢复；依赖 Bundle 已加载仍不等于所有 Unity 对象均已导出。

Texture2D 不进入主 JSON 对象快照，而是在解析 Material 后按实际引用名称执行第二次过滤导出。佩丽卡样本只导出 37 个实际引用纹理，而不是扫描并转换 64 个依赖 Bundle 中的全部纹理。

当前 ModelDocument 状态为 `texturedSkinnedModel`。GLB 导出器按显式 `LODGroup` 选择 LOD0，并排除未归组的 `shadowProxyDesktop` Renderer，再继续裁剪未使用的 Mesh、Skin、Accessor、Material 和纹理；佩丽卡 v24 预览 GLB 为 12 个 Mesh、12 个 Skin 和 23 张嵌入图片。图片除标准 PBR 槽位外，还包含由 `extras.endfieldPreview` 标识的 Ramp、SDF、高光和丝袜专用贴图。
