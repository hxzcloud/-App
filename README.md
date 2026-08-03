# 悬浮翻译软件 FloatTranslator

一款轻量级的 Windows 桌面悬浮翻译工具，支持全局快捷键唤起、实时翻译、译文转代码变量名。

## ✨ 功能特性

- 🖼️ **悬浮窗口**：始终置顶的悬浮翻译窗口，不影响其他操作
- ⌨️ **全局快捷键**：`Ctrl+Alt+T` 一键显示/隐藏翻译窗口
- ⏎ **Enter 直译**：输入框按 Enter 即翻译，Shift+Enter 换行
- 🌐 **多语言支持**：中文、英文、日文、韩文、法文、德文、俄文、西班牙文、意大利文互译
- 🎯 **智能识别**：按字符权重判定主要语种，避免中英混排误判
- 🧪 **多源降级**：腾讯 TranSmart → 有道 → MyMemory，自动故障转移
- 🧬 **变量名缩写**：把译文压缩成 camelCase / PascalCase / snake_case / CONSTANT_CASE 四种命名风格
- 📋 **一键复制**：翻译结果与变量名点击即复制
- 🖱️ **自由拖动**：标题栏拖动，右下角缩放
- 🔄 **语言互换**：一键交换源/目标语言及输入/结果内容
- 📥 **系统托盘**：隐藏到托盘，Esc 快速隐藏

## 📁 工程结构

```
FloatTranslator/
├── main.py              # 主程序（单文件，~1060 行）
├── config.json          # 配置：窗口位置/尺寸、目标语言、背景图
├── requirements.txt     # Python 依赖（PyQt5 / pynput / requests）
├── 启动.bat             # 带控制台启动（自动装依赖）
├── 启动(无控制台).bat   # 后台静默启动
└── 启动.vbs             # 通过 VBScript 静默拉起
```

## 🚀 快速开始

### 环境要求
- Windows 10/11
- Python 3.8+

### 安装与启动

1. 双击 `启动.bat` —— 会自动检测 Python、从清华源装依赖、启动程序
2. 或手动：`pip install -r requirements.txt && python main.py`

## 📖 使用说明

| 操作 | 说明 |
|------|------|
| `Ctrl + Alt + T` | 显示/隐藏翻译窗口 |
| `Enter`（输入框内）| 翻译 |
| `Shift + Enter` | 输入框内换行 |
| `Esc` | 隐藏窗口 |
| 拖动标题栏 | 移动窗口 |
| 右下角 | 拉伸窗口 |
| `−` / `×` | 隐藏到托盘 |

### 翻译流程

1. 按 `Ctrl+Alt+T` 唤出
2. 输入文字（默认自动检测源语言）
3. 按 `Enter` 或点「翻译」按钮
4. 译文出来后点「**变量名**」可一键生成 4 种命名风格，鼠标移到对应项即显示，点击即复制到剪贴板

### 变量名缩写规则

- 去除常见停用词（the / a / of / please 等）
- 优先保留英文关键词；含中文则先翻译成英文
- 常见长词自动缩略：information → info、maximum → max、between → btw …
- 单个单词超 12 字符时截断，数字开头自动加 `v_` 前缀
- 输出四种风格：`camelCase` / `PascalCase` / `snake_case` / `CONSTANT_CASE`

## ⚙️ 配置说明（`config.json`）

```json
{
    "window": { "x": 200, "y": 200, "width": 350, "height": 300 },
    "language": { "from": "auto", "to": "en" },
    "appearance": { "background_image": "", "background_opacity": 0.95 }
}
```

窗口位置/尺寸会在每次拖动和缩放后自动写回。背景图通过托盘菜单「选择背景图」设置，`background_opacity` 控制背景图透明度（有效范围 0.05–0.6，超出会被钳制）。

## 🔧 技术说明

### 翻译源（按优先级降级链）

1. **腾讯 TranSmart**（`transmart.qq.com/api/imt`）：国内直连，~100ms 响应，质量最好
2. **有道翻译 aidemo**：稳定的备用源
3. **MyMemory**（`api.mymemory.translated.net`）：兜底源，有匿名每日字数限额
4. **Google 翻译**：仅作为 VPN 环境下兜底，国内不可达

降级判断：每个源失败抛 `TranslationError`，链上自动切下一个，不再用字符串前缀误判结果。

### 技术栈
- **GUI**：PyQt5（无边框 + 圆角 + 阴影）
- **全局快捷键**：pynput
- **HTTP**：requests
- **线程模型**：翻译走 `QThread`，键盘监听走 pynput 守护线程，热键通过 `QTimer.singleShot` 切回主线程

## ❓ 常见问题

**Q: 快捷键不生效？**
> A: 多见于以管理员权限运行的进程；用普通权限启动即可。

**Q: 翻译失败？**
> A: 状态栏会提示来自哪个源；TranSmart 在国内联通/电信下均可直连；若全部失败检查代理。

**Q: 如何完全退出？**
> A: 托盘右键 → 「退出」。

## 📝 更新日志

### v2.1 (2026-08-03)
- 修复翻译源：实测替换为腾讯 TranSmart（首选）+ 有道 + MyMemory
- 降级判断从字符串前缀改为异常机制
- 语言检测加入 CJK 权重，解决中英混排误判
- 翻译快捷键改为 Enter，Shift+Enter 换行
- 新增变量名缩写功能（4 种命名风格 + 点击复制）
- 移除未生效的 `for_api` 参数；`background_opacity` 配置改为真正读取并生效
- 增加异步任务清理，避免 worker 引用丢失

### v1.0.0
- 初始版本

## 📄 许可证

MIT License