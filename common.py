import sys
import logging
from typing import Optional

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPixmap, QPainter, QKeyEvent
from PyQt6.QtWidgets import QWidget, QFileDialog, QMessageBox

logger = logging.getLogger(__name__)


class BaseImageWindow(QWidget):
    ZOOM_STEP = 1.1
    MIN_SCALE = 0.01
    MAX_SCALE = 20.0

    def __init__(self, pixmap: QPixmap, img_w: int, img_h: int):
        super().__init__()
        self.original_pixmap = pixmap
        self.img_w = img_w
        self.img_h = img_h
        self.scale = 1.0
        self.scaled_pixmap: Optional[QPixmap] = None
        self._update_scaled_pixmap()
        self.setMouseTracking(True)

    # ----- 缩放与缓存 -----
    def _display_size(self) -> tuple[int, int]:
        return int(round(self.img_w * self.scale)), int(round(self.img_h * self.scale))

    def _update_scaled_pixmap(self):
        if self.original_pixmap and not self.original_pixmap.isNull():
            w, h = self._display_size()
            self.scaled_pixmap = self.original_pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            self.scaled_pixmap = None

    def set_pixmap(self, pixmap: QPixmap, img_w: int, img_h: int):
        """更换当前显示的原始图（例如 split 翻页时）。"""
        self.original_pixmap = pixmap
        self.img_w = img_w
        self.img_h = img_h
        self._update_scaled_pixmap()
        self.update()

    def apply_scale(self, new_scale: float):
        self.scale = max(self.MIN_SCALE, min(self.MAX_SCALE, new_scale))
        self._update_scaled_pixmap()
        self.update()

    def zoom_in(self):
        self.zoom(self.ZOOM_STEP)

    def zoom_out(self):
        self.zoom(1.0 / self.ZOOM_STEP)

    def zoom(self, factor: float):
        """子类实现，可能需要调整偏移。"""
        raise NotImplementedError

    # ----- 屏幕中心与绘制辅助 -----
    def _screen_center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def draw_scaled_pixmap(self, painter: QPainter, top_left: QPointF):
        if self.scaled_pixmap:
            painter.drawPixmap(
                int(round(top_left.x())),
                int(round(top_left.y())),
                self.scaled_pixmap,
            )

    # ----- 对话框与确认 -----
    def confirm_quit(self) -> bool:
        reply = QMessageBox.question(
            self, "退出确认", "确定要退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def show_export_dialog(self, default_name: str) -> Optional[str]:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 JSON", default_name, "JSON 文件 (*.json)"
        )
        return path

    def show_import_dialog(self) -> Optional[str]:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON", "", "JSON 文件 (*.json)"
        )
        return path

    # ----- 键盘事件 -----
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Plus:
            self.zoom_in()
        elif key == Qt.Key.Key_Minus:
            self.zoom_out()
        elif key == Qt.Key.Key_Escape:
            if self.confirm_quit():
                self.close()
        elif key == Qt.Key.Key_S or key == Qt.Key.Key_M:
            self.on_export()
        elif key == Qt.Key.Key_I:
            self.on_import()
        elif key == Qt.Key.Key_Z:
            self.on_undo()
        else:
            super().keyPressEvent(event)

    # 子类必须实现的抽象方法
    def on_export(self):
        raise NotImplementedError

    def on_import(self):
        raise NotImplementedError

    def on_undo(self):
        raise NotImplementedError