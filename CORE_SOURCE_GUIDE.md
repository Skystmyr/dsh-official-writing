# 公文写作插件核心源码导览

本文只梳理插件运行所需的核心源码，不包含测试、样例、临时目录和缓存文件。插件包版本：`1.7.0`。当前配置中的已安装包通过本地链接指向本目录。

## 目录与核心文件

```text
official-writing/
  package.json                 包清单、入口和 Web Client 声明
  cordis.patch.yml             将插件行 official-writing 插入当前配置
  index.js                     Host 入口；注册写作与排版两个 Skill
  client.js                    Web 左侧栏入口、工作台、提示词、模型、素材与上传流程
  formatter/
    SKILL.md                   Word 排版工作流及默认格式说明
    format_document.py         Markdown/JSON 到新建 DOCX 的确定性排版器
    import_document.py         安全读取与核对输入文档内容
    preserve_docx.py           在保留原 DOCX 包结构的前提下整理安全段落
    legacy_import.ps1          旧版 DOC/WPS 的辅助转换程序；自动转换当前关闭
```

源码都留在当前插件目录中，没有复制第二份“整理版源码”，以免副本与实际运行代码逐渐不一致。

## 启动与注册

1. DSH 读取 `package.json` 中的 `dsh.bundle.patch` 和 `dsh.client`。
2. `cordis.patch.yml` 插入 `@local/official-writing` 插件行。
3. Host 加载 `index.js`：注册 `official-document-writing` 和 `official-document-formatting` 两个运行时 Skill。写作规则正文内嵌于 Host 源码；排版 Skill 正文及 Python 工具从同一包的 `formatter/` 目录读取。
4. Web 加载 `client.js`：注册 `sidebar.panellist` 的 `official-writing` 按钮，以及 `main` 面板中的对应页面。

## 前端主要流程

- `client.js:129`：`buildWritingPrompt()` 按任务动态组装简短提示，只添加文种、本次要求、确实选中的知识库路径和 Word 排版指令；附件内容通过 DSH 附件通道发送，不在提示正文里重复文件名。
- `client.js:140`：读取浏览器本地保存的文种、个性要求和 Word 输出选项；历史需求独立存储，不自动叠加到新任务。
- `client.js:161`：`Page` 维护目录浏览、素材选择、附件、模型和排版开关等界面状态。
- `client.js:206`：`prepare()` 校验要求、附件大小、模型选择，生成提示词并启动草稿或附件发送流程。
- `client.js:228`：注册 Slot、知识库目录读取、模型目录、模型选择和附件传送服务。
- 普通模式用 `uiWorkspace.startSession()` 设置会话草稿；附件模式先用 `fileUploads.upload()` 为目标会话暂存文件，再将文件收据和文本提示一并排入该会话。

## Host 技能入口

`index.js` 中的两个 Skill 是模型按需获取的指令资源：

- `official-document-writing`：文种结构、机关文字风格、事实边界、修改方式和输出原则。
- `official-document-formatting`：排版规则、生成新 Word 或保留已有 DOCX 的操作流程。

技能能减少每次起草提示中重复粘贴的规则，但被加载的技能正文和所选附件仍可能占用模型上下文；插件不承诺固定 Token 折扣。

## Word 排版核心

### 新建 DOCX

`formatter/format_document.py` 的主要职责：

- `resolve_config()`：合并并校验单次排版的显式配置覆盖，不修改全局默认值。
- `parse_markdown()` / `parse_json()`：读取受控 Markdown 或 JSON；不支持的表格、图片、代码、复杂 Markdown 会拒绝，不静默丢弃内容。
- `segment_text()` / `styled_segments()`：识别成对中文或英文双引号、引号内文字，以及标题同段正文边界；保留文本原字符，分别应用正文字符格式。
- `build_document()`：创建 A4 DOCX，设置段落样式、字体、字号、缩进、孤行控制和自动 PAGE 域。
- `check_document()`：检查生成 DOCX 的版式结构；若提供原始输入，还会比较正文段落、内容和顺序。
- `format_file()` / `main()`：实现 CLI、覆盖保护及 JSON 结果。

当前默认版式：A4，四边 25mm，固定 28 磅行距；大标题方正小标宋_GBK 20 磅居中，标题前后各空一行；一级方正黑体_GBK 16 磅；二级方正楷体_GBK 16 磅加粗；三级标题方正仿宋_GBK 16 磅加粗并与正文同段接排，段落按正文格式两端对齐、首行缩进两字符；正文方正仿宋_GBK 16 磅；正文取消孤行控制；页码为居中四号宋体自动 PAGE 域。

双引号以及其中内容采用正文格式；ASCII 字母数字仍按正文的新罗马规则处理。引号必须在单段内配对，不配对时拒绝输出，避免错误格式化。

### 已有 DOCX

`formatter/preserve_docx.py` 不通过重建文档来排版，而是对可安全识别的段落修改格式，并校验正文结构及非目标内容保持不变。复杂字段、自动编号、修订、对象等风险段落会跳过并报告；已有页边距、页眉、页脚和页码保持原样，因此此模式不会把新建文档的默认 PAGE 页脚强加到旧文件上。

`formatter/import_document.py` 负责读取纯 DOCX/TXT/受控 Markdown 并核对生成内容。含表格、图片、超链接、修订或其他不支持结构的文档会拒绝导入。`.doc` 和 `.wps` 自动转换目前关闭，需要先在 Word/WPS 中另存为 DOCX。

## 输出目录边界

前端提示和排版 Skill 要求按指定输出目录（默认 `E:\AI\公文输出\<文稿标题>\`，可在界面路径设置中自定义）组织同一篇文稿的草稿、中间稿和成稿，但目录创建及文件写入由 Agent 工具执行，**不是前端直接写盘，也不是插件自行绕过沙箱**。该目录在工作区之外时，是否能写取决于 DSH 沙箱和授权；受阻时必须报告，不能假称已保存。

## 常用检查

使用 DSH 提供的 Python 和 `python-docx`：

```powershell
& "<DSH Python 绝对路径>" "official-writing\formatter\verify_format.py"
& "<DSH Python 绝对路径>" "official-writing\formatter\format_document.py" --check "<输出目录>\成稿.docx"
```

前端及技能注册回归脚本在 `verify-writing.mjs`、`verify-model.mjs` 和 `verify-format-integration.mjs`；它们是测试文件，不属于运行时核心模块。
