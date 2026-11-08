# 第三方资源说明

本项目使用或引用的第三方字体、图片等资源及其授权情况。

## 字体

| 资源 | 用途 | 授权 | 来源 |
|---|---|---|---|
| **Noto Sans SC**（思源黑体） | 界面中文/拉丁文字 | [SIL Open Font License 1.1](https://scripts.sil.org/OFL) | Google / Adobe |
| **Segoe UI** | 界面西文（系统字体） | Microsoft 系统字体许可（随 Windows 使用，不单独分发） | Microsoft Windows |
| **Microsoft YaHei**（微软雅黑） | 回退字体（系统字体） | Microsoft 系统字体许可（随 Windows 使用，不单独分发） | Microsoft Windows |

说明：程序仅通过 CSS `font-family` 引用系统已安装字体，未随程序分发字体文件。若后续将字体文件打包进发行版，需随包附带对应 OFL 授权文本。

## 图片

| 资源 | 用途 | 授权 / 来源 |
|---|---|---|
| `splash.png` | 启动画面中的鲸鱼娘形象 | 来源：[**蓝色大肥鱼档案馆**](https://fisharchive.cc/) · [鲸鱼娘同人表情包](https://fisharchive.cc/#wall)<br>授权：**CC BY-NC-SA 4.0**（署名 + 非商业性使用 + 相同方式共享）<br>角色原作：[上善无形](https://space.bilibili.com/4456176) · DeepSeek 元素二次设计：[ZipZipPipe](https://space.bilibili.com/4168597) |
| 界面 emoji 图标（🎵 🔋 🔊） | 按钮图标 | 操作系统 emoji 字体，不涉及单独授权 |

### 关于 CC BY-NC-SA 4.0

本项目以 MIT 协议开源代码，但 `splash.png` 中的鲸鱼娘形象按 **CC BY-NC-SA 4.0** 授权使用：

- **BY**：保留原作者署名（上善无形 / ZipZipPipe / 蓝色大肥鱼档案馆）
- **NC**：**不得用于商业用途**（本项目免费开源、无收费，符合要求）
- **SA**：二次修改后的形象需以相同协议共享

如需商用，请另行取得原作者授权。

## 参考与致谢

- **WEIKAV 网页驱动**（`https://weikav.driveall.cn/`）：本项目 HID 协议通过逆向其前端通信逻辑获得（命令编号、数据包结构），未复制其代码。
- **蓝色大肥鱼档案馆**（`https://fisharchive.cc/`）：鲸鱼娘形象素材来源。

## 依赖库

| 库 | 用途 | 授权 |
|---|---|---|
| pywebview | WebView 窗口 | BSD 3-Clause |
| hidapi | HID 通信 | BSD 3-Clause / GPL（双许可） |
| soundcard | 音频采集 | BSD 3-Clause |
| numpy | 数值计算 | BSD 3-Clause |

---

如有遗漏或错误，欢迎提交 Issue 指出。
