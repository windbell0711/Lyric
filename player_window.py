import sys
import json
import logging
import time
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, QUrl
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QImage, QMouseEvent, QKeyEvent
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import QApplication, QWidget, QFileDialog, QMessageBox

from models import SplitRegion

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


class PlayerWindow(QWidget):
    """桌面歌词播放器，无边框透明窗口，按时间显示二值化歌词内容，支持淡入淡出和颜色切换。"""

    DISPLAY_SCALE = 0.8
    CLICK_THRESHOLD = 5
    DEFAULT_EXTRACT_FADE_OUT_MS = 300
    DEFAULT_SPLIT_FADE_IN_MS = 200
    DEFAULT_FONT_COLORS = ["#000000"]  # 默认黑色

    def __init__(self, extract_path: str, split_path: str, timeline_path: str):
        super().__init__()

        # ----- 加载三个 JSON -----
        with open(extract_path, "r", encoding="utf-8") as f:
            extract_data = json.load(f)
        with open(split_path, "r", encoding="utf-8") as f:
            split_data = json.load(f)
        with open(timeline_path, "r", encoding="utf-8") as f:
            timeline_data = json.load(f)

        self.image_path = extract_data["image_path"]
        self.rect_w = int(extract_data["width"])
        self.rect_h = int(extract_data["height"])
        self.marks = extract_data["marks"]
        self.splits_data = split_data["splits"]
        self.extract_timings = timeline_data["extract_timings"]

        # 动画时长
        self.extract_fade_out_ms = timeline_data.get(
            "extract_fade_out_ms", self.DEFAULT_EXTRACT_FADE_OUT_MS
        )
        self.split_fade_in_ms = timeline_data.get(
            "split_fade_in_ms", self.DEFAULT_SPLIT_FADE_IN_MS
        )
        logger.info(f"动画时长：extract淡出={self.extract_fade_out_ms}ms, split淡入={self.split_fade_in_ms}ms")

        # 字体颜色配置
        self.font_colors = timeline_data.get("font_colors", self.DEFAULT_FONT_COLORS)
        self.current_color_index = 0
        logger.info(f"字体颜色列表：{self.font_colors}")

        # ----- 加载原图并裁剪二值化子图（透明背景）-----
        original = QPixmap(self.image_path)
        if original.isNull():
            raise ValueError(f"无法加载原图: {self.image_path}")

        self.base_binary_images: Dict[int, QPixmap] = {}  # 二值化掩码（黑字透明底）
        for mark in self.marks:
            sub = self._crop_and_binarize(original, mark["cx"], mark["cy"])
            self.base_binary_images[mark["id"]] = sub

        # 生成当前颜色下的彩色子图
        self.colored_sub_images: Dict[int, QPixmap] = {}
        self._update_colored_images()

        # ----- 构建 extract_id -> 时间/区域映射 -----
        self.extract_info: Dict[int, dict] = {}
        for timing in self.extract_timings:
            eid = timing["extract_id"]
            self.extract_info[eid] = {
                "start_time": timing["start_time"],
                "splits": timing["splits"],
            }

        self.extract_regions: Dict[int, List[SplitRegion]] = {}
        for item in self.splits_data:
            eid = item["extract_id"]
            regions = [SplitRegion(id=r["id"], x=r["x"], y=r["y"],
                                   width=r["width"], height=r["height"])
                       for r in item["regions"]]
            self.extract_regions[eid] = regions

        # ----- 音频播放器 -----
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)

        audio_path = timeline_data.get("audio_path", "")
        if not audio_path:
            raise ValueError("timeline.json 中缺少 audio_path")
        logger.info(f"音频路径: {audio_path}")
        if audio_path.startswith("file://"):
            self.player.setSource(QUrl(audio_path))
        else:
            self.player.setSource(QUrl.fromLocalFile(audio_path))

        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # ----- 窗口设置（透明无边框）-----
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 窗口大小基于子图原始尺寸缩放
        if self.base_binary_images:
            first_sub = next(iter(self.base_binary_images.values()))
            disp_w = int(first_sub.width() * self.DISPLAY_SCALE)
            disp_h = int(first_sub.height() * self.DISPLAY_SCALE)
            self.setFixedSize(disp_w, disp_h)
        else:
            self.setFixedSize(self.rect_w, self.rect_h)

        # 居中显示
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move((sg.width() - self.width()) // 2,
                      (sg.height() - self.height()) // 2)

        # ----- 状态变量 -----
        self.current_extract_id: Optional[int] = None
        self.pending_extract_id: Optional[int] = None
        self.extract_fade_out_alpha: float = 0.0

        # split 动画状态
        self.split_alphas: Dict[int, float] = {}
        self.split_target_alphas: Dict[int, float] = {}
        self.split_fade_in_start: Dict[int, float] = {}

        self._auto_play_requested = True

        # 鼠标拖动
        self._mouse_pressed = False
        self._mouse_press_global = None
        self._mouse_press_window = None

        # 定时更新画面（10ms）
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_display)
        self.timer.start(10)

        self.last_tick_time = time.monotonic()
        logger.debug("播放器初始化完成")

    # ------------------------------------------------------------------
    # 二值化与裁剪（透明背景）
    # ------------------------------------------------------------------
    def _crop_and_binarize(self, original: QPixmap, cx: float, cy: float) -> QPixmap:
        left = int(round(cx - self.rect_w / 2))
        top = int(round(cy - self.rect_h / 2))
        right = left + self.rect_w
        bottom = top + self.rect_h

        # 创建透明底图
        sub = QImage(self.rect_w, self.rect_h, QImage.Format.Format_ARGB32)
        sub.fill(Qt.GlobalColor.transparent)

        src_left = max(0, left)
        src_top = max(0, top)
        src_right = min(original.width(), right)
        src_bottom = min(original.height(), bottom)

        if src_right > src_left and src_bottom > src_top:
            valid_w = src_right - src_left
            valid_h = src_bottom - src_top
            dst_left = src_left - left
            dst_top = src_top - top
            crop = original.copy(src_left, src_top, valid_w, valid_h)  # QPixmap

            painter = QPainter(sub)
            painter.drawPixmap(dst_left, dst_top, crop)
            painter.end()

        return self._binarize_image(sub)

    def _binarize_image(self, image: QImage) -> QPixmap:
        """二值化并设置 alpha：白色透明，黑色不透明。"""
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                gray = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
                if gray > 128:
                    image.setPixelColor(x, y, QColor(0, 0, 0, 0))  # 透明
                else:
                    image.setPixelColor(x, y, QColor(0, 0, 0, 255))  # 黑色（将被着色）
        return QPixmap.fromImage(image)

    def _update_colored_images(self):
        """根据当前颜色重新生成所有彩色子图。"""
        color = QColor(self.font_colors[self.current_color_index])
        self.colored_sub_images.clear()
        for eid, base_pixmap in self.base_binary_images.items():
            colored = self._apply_color(base_pixmap, color)
            self.colored_sub_images[eid] = colored
        logger.debug(f"已更新彩色子图，当前颜色: {color.name()}")

    def _apply_color(self, pixmap: QPixmap, color: QColor) -> QPixmap:
        """将二值化掩码中的黑色像素替换为指定颜色。"""
        result = QPixmap(pixmap.size())
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), color)
        painter.end()
        return result

    # ------------------------------------------------------------------
    # 颜色切换
    # ------------------------------------------------------------------
    def _toggle_color(self):
        if not self.font_colors:
            return
        self.current_color_index = (self.current_color_index + 1) % len(self.font_colors)
        self._update_colored_images()
        self.update()
        logger.info(f"切换到字体颜色: {self.font_colors[self.current_color_index]}")

    # ------------------------------------------------------------------
    # 音频回调
    # ------------------------------------------------------------------
    def _on_player_error(self, error, error_string):
        logger.error(f"音频播放错误: {error} - {error_string}")
        QMessageBox.critical(self, "播放错误", f"无法播放音频：\n{error_string}")

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._auto_play_requested:
            self._auto_play_requested = False
            self.player.play()
            self.timer.start()
            logger.debug("媒体加载完成，自动开始播放")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            logger.info("音频播放结束")
            self.timer.stop()

    # ------------------------------------------------------------------
    # 更新当前帧（含动画）
    # ------------------------------------------------------------------
    def _update_display(self):
        now = time.monotonic()
        delta = now - self.last_tick_time
        self.last_tick_time = now

        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

        # 动画推进仅在播放时进行
        if playing:
            # 1. extract 淡出
            if self.extract_fade_out_alpha > 0.0:
                alpha_decrease = delta * 1000 / self.extract_fade_out_ms
                self.extract_fade_out_alpha -= alpha_decrease
                if self.extract_fade_out_alpha < 0.0:
                    self.extract_fade_out_alpha = 0.0
                    self._perform_extract_switch()
                self.update()

            # 2. split 淡入
            for split_id, target in list(self.split_target_alphas.items()):
                if target == 1.0 and split_id in self.split_alphas:
                    current_alpha = self.split_alphas[split_id]
                    if current_alpha < 1.0:
                        alpha_increase = delta * 1000 / self.split_fade_in_ms
                        new_alpha = current_alpha + alpha_increase
                        if new_alpha > 1.0:
                            new_alpha = 1.0
                        self.split_alphas[split_id] = new_alpha
                        self.update()

        # 3. 根据音频时间确定当前 extract 和 split 触发
        position = self.player.position()
        active_extract_id = None
        for eid, info in self.extract_info.items():
            start = info["start_time"]
            if start is not None and start <= position:
                active_extract_id = eid

        # 检测是否需要启动淡出
        if (self.pending_extract_id is None and
                active_extract_id is not None and
                active_extract_id != self.current_extract_id):
            self.pending_extract_id = active_extract_id
            self.extract_fade_out_alpha = 1.0
            logger.debug(f"启动 extract 淡出，目标 extract {active_extract_id}")

        # 更新 split 触发状态
        if self.current_extract_id is not None:
            info = self.extract_info.get(self.current_extract_id)
            if info:
                for split_info in info["splits"]:
                    split_id = split_info["split_id"]
                    split_time = split_info["time"]
                    if split_time is not None and split_time <= position:
                        if split_id not in self.split_target_alphas:
                            self.split_target_alphas[split_id] = 1.0
                            self.split_alphas[split_id] = 0.0
                            logger.debug(f"split {split_id} 触发淡入")
                        elif self.split_target_alphas[split_id] == 0.0:
                            self.split_target_alphas[split_id] = 1.0
                            self.split_alphas[split_id] = 0.0

        self.update()

    def _perform_extract_switch(self):
        if self.pending_extract_id is not None:
            old_id = self.current_extract_id
            new_id = self.pending_extract_id
            logger.debug(f"extract 切换：{old_id} -> {new_id}")

            self.current_extract_id = new_id
            self.pending_extract_id = None
            # 重置 split 动画状态
            self.split_alphas.clear()
            self.split_target_alphas.clear()
            self.split_fade_in_start.clear()
            self.update()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        if self.current_extract_id is None or self.current_extract_id not in self.colored_sub_images:
            return

        bin_pixmap = self.colored_sub_images[self.current_extract_id]

        scale_x = self.width() / self.rect_w
        scale_y = self.height() / self.rect_h
        scale = min(scale_x, scale_y)
        target_w = int(self.rect_w * scale)
        target_h = int(self.rect_h * scale)
        x0 = (self.width() - target_w) // 2
        y0 = (self.height() - target_h) // 2

        # 全局淡出透明度
        global_alpha = 1.0
        if self.extract_fade_out_alpha > 0.0:
            global_alpha = self.extract_fade_out_alpha

        regions = self.extract_regions.get(self.current_extract_id, [])
        for region in regions:
            split_id = region.id
            if split_id not in self.split_target_alphas or self.split_target_alphas[split_id] == 0.0:
                continue
            alpha = self.split_alphas.get(split_id, 0.0)
            if alpha <= 0.0:
                continue

            # 应用全局和局部透明度
            final_alpha = alpha * global_alpha
            if final_alpha <= 0.0:
                continue

            painter.save()
            painter.setOpacity(final_alpha)

            left = x0 + region.x * scale
            top = y0 + region.y * scale
            w = region.width * scale
            h = region.height * scale
            target_rect = QRectF(left, top, w, h)
            src_rect = QRectF(region.x, region.y, region.width, region.height)

            painter.drawPixmap(target_rect, bin_pixmap, src_rect)
            painter.restore()

    # ------------------------------------------------------------------
    # 鼠标事件：左键拖动，右键关闭
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_pressed = True
            self._mouse_press_global = event.globalPosition().toPoint()
            self._mouse_press_window = self.pos()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._mouse_pressed and self._mouse_press_global is not None:
            delta = event.globalPosition().toPoint() - self._mouse_press_global
            self.move(self._mouse_press_window + delta)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._mouse_pressed:
            self._mouse_pressed = False
            event.accept()

    # ------------------------------------------------------------------
    # 键盘事件：P 暂停/播放，M 切换颜色，Esc 关闭
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_P:
            self._toggle_play_pause()
        elif event.key() == Qt.Key.Key_M:
            self._toggle_color()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def _toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.timer.stop()
            logger.debug("暂停播放")
        else:
            self.player.play()
            self.timer.start()
            logger.debug("开始/恢复播放")


def main_player():
    app = QApplication(sys.argv)

    extract_path, _ = QFileDialog.getOpenFileName(None, "选择 extract.json", "", "JSON 文件 (*.json)")
    if not extract_path:
        sys.exit(0)

    split_path, _ = QFileDialog.getOpenFileName(None, "选择 split.json", "", "JSON 文件 (*.json)")
    if not split_path:
        sys.exit(0)

    timeline_path, _ = QFileDialog.getOpenFileName(None, "选择 timeline.json", "", "JSON 文件 (*.json)")
    if not timeline_path:
        sys.exit(0)

    try:
        window = PlayerWindow(extract_path, split_path, timeline_path)
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.error(f"播放器初始化失败：{e}")
        QMessageBox.critical(None, "错误", f"初始化失败：\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main_player()