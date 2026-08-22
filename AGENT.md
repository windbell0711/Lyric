
> 以下内容由 DeepSeek 撰写
> https://chat.deepseek.com/share/l5514ku74q7e5s2tsb

## 项目概述

Lyric 是一个桌面歌词生成与展示工具，用于从一张包含全部歌词的图片中提取每一行歌词，将其分割为多个可逐个淡入的片段，然后根据音频时间轴记录每个片段的出现时机，最终通过一个无边框透明窗口在桌面上播放动态歌词。

项目由四个主要阶段组成：**Extract（提取）**、**Split（分割）**、**Timeline（时间轴）**、**Player（播放器）**。每个阶段产生或使用 JSON 文件进行数据交换。

---

## 技术栈

- Python 3.9+
- PyQt6（含 QtMultimedia）
- 类型注解、dataclasses、logging
- 平台：Windows（主要开发与运行环境）
- 音频格式依赖系统解码器（测试环境支持 mp3、wav、flac 等）

---

## 文件结构

```
Lyric/
├── AGENT.md                  # 本文件
├── main.py                   # 可选：整体入口（当前未统一，各阶段独立运行）
├── models.py                 # 所有数据类定义
├── common.py                 # 公共基类 BaseImageWindow，封装缩放、绘制、对话框等
├── extract_window.py         # Extract 阶段：图片标记器
├── split_window.py           # Split 阶段：分割器
├── timeline_window.py        # Timeline 阶段：时间轴记录器
└── player_window.py          # Player 阶段：桌面歌词播放器
```

---

## 模块功能与状态

### `models.py`
定义所有数据类：
- `MarkItem`：extract 标记（id, cx, cy）
- `SplitRegion`：split 框（id, x, y, width, height）
- `SplitItem`：一个 extract 的所有 split 框（extract_id, regions）
- `TimelineSplit`：timeline 中单个 split 的时间信息（split_id, time）
- `TimelineExtract`：timeline 中一个 extract 的时间信息（extract_id, start_time, splits）

### `common.py`
提供 `BaseImageWindow` 基类，包含：
- 缩放管理（`scale`、`_update_scaled_pixmap`）
- 屏幕中心与图像绘制辅助
- 导入/导出对话框
- 标准键盘处理（`+`/`-`/`Esc`/`S`/`M`/`I`/`Z`）
- 抽象方法 `on_export`、`on_import`、`on_undo` 等

### `extract_window.py`
- 功能：全屏显示用户选择的图片，用户拖动图片，屏幕中心有固定矩形，按空格记录矩形位置。
- 快捷键：`+`/`-` 缩放，`=` 重置居中，`空格` 记录，`Z` 撤销，`S/M` 导出 `extract.json`，`I` 导入已有 JSON，`Esc` 退出。
- 输出 JSON 格式：
  ```json
  {
    "image_path": "原图路径",
    "width": 100,
    "height": 50,
    "marks": [{"id": 1, "cx": 300, "cy": 200}]
  }
  ```
- 状态：完成，可独立运行。

### `split_window.py`
- 功能：读取 `extract.json`，对每个标记裁剪出子图，用户在全屏下用鼠标框选若干区域（红色矩形），按左右键翻页，Z 撤销当前页最后一框。
- 快捷键：`+`/`-` 缩放，`←`/`→` 翻页，`Z` 撤销，`S/M` 导出 `split.json`，`I` 导入，`Esc` 退出。
- 输出 JSON 格式：
  ```json
  {
    "image_path": "原图路径",
    "width": 100,
    "height": 50,
    "splits": [
      {
        "extract_id": 1,
        "regions": [
          {"id": 1, "x": 10, "y": 20, "width": 30, "height": 15}
        ]
      }
    ]
  }
  ```
- 状态：完成。

### `timeline_window.py`
- 功能：读取 `extract.json` 和 `split.json`，播放音频，用户根据音乐节奏按空格记录每个 split 的出现时间，全部变绿后空格翻页自动记录 extract 开始时间。
- 快捷键：`B` 开始播放，`P` 暂停/继续，`空格` 记录/翻页，`Z` 撤销，`S/M` 导出 `timeline.json`，`Esc` 退出。
- 新增：进入前询问淡出/淡入时长，导出时写入全局字段。
- 输出 JSON 格式：
  ```json
  {
    "audio_path": "音频绝对路径",
    "extract_fade_out_ms": 300,
    "split_fade_in_ms": 200,
    "font_colors": ["#000000"],
    "extract_timings": [
      {
        "extract_id": 1,
        "start_time": 1200,
        "splits": [
          {"split_id": 1, "time": 1500},
          {"split_id": 2, "time": 2300}
        ]
      }
    ]
  }
  ```
- 状态：完成。

### `player_window.py`
- 功能：桌面歌词播放器，读取三个 JSON，无边框透明窗口，按时间轴播放音频并显示歌词内容。
- 特性：
  - 二值化：子图白色变为透明，黑色保留，可切换颜色。
  - 透明窗口：背景完全透明，仅绘制已触发的 split 内容。
  - 动画：extract 切换淡出，split 出现淡入（时长来自 timeline.json）。
  - 单曲循环：播放结束后默认回到起点从头重新播放。
  - 颜色切换：按 `M` 切换 `font_colors` 列表中的颜色。
  - 启动时先询问 .dly 网址：输入网址则下载打开，留空则选择本地文件，网址无效则提示并退出。
- 快捷键：`P` 暂停/播放，`M` 切换颜色，`Esc` 关闭（窗口无边框，可左键拖动，右键关闭）。
- 状态：完成。

---

## 数据流

1. **Extract**：原始图片 → `extract.json`（含所有歌词行的位置和尺寸）
2. **Split**：`extract.json` → `split.json`（每个歌词行内的多个子区域）
3. **Timeline**：`extract.json` + `split.json` + 音频 → `timeline.json`（时间轴信息）
4. **Player**：`extract.json` + `split.json` + `timeline.json` → 桌面歌词显示

所有 JSON 均包含 `image_path`（原图绝对路径）、`width`、`height`（矩形大小），保证数据一致性。

---

## 运行方法

每个阶段独立运行（未提供统一入口）：

```bash
# Extract
python extract_window.py

# Split
python split_window.py

# Timeline
python timeline_window.py

# Player
python player_window.py
```

启动后按提示选择对应文件。

---

## 开发约定

- **类型注解**：所有函数参数和返回值都尽量添加类型注解。
- **数据类**：使用 `dataclasses` 定义数据模型，避免使用字典传递复杂数据。
- **日志**：使用 `logging`，级别默认 `DEBUG`，输出到控制台。
- **代码风格**：遵循 PEP 8，类名使用驼峰，函数名小写下划线。
- **UI 模式**：全屏显示，无边框（提取、分割、时间轴），播放器窗口无边框透明。
- **坐标系统**：所有 JSON 中的坐标均为原始图片像素坐标，x 向右，y 向下，中心为 `cx, cy`，分割框为左上角 `x, y` 加 `width, height`。
- **ID 管理**：全局递增，撤销不回收。
- **错误处理**：关键操作使用 `QMessageBox` 提示，异常记录日志。

---

## 已知问题与注意事项

1. **音频路径**：`timeline.json` 中保存的 `audio_path` 应为本地文件路径（不含 `file://` 前缀），播放器已兼容两种格式。
2. **暂停问题**：早期播放器暂停键失效，已通过直接比较 `playbackState()` 解决。
3. **透明窗口**：播放器使用 `WA_TranslucentBackground`，需要操作系统支持（Windows 10+ 正常）。
4. **字体颜色**：`timeline_window` 导出时默认提供 `["#000000"]`，用户可手动修改 JSON 添加更多颜色，按 `M` 循环切换。
5. **动画时长**：`timeline_window` 导出前会询问淡出/淡入时长，默认 300ms/200ms。
6. **音频格式**：依赖系统解码器，若无声请安装 LAV Filters 或 K-Lite Codec Pack。

---

## 未来工作

- **统一入口**：可添加 `main.py`，通过一个主菜单引导用户依次完成三个阶段或直接进入播放器。
- **`.dly` 文件**：当前最终产物为三个json文件，尚未定义专用打包格式（.dly），可在后续整合。
- **动画优化**：可考虑使用 `QPropertyAnimation` 代替手动 `QTimer`，但当前方案足够。
- **音视频同步校准**：若音频偏移，可增加偏移量设置。

---

## 总结

项目核心功能已完成，四个阶段均可独立运行，数据格式清晰，代码结构合理，注释充分。后续开发可根据新需求在现有基础上扩展。如有疑问，请查阅各窗口文件内的详细注释。
