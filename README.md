# Lyric

> **Lyric** 是一个 Windows 桌面应用，用于从一张包含所有歌词的图片中提取每一行歌词，将其分割为可独立淡入的片段，并根据音频时间轴记录每个片段的出现时机。最终通过一个无边框透明窗口在桌面上动态显示歌词。

---

## ✨ 功能特性

- **图片标记（Extract）**：全屏显示歌词图片，通过拖动和缩放，手动标记每一行歌词的位置，并设置二值化阈值。
- **区域分割（Split）**：对每一行歌词裁剪出的子图，框选多个区域，作为后续逐个淡入的对象。
- **时间轴记录（Timeline）**：跟随音乐播放，精确到毫秒记录每个片段的出现时间以及每一行的切换时间。
- **打包（Wrap）**：将三个 JSON、歌词图片和音频整合为一个 `.dly` 播放包。
- **桌面播放（Player）**：无边框透明置顶窗口，直接打开 `.dly` 播放包，根据时间轴播放音频并逐步显示歌词内容，支持字体颜色切换、淡入淡出动画。
- **数据持久化**：每个阶段生成独立 JSON 文件，便于检查、修改和传递。
- **二值化处理**：歌词图片按 extract.json 中的阈值自动二值化，背景透明，仅显示文字部分。

---

## 🛠️ 技术栈

- Python 3.9+
- PyQt6（包含 QtMultimedia）
- dataclasses / typing / logging
- 支持 Windows 10/11

---

## 🔄 数据流

```
原始图片
   │
   ├── extract_window.py ──► extract.json
   │
   ├── split_window.py ────► split.json
   │
   ├── timeline_window.py ─► timeline.json
   │
   ├── wrap_window.py ─────► xxx.dly
   │
   └── player_window.py ───► 桌面歌词显示（打开 .dly）
```

### JSON 格式示例

**extract.json**

```json
{
  "image_path": "C:/lyrics/cover.png",
  "width": 100,
  "height": 50,
  "threshold": 128,
  "marks": [
    {"id": 1, "cx": 300, "cy": 200},
    {"id": 2, "cx": 500, "cy": 400}
  ]
}
```

> `threshold`（0~255）为二值化全局参数：播放器渲染时，灰度大于该值的像素视为背景并透明化。缺失时默认 128。

**split.json**

```json
{
  "image_path": "C:/lyrics/cover.png",
  "width": 100,
  "height": 50,
  "splits": [
    {
      "extract_id": 1,
      "regions": [
        {"id": 1, "x": 10, "y": 20, "width": 30, "height": 15},
        {"id": 2, "x": 60, "y": 10, "width": 20, "height": 25}
      ]
    },
    {
      "extract_id": 2,
      "regions": []
    }
  ]
}
```

**timeline.json**

```json
{
  "audio_path": "C:/lyrics/song.mp3",
  "extract_fade_out_ms": 300,
  "split_fade_in_ms": 200,
  "font_colors": ["#000000", "#F0F0F0", "#202122"],
  "extract_timings": [
    {
      "extract_id": 1,
      "start_time": 1200,
      "splits": [
        {"split_id": 1, "time": 1500},
        {"split_id": 2, "time": 2300}
      ]
    },
    {
      "extract_id": 2,
      "start_time": 5200,
      "splits": [
        {"split_id": 3, "time": 5400}
      ]
    }
  ]
}
```

---

## 🎮 快捷键总览

### Extract 阶段

| 按键 | 功能 |
|------|------|
| `+` / `-` | 缩放图片 |
| `=` | 重置图片居中 |
| 鼠标左键拖动 | 移动图片 |
| `空格` | 记录当前矩形位置 |
| `Z` | 撤销最近记录 |
| `S` / `M` | 导出 JSON |
| `I` | 导入已有 JSON |
| `Esc` | 退出（询问确认） |

### Split 阶段

| 按键 | 功能 |
|------|------|
| `+` / `-` | 缩放子图 |
| `←` / `→` | 切换上/下一个 extract |
| 鼠标左键拖动 | 框选分割区域 |
| `Z` | 撤销当前页最后一个框 |
| `S` / `M` | 导出 JSON |
| `I` | 导入已有 JSON |
| `Esc` | 退出（询问确认） |

### Timeline 阶段

| 按键 | 功能 |
|------|------|
| `B` | 开始播放音频 |
| `P` | 暂停 / 继续播放 |
| 空格 | 记录 split 时间 / 翻页 |
| `Z` | 撤销最近一次操作 |
| `S` / `M` | 导出 timeline.json |
| `Esc` | 退出（询问确认） |

### Wrap 阶段

| 操作 | 功能 |
|------|------|
| 文件对话框 | 依次选择 extract.json、split.json、timeline.json |
| 自动定位 | 从 extract.json 的 `image_path` 与 timeline.json 的 `audio_path` 找到图片和音频 |
| 保存对话框 | 选择 .dly 输出路径并打包 |

### Player 阶段

> 启动后先弹出输入框询问 `.dly` 播放包的网址：输入网址则从该网址下载并打开；留空则弹出文件对话框选择本地 `.dly`；网址无效（无法访问或内容不是 .dly）会弹出错误提示并退出。

| 操作 | 功能 |
|------|------|
| 网址输入框 | 输入 .dly 网址下载打开；留空则选择本地文件 |
| `P` | 暂停 / 继续播放 |
| `M` | 切换字体颜色（循环 font_colors） |
| 鼠标左键拖动 | 移动无边框窗口 |
| 鼠标右键 | 关闭窗口 |
| `Esc` | 关闭窗口 |

> Player 始终置顶显示（WindowStaysOnTopHint），歌词浮于其他窗口之上。

---

## 🎨 动画与视觉效果

- **透明背景**：播放器窗口完全透明，仅显示歌词文字，与桌面无缝融合。
- **extract 淡出**：歌词行切换时，旧内容逐渐淡出至透明，新内容随后出现。
- **split 淡入**：每个片段在触发时从透明逐渐显现，淡入时长可在 `timeline.json` 中全局配置。
- **颜色切换**：按 `M` 键循环切换 `font_colors` 列表中的颜色（默认黑色，可手动添加更多颜色值）。

---

## ⚠️ 注意事项

- **音频格式**：依赖 Windows 系统解码器。若无声，请安装 [LAV Filters](https://github.com/Nevcairiel/LAVFilters/releases) 或 [K-Lite Codec Pack](https://codecguide.com/download_kl.htm)。
- **图片格式**：支持常见图片格式（png, jpg, bmp, gif, tiff 等）。
- **播放包**：Player 阶段只接受 `.dly` 播放包，请先运行 Wrap 阶段生成。`.dly` 为 ZIP 容器，内含 `manifest.json` 文件清单，可直接用解压软件查看内容。
- **音频路径**：`timeline.json` 中保存的 `audio_path` 为本地文件路径（通常不含 `file://` 前缀）。Wrap 阶段已兼容两种格式，打包后音频嵌入 .dly，不再依赖原始文件。
- **透明窗口**：需要 Windows 10 及以上系统支持。若窗口未透明，请检查系统主题或尝试重启应用。

---

## 📝 开发说明

- 代码包含完整的类型注解和文档字符串，便于二次开发。
- 日志默认输出到控制台，级别为 `DEBUG`。
- 坐标系统：图片左上角为原点，x 向右，y 向下。JSON 中的坐标均为原始像素值。
- ID 管理：全局递增，撤销不回收 ID。

---

## 🙏 致谢

本项目代码由 **DeepSeek** 编写完成，感谢其贡献。如果你在使用中发现问题或有改进建议，欢迎提交 Issue 或 Pull Request。

https://chat.deepseek.com/share/l5514ku74q7e5s2tsb

---

**Enjoy your desktop lyrics!** 🎵
