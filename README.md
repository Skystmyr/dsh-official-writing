# official-writing (DeepSeek Harness 公文写作与排版插件)

感谢AI神力，帮助我减少了不少工作量。小白第一个小插件，制作的时候也学习借鉴了已有的轮子，我这个格式是根据我的工作要求来的，希望大家可以自行结合实际修改，欢迎提出改进意见建议

面向党政机关综合文字材料起草与标准排版的 DeepSeek Harness (DSH) 扩展插件。

提供公文起草提示动态组装、素材库关联、专属运行时技能（Skills）以及基于 `python-docx` 的严格方正版式 Word 导出能力。

---

## 核心特性

1. **规范起草与 Token 优化**
   - 内置 `official-document-writing` 技能，覆盖通知、请示、工作总结、班子汇报、调研报告、工作方案、讲话稿等十余种党政机关常见文种。
   - 采用按需调用机制，无需在每次提问中重复粘贴几千字起草规则，显著节省模型上下文与 Token。
   - 自动梳理事实素材与范文参考，严格区隔统计口径、项目阶段与依据来源，杜绝虚构事实。

2. **确定性方正版式 Word (.docx) 排版引擎**
   - 遵循国家公文格式标准，默认版式配置：
     - **纸张与边距**：A4 纸，上下左右边距各 25mm。
     - **行距控制**：固定 28 磅行距，段前段后 0 磅。
     - **标题规范**：
       - 主标题：方正小标宋_GBK，20 磅（二号），居中。
       - 一级标题：方正黑体_GBK，16 磅（三号），首行缩进 2 字符。
       - 二级标题：方正楷体_GBK，16 磅（三号），加粗，首行缩进 2 字符。
       - 三级标题：方正仿宋_GBK，16 磅（三号），加粗，不缩进。
     - **正文规范**：方正仿宋_GBK，16 磅（三号），两端对齐，首行缩进 2 字符，取消孤行控制。
     - **西文字符混排**：英文、数字与百分号自动匹配 Times New Roman。
     - **双引号处理**：中文与英文双引号及其内容严格按正文格式排版，确保字形一致。
     - **页码格式**：页脚居中四号宋体“— PAGE —”自动域。
   - 原文一致性比对：排版完成后自动回检正文段落、字数及标点，防止内容丢失或篡改。

3. **已有文档保结构排版 (`preserve_docx.py`)**
   - 针对已有 DOCX 进行就地段落样式整理，保留原文件的分节、页眉页脚、表格及复杂嵌入对象，跳过高风险格式段落并生成详尽的变更报告。

4. **便捷的 Web 工作台**
   - 集成在 DSH 侧边栏，支持文种选择、本次个性要求输入、最近 30 条历史记录恢复、素材目录选择与多附件直接上传起草。

5. **灵活的本地路径配置**
   - 支持在 Web 界面中一键自定义“知识库路径”与“公文输出目录”（配置持久化保存在当前浏览器本地存储中）。
   - 默认采用 `E:\AI\知识库` 与 `E:\AI\公文输出`，其他用户克隆后可根据本机盘符或工作目录自由调整。

---

## 环境依赖

- **宿主环境**：[DeepSeek Harness (DSH)](https://github.com/deepseek-ai)
- **字体支持**（需在操作系统中安装对应字库以保证最佳显示与打印效果）：
  - 方正小标宋_GBK
  - 方正黑体_GBK
  - 方正楷体_GBK
  - 方正仿宋_GBK
  - Times New Roman、宋体
- **Python 依赖**：
  - Python 3.10+
  - `python-docx`
  - `lxml`
  - `Pillow`

---

## 目录结构

```text
official-writing/
├── package.json              # 插件包清单与 DSH 注入配置
├── cordis.patch.yml          # Cordis 加载补丁
├── index.js                  # Host 端入口：注册 writing 与 formatting 运行时技能
├── client.js                 # Web 端入口：侧边栏面板、素材选择器与工作台
├── icon.svg                  # 插件图标
├── locale/                   # 多语言定义 (zh, en)
│   ├── zh.json
│   └── en.json
├── formatter/                # Word 排版引擎核心 (Python)
│   ├── format_document.py    # Markdown / JSON 到标准 DOCX 排版转换器
│   ├── import_document.py    # 外部文档安全读取与校验
│   ├── preserve_docx.py      # 已有 DOCX 保结构安全整理器
│   ├── font_names.py         # 字体族名检查工具
│   ├── legacy_import.ps1     # 兼容性导入辅助脚本
│   ├── SKILL.md              # 排版技能详细指引与智能体调用约束
│   ├── test_*.py             # 排版单元测试与回归套件
│   └── verify_*.py           # 格式与混排验证脚本
├── examples/                 # 排版样例与说明
├── verify-*.mjs              # 插件前端与技能注册集成测试
└── CORE_SOURCE_GUIDE.md      # 核心源码架构导览
```

---

## 安装与使用

### 1. 本地安装到 DSH Profile

在 DSH 环境中，通过 `plugin_manager` 工具将插件目录安装到当前 Profile：

```javascript
plugin_manager({
  action: "install_bundle",
  target: "<插件目录绝对路径>"
})
```

或者在 DSH 配置中引入 `cordis.patch.yml`。

### 2. 运行测试验证

执行内置的排版与集成测试验证环境完备性：

```powershell
# 运行 Python 排版引擎测试
python -m unittest discover -s formatter -p "test_*.py"

# 运行前端与技能集成测试
node verify-writing.mjs
node verify-model.mjs
node verify-format-integration.mjs
```

---

## 隐私与安全说明

- **保密原则**：处理涉密、内部工作材料时，请务必核实模型服务提供商及接入网络符合单位保密规定。
- **沙箱权限**：公文文档的保存和写入由宿主环境（DSH）的权限与沙箱机制严格保护，插件不会绕过沙箱或进行隐蔽文件读写。
