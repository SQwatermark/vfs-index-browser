# ProjectileComponentData 本地解析链

## 结论

> 产品化状态（2026-08-25）：下述聚焦解码能力曾在部署主机验证，但其实现目前只存在于
> 被 Git 忽略的研究副本提交 `03336c4` 中，并叠加了 `Exporter.cs` 的 85 行未提交修正。
> 权威 AnimeStudio 提交 `8cdec963` 的通用 `JSON` 只导出 MonoBehaviour 外壳，`Convert`
> 也没有恢复 managed-reference 内部数据。新 `Vfs.UnityWorker 0.3.0` 已将可证明的聚焦
> 解码迁入 `Endfield.Extensions`，并声明 `decodeProjectileComponent`；该操作明确输出
> partial 组件，不再依赖旧通用 JSON。
> 这一区别是可复现性边界，不否定下述 manifest 身份链和历史样本结论。
>
> 后续产品化更新（2026-08-25）：`Endfield.Extensions` 已随嵌入式 Unity worker 合入当前
> 工作树并完成真实服务验证。`GameplayTagQuery.tags[]` 按同版本 MemoryPack 生成包装器
> 证据读取为 raw int32 `tagId`，不再虚构并消费字符串 path；这修复了莱万汀
> `projectile_chr_0016_laevat_attack_4_2` 与 `projectile_chr_0016_laevat_attack_5` 的
> 前缀错位。两份资源均经 `/api/akedb-compatible/ProjectileData/<id>.json` 返回 200，
> 且解码 ID 与请求严格一致。序列化组件保留原始大小写时，ID 核验使用大小写不敏感的
> ordinal 比较，与 manifest 路径的大小写不敏感身份规则保持一致。
>
> 新 worker 当前已恢复 managed-reference registry 和 Projectile 固定前缀。汤汤三段攻击
> 样本中组件 payload 为 `[1560, 4988)`；前缀精确消费到 2032，余下 2956 字节的
> `moveModeDict` 已继续消费至 2548，主特效结束条件消费至 2564。当前还剩 2424 字节的
> 特效、声音和最终距离/倍率尾部保留为 Raw words。公开 capability 已可提供这一诚实的
> partial 结果，但不能把 Raw tail 解释成已完成语义化。

当前 manifest 和 VFS 索引已经包含投射物配置，不需要从 AKEDB 或其他远程服务补数据。
`ProjectileComponentData` 不是 JsonData 中以 projectileId 命名的文件，而是 Unity
`data_<projectileId>.asset` 对象中的 managed reference。稳定身份链为：

```text
projectileId
  -> Assets/Beyond/DynamicAssets/GameData/Projectile/data_<projectileId>.asset
  -> manifest assetIndex + bundleName
  -> VFS Data/Bundles/Windows/<bundleName>
  -> exact-container MonoBehaviour JSON export
  -> layout == Beyond.Gameplay.Core.ProjectileComponentData
```

因此 `/api/search` 不是合适入口：它只查询一级 VFS 逻辑文件，而 projectile 的逻辑名位于
`manifest.hgmmap` 的 Unity AssetInfo 树中。新接口 `GET /api/projectile?projectileId=...`
直接执行上述精确链路，全程不依赖当前返回 502 的远程服务。

## 本地证据

研究使用仓库本地 `data/manifest.decompressed.bin`：

- 大小：`137818624` bytes；
- SHA-256：`c6a22af9de87f21e9da4dcac93a39dad487e745245c4e9ded80d77a1cd3f72ec`；
- manifest version：`5f521eb8-5202-dcdf-2412-02d992d0d771`；
- manifest hash：`d8a7b49e0f157b6793ac5f7ac77c8da0`；
- `327584` 个 asset、`237800` 个 bundle；
- 路径含 `projectile` 的 asset 共 `374` 个，其中包含 ProjectileTable 和投射物对象。

庄方易的两个 AKEDB projectileId 均为唯一精确命中：

| projectileId | assetIndex | asset pathHash | bundle | asset size |
| --- | ---: | --- | --- | ---: |
| `projectile_chr_0030_zhuangfy_attack_sword_1` | `149277` | `0c163ed620c34550` | `main/1c6d472666ce218a2b47735d.ab` | `521` |
| `projectile_chr_0030_zhuangfy_attack_sword_2` | `139627` | `0c167206a63c695f` | `main/7cac29f65e7a54cbee1c8352.ab` | `521` |

主 VFS SQLite 同时记录了两个 AB 的 Persistent 和 StreamingAssets 条目。索引生成时可用的
StreamingAssets 记录分别为 `file id 175769`（26813 bytes）与 `file id 175768`
（26783 bytes），说明 manifest 身份和承载 Unity 对象的 VFS 容器均已进入索引。

IL2CPP 字符串证据还给出运行时常量：

- `Beyond.PathDef::PROJECTILE_DATA_FOLDER = "Assets/Beyond/DynamicAssets/GameData/Projectile/"`；
- `Beyond.Gameplay.DataConst::PROJECTILE_TABLE = "ProjectileTable"`。

这与 manifest 的实际目录和 `projectiletable.asset` 完全一致。

## API 与解析策略

接口只接受小写 ASCII 字母、数字和下划线组成的 projectileId，先构造完整路径再调用
manifest 的大小写不敏感精确查询。它不会使用 `LIKE`、子串命中或“最接近”回退；零命中
返回 404，多命中作为索引歧义返回 422。

定位后只读取一个目标 AB，并使用能进入终末地 managed-reference 专用解码器的 JSON 模式
导出其中的 MonoBehaviour。AnimeStudio 当前的 container 过滤在这类独立 projectile AB 上会
错误排除目标，因此接口不依赖该过滤器；选择器会递归查找
`layout == Beyond.Gameplay.Core.ProjectileComponentData`，要求其中的 `id` 与请求相等且结果唯一。
导出器产生 JSON 或旧式 `.txt` JSON 时均可读取，并同时返回：

- `source`：manifest、assetIndex、pathHash、bundle 和导出 JSON pointer；
- `projectileComponentData`：聚焦 managed-reference 数据；
- `unityObject`：拥有该 managed reference 的完整 Unity MonoBehaviour JSON；
- `decode`：完整度和 ID 校验状态。

manifest 派生 SQLite 和 MonoBehaviour 导出都按需缓存。MonoBehaviour 缓存身份包含 VFS
record、chunk 修改时间、assetIndex、asset path，以及 AnimeStudio EXE/CLI DLL/核心 DLL
身份；游戏资源或解码器重建后不会误用旧结果。

## 解码边界

旧研究工作树中的本地定制 AnimeStudio 包含
`Beyond.Gameplay.Core.ProjectileComponentData` 的聚焦解码器。
当前已按 IL2CPP 字段顺序和精确消费边界恢复 ID、结束条件、碰撞形状、目标过滤、命中限制、
移动分段、MoveModeData、特效列表、声音哈希和尾部距离/倍率等结构。部分 Blackboard 包装、
枚举和哈希含义仍是诊断表示，因此返回的组件通常带 `$partial: true`，API 对应报告
`decode.status = "partial"`，不会把它伪装成完全语义化结果。

早期会话中索引指向的 `D:\Hypergryph Launcher\games\Endfield Game` chunk 路径一度不在
当前主机，因此当时无法对庄方易两个 AB 做实时端到端导出。当前嵌入式 worker 已可通过服务
按需恢复并解码实际资源；实现与测试覆盖精确身份查询、按需资源链、managed-reference 选择、
歧义处理和 HTTP 状态。若未来本地 chunk 或缓存再次缺失，应把它报告为证据不可用，而不是
增加模糊路径回退。

## 后续验证

游戏升级后应重新记录 manifest hash，并至少对上述两个 projectileId 验证：精确路径仍唯一、
组件 `id` 与请求一致、managed-reference payload 被完整消费。若 `decode.status` 变为
`unparsed` 或 422，应先更新同版本 IL2CPP 字段顺序和 AnimeStudio 聚焦解码器，而不是增加
模糊路径回退或远程数据依赖。
