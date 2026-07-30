# VFS 浏览器架构

## 目标

工具只依赖本地游戏文件，先稳定提供可查询的基础文件层，再在其上构建干员、武器、模型、任务和音频等聚合视图。

## 三层模型

### 1. VFS 物理读取层

SQLite 主索引保存逻辑文件、来源、物理 chunk、偏移、长度、加密标记和 IV。`server.py` 根据这些字段读取精确字节范围，并在必要时执行 ChaCha20 解密。

### 2. Manifest 逻辑目录层

`manifest.hgmmap` 是游戏提供的全局 BundleManifest。`manifest_index.py` 解压并解析：

- Bundle 名称及索引；
- AssetInfo 逻辑路径；
- AssetInfo 到 Bundle 的映射；
- 资源声明大小。

解析结果按 manifest 内容 SHA-256 缓存为派生 SQLite。前端在 `manifest.hgmmap` 同级显示虚拟目录，进入后只查询当前目录直属子目录和当前页文件，不加载整棵树。

### 3. 内容解析层

manifest 负责回答“资源在哪里”，不负责解释 Unity 对象。用户选择资源后，服务根据 Bundle 名称定位 Effective `.ab`，调用 AnimeStudio，并用 AssetMap 的 `Container` 精确匹配导出文件。常规资源类型无法导出时，`.asset` 和 `.prefab` 可按同一 container 精确回退到 MonoBehaviour TypeTree Dump；同一逻辑资源关联的多个组件会合并为一份可预览文本，而不会扫描整个 Bundle。PCK、USM、TableCfg 和 MemoryPack 也采用相同的按需解析原则。

Prefab 模型属于该层的聚合解析：服务查询 Bundle 传递依赖闭包，通过跨 Bundle PPtr 恢复 `ModelDocument`，再由独立导出器生成 GLB。ModelDocument 保留完整模型语义和原始材质参数，GLB 只承载 LOD0 通用预览所需的资源子集。

## 关键约束

- 普通目录是否含 `.ab` 不再影响目录结构。
- 不以全量 AnimeStudio 扫描作为全局索引来源。
- manifest 缓存是可删除的派生产物，不是持久数据源。
- 新格式解析器遇到结构不一致时应明确报错，不静默忽略。
- HTTP 层不复制二进制格式知识；格式解析应位于独立模块。

## 后续演进

1. 建立 manifest 搜索 API 和 Bundle 依赖查看器。
2. 在现有[组合模型恢复与导出设计](model-recovery.md)基础上继续恢复动画、BlendShape 和游戏特有 Shader。
3. 将 `server.py` 中 PCK、AB、USM 适配器逐步拆成独立模块。
4. 增加小型合成样本测试，避免测试依赖本机游戏文件。
