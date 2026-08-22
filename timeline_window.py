import sys
import json
import logging
from typing import List, Optional
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QPointF, QRectF, QUrl
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QKeyEvent, QMouseEvent
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QInputDialog

from models import SplitItem, SplitRegion, TimelineExtract, TimelineSplit
from common import BaseImageWindow

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


@dataclass
class TimelineAction:
    """用于撤销的操作记录。"""
    type: str  # "split" 或 "extract"
    extract_idx: int
    split_idx: Optional[int] = None
    old_extract_idx: Optional[int] = None
    new_extract_idx: Optional[int] = None


class TimelineWindow(BaseImageWindow):
    def __init__(self, extract_json_path: str, split_json_path: str, audio_path: str,
                 extract_fade_out_ms: int = 300, split_fade_in_ms: int = 200):
        # 加载 extract.json
        with open(extract_json_path, "r", encoding="utf-8") as f:
            extract_data = json.load(f)
        # 加载 split.json
        with open(split_json_path, "r", encoding="utf-8") as f:
            split_data = json.load(f)

        self.image_path = extract_data["image_path"]
        self.rect_w = int(extract_data["width"])
        self.rect_h = int(extract_data["height"])
        self.marks = extract_data["marks"]
        self.splits_data = split_data["splits"]

        # 动画时长
        self.extract_fade_out_ms = extract_fade_out_ms
        self.split_fade_in_ms = split_fade_in_ms

        # 加载原图
        original = QPixmap(self.image_path)
        if original.isNull():
            raise ValueError(f"无法加载原图: {self.image_path}")

        # 为每个 extract 标记裁剪子图（同 split 窗口）
        self.sub_images: List[QPixmap] = []
        for mark in self.marks:
            self.sub_images.append(self._crop_sub_image(original, mark["cx"], mark["cy"]))

        # 初始化显示：第一个子图
        first_sub = self.sub_images[0] if self.sub_images else QPixmap()
        super().__init__(first_sub, self.rect_w, self.rect_h)

        # 构建 extract_id -> regions 映射
        self.extract_regions: dict[int, List[SplitRegion]] = {}
        for item in self.splits_data:
            rid = item["extract_id"]
            regions = [SplitRegion(id=r["id"], x=r["x"], y=r["y"],
                                   width=r["width"], height=r["height"])
                       for r in item["regions"]]
            self.extract_regions[rid] = regions

        # 构建 timeline 数据结构
        self.timeline_extracts: List[TimelineExtract] = []
        for mark in self.marks:
            extract_id = mark["id"]
            regions = self.extract_regions.get(extract_id, [])
            t_extract = TimelineExtract(extract_id=extract_id)
            for region in regions:
                t_extract.splits.append(TimelineSplit(split_id=region.id))
            self.timeline_extracts.append(t_extract)

        # 当前状态
        self.current_extract_idx = 0
        self.current_split_idx = 0

        # 操作栈
        self.action_stack: List[TimelineAction] = []

        # 音频播放器
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setSource(QUrl.fromLocalFile(audio_path))
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # 记录是否已开始播放过（用于第一次记录 start_time）
        self.started_playback = False

        # 设置窗口
        self.setWindowTitle("时间轴 - Timeline")
        self._fit_to_screen()
        logger.debug(f"Timeline 初始化完成，extract数={len(self.timeline_extracts)}")

    # ------------------------------------------------------------------
    # 子图裁剪（同 split 窗口）
    # ------------------------------------------------------------------
    def _crop_sub_image(self, original: QPixmap, cx: float, cy: float) -> QPixmap:
        left = int(round(cx - self.rect_w / 2))
        top = int(round(cy - self.rect_h / 2))
        right = left + self.rect_w
        bottom = top + self.rect_h

        sub = QPixmap(self.rect_w, self.rect_h)
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
            crop = original.copy(src_left, src_top, valid_w, valid_h)
            painter = QPainter(sub)
            painter.drawPixmap(dst_left, dst_top, crop)
            painter.end()

        return sub

    # ------------------------------------------------------------------
    # 缩放与显示
    # ------------------------------------------------------------------
    def _fit_to_screen(self):
        if self.width() > 0 and self.height() > 0:
            margin = 0.9
            self.scale = min(
                self.width() * margin / self.rect_w,
                self.height() * margin / self.rect_h
            )
            self.scale = max(self.MIN_SCALE, min(self.MAX_SCALE, self.scale))
            self.apply_scale(self.scale)

    def zoom(self, factor: float):
        if factor <= 0:
            return
        new_scale = self.scale * factor
        self.apply_scale(new_scale)
        logger.debug(f"Timeline 缩放：scale={self.scale:.4f}")

    def _sub_image_top_left(self) -> QPointF:
        center = self._screen_center()
        disp_w, disp_h = self._display_size()
        return QPointF(center.x() - disp_w / 2, center.y() - disp_h / 2)

    def _screen_to_sub_coord(self, screen_pos: QPointF) -> tuple[float, float]:
        top_left = self._sub_image_top_left()
        return (
            (screen_pos.x() - top_left.x()) / self.scale,
            (screen_pos.y() - top_left.y()) / self.scale,
        )

    def _sub_to_screen_coord(self, x: float, y: float) -> QPointF:
        top_left = self._sub_image_top_left()
        return QPointF(top_left.x() + x * self.scale, top_left.y() + y * self.scale)

    # ------------------------------------------------------------------
    # 当前 extract 的 regions 和 timeline 对象
    # ------------------------------------------------------------------
    def _current_extract(self) -> TimelineExtract:
        return self.timeline_extracts[self.current_extract_idx]

    def _current_regions(self) -> List[SplitRegion]:
        extract_id = self._current_extract().extract_id
        return self.extract_regions.get(extract_id, [])

    # ------------------------------------------------------------------
    # 音频控制
    # ------------------------------------------------------------------
    def start_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return  # 已经在播放
        self.player.play()
        # 第一次播放时记录第一个 extract 的开始时间
        if not self.started_playback and self.timeline_extracts:
            self.started_playback = True
            first_extract = self.timeline_extracts[0]
            if first_extract.start_time is None:
                first_extract.start_time = self.player.position()
                logger.info(f"记录 extract {first_extract.extract_id} 开始时间 {first_extract.start_time} ms")
        logger.debug("音频开始播放")

    def pause_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            logger.debug("音频暂停")

    def toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause_playback()
        else:
            self.start_playback()

    def _on_player_error(self, error, error_string):
        logger.error(f"音频播放错误: {error} - {error_string}")
        QMessageBox.critical(self, "播放错误", f"无法播放音频：\n{error_string}")

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            logger.info("音频播放结束")
            QMessageBox.information(self, "播放结束", "音频已播放完毕，请按 S 或 M 导出 timeline.json")

    # ------------------------------------------------------------------
    # 空格处理：记录时间 / 翻页
    # ------------------------------------------------------------------
    def handle_space(self):
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            QMessageBox.information(self, "提示", "请先按 B 开始播放音频")
            return

        current_extract = self._current_extract()
        regions = self._current_regions()

        # 如果当前 extract 还有未变绿的 split
        if self.current_split_idx < len(regions):
            split = current_extract.splits[self.current_split_idx]
            split.time = self.player.position()
            logger.info(f"记录 split {split.split_id} 时间 {split.time} ms")

            action = TimelineAction(
                type="split",
                extract_idx=self.current_extract_idx,
                split_idx=self.current_split_idx,
            )
            self.action_stack.append(action)

            self.current_split_idx += 1
            self.update()

        else:
            # 所有 split 已变绿，翻到下一个 extract
            if self.current_extract_idx + 1 < len(self.timeline_extracts):
                old_idx = self.current_extract_idx
                new_idx = old_idx + 1
                new_extract = self.timeline_extracts[new_idx]
                new_extract.start_time = self.player.position()
                logger.info(f"翻页：进入 extract {new_extract.extract_id}，开始时间 {new_extract.start_time} ms")

                action = TimelineAction(
                    type="extract",
                    extract_idx=old_idx,
                    old_extract_idx=old_idx,
                    new_extract_idx=new_idx,
                )
                self.action_stack.append(action)

                self.current_extract_idx = new_idx
                self.current_split_idx = 0
                self.set_pixmap(self.sub_images[new_idx], self.rect_w, self.rect_h)
                self._fit_to_screen()
                self.update()
            else:
                QMessageBox.information(self, "提示", "已经是最后一个 extract")

    # ------------------------------------------------------------------
    # 撤销
    # ------------------------------------------------------------------
    def undo(self):
        if not self.action_stack:
            QMessageBox.information(self, "提示", "没有可撤销的操作")
            return

        action = self.action_stack.pop()
        if action.type == "split":
            extract = self.timeline_extracts[action.extract_idx]
            if action.split_idx is not None and action.split_idx < len(extract.splits):
                split = extract.splits[action.split_idx]
                split.time = None
                self.current_split_idx = action.split_idx
                logger.info(f"撤销 split {split.split_id} 的时间记录")
                self.update()
        elif action.type == "extract":
            if action.old_extract_idx is not None and action.new_extract_idx is not None:
                new_extract = self.timeline_extracts[action.new_extract_idx]
                new_extract.start_time = None

                self.current_extract_idx = action.old_extract_idx
                old_extract = self.timeline_extracts[action.old_extract_idx]
                self.current_split_idx = len(old_extract.splits)

                self.set_pixmap(self.sub_images[self.current_extract_idx], self.rect_w, self.rect_h)
                self._fit_to_screen()
                logger.info(f"撤销翻页，回到 extract {old_extract.extract_id}")
                self.update()

    # ------------------------------------------------------------------
    # 导出 timeline.json
    # ------------------------------------------------------------------
    def export_timeline(self):
        path = self.show_export_dialog("timeline.json")
        if not path:
            return

        data = {
            "audio_path": self.player.source().toLocalFile() if self.player.source().isValid() else "",
            "extract_fade_out_ms": self.extract_fade_out_ms,
            "split_fade_in_ms": self.split_fade_in_ms,
            "font_colors": ["#C1C2C3", "#000000", "#EEEEEE"],  # 默认
            "extract_timings": [
                {
                    "extract_id": ext.extract_id,
                    "start_time": ext.start_time,
                    "splits": [
                        {"split_id": sp.split_id, "time": sp.time}
                        for sp in ext.splits
                    ],
                }
                for ext in self.timeline_extracts
            ],
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"导出 timeline.json 成功：{path}")
            QMessageBox.information(self, "导出成功", f"已保存到 {path}")
        except Exception as e:
            logger.error(f"导出失败：{e}")
            QMessageBox.critical(self, "导出失败", f"无法写入文件：\n{e}")

    # ------------------------------------------------------------------
    # 键盘事件
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key == Qt.Key.Key_Space:
            self.handle_space()
        elif key == Qt.Key.Key_B:
            self.start_playback()
        elif key == Qt.Key.Key_P:
            self.toggle_play_pause()
        elif key == Qt.Key.Key_Z:
            self.undo()
        elif key == Qt.Key.Key_S or key == Qt.Key.Key_M:
            self.export_timeline()
        elif key == Qt.Key.Key_Escape:
            reply = QMessageBox.question(
                self, "退出确认", "确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.close()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 绘制子图
        if self.scaled_pixmap:
            self.draw_scaled_pixmap(painter, self._sub_image_top_left())

        # 绘制当前 extract 的所有 split 框
        regions = self._current_regions()
        current_extract = self._current_extract()

        for idx, region in enumerate(regions):
            is_green = idx < self.current_split_idx
            color = QColor(0, 200, 0) if is_green else QColor(0, 120, 255)
            pen = QPen(color)
            pen.setWidth(2)
            painter.setPen(pen)

            top_left = self._sub_to_screen_coord(region.x, region.y)
            bottom_right = self._sub_to_screen_coord(region.x + region.width, region.y + region.height)
            rect = QRectF(top_left, bottom_right)
            painter.drawRect(rect)

        # 显示当前播放时间
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState or self.player.position() > 0:
            time_text = f"{self.player.position()} ms"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(10, 30, time_text)

        # 显示当前 extract 序号
        if self.timeline_extracts:
            page_text = f"Extract {self.current_extract_idx + 1}/{len(self.timeline_extracts)}"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(10, 50, page_text)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def main_timeline():
    app = QApplication(sys.argv)

    # 选择 extract.json
    extract_path, _ = QFileDialog.getOpenFileName(None, "选择 extract.json", "", "JSON 文件 (*.json)")
    if not extract_path:
        sys.exit(0)

    # 选择 split.json
    split_path, _ = QFileDialog.getOpenFileName(None, "选择 split.json", "", "JSON 文件 (*.json)")
    if not split_path:
        sys.exit(0)

    # 选择音频文件
    audio_path, _ = QFileDialog.getOpenFileName(None, "选择音频文件", "",
                                                "音频文件 (*.mp3 *.wav *.flac *.m4a)")
    if not audio_path:
        sys.exit(0)

    # 输入淡出和淡入时长
    fade_out, ok1 = QInputDialog.getInt(None, "淡出时长", "请输入 extract 淡出时长（毫秒）:", 300, 0, 100000, 1)
    if not ok1:
        sys.exit(0)
    fade_in, ok2 = QInputDialog.getInt(None, "淡入时长", "请输入 split 淡入时长（毫秒）:", 200, 0, 100000, 1)
    if not ok2:
        sys.exit(0)

    try:
        window = TimelineWindow(extract_path, split_path, audio_path, fade_out, fade_in)
        window.showFullScreen()
        # 开始提示
        QMessageBox.information(window, "操作提示",
                                "按 B 开始播放音频\n按 P 暂停/继续\n按空格记录时间/翻页\n按 Z 撤销\n按 S/M 导出")
        sys.exit(app.exec())
    except Exception as e:
        logger.error(f"初始化 Timeline 失败：{e}")
        QMessageBox.critical(None, "错误", f"初始化失败：\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main_timeline()