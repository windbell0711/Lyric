import sys
import json
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from models import MarkItem
from common import BaseImageWindow

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)


class ExtractWindow(BaseImageWindow):
    def __init__(self, image_path: str, rect_w: int, rect_h: int):
        self.image_path = image_path
        self.rect_w = rect_w
        self.rect_h = rect_h

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            raise ValueError(f"无法加载图片: {image_path}")

        super().__init__(pixmap, pixmap.width(), pixmap.height())

        self.offset_x = 0.0
        self.offset_y = 0.0
        self.marks: List[MarkItem] = []
        self.next_mark_id = 1

        self.dragging = False
        self.last_mouse_pos: Optional[QPointF] = None

        self.setWindowTitle("图片标记器 - Extract")
        logger.debug(f"Extract 初始化：图片={image_path}, 矩形={rect_w}x{rect_h}")

    # ----- 缩放（带偏移调整）-----
    def zoom(self, factor: float):
        if factor <= 0:
            return
        old_img_cx = self.img_w / 2 - self.offset_x / self.scale
        old_img_cy = self.img_h / 2 - self.offset_y / self.scale
        new_scale = self.scale * factor
        new_scale = max(self.MIN_SCALE, min(self.MAX_SCALE, new_scale))
        self.apply_scale(new_scale)
        self.offset_x = new_scale * (self.img_w / 2 - old_img_cx)
        self.offset_y = new_scale * (self.img_h / 2 - old_img_cy)
        self.update()
        logger.debug(f"Extract 缩放：scale={self.scale:.4f}")

    def reset_center(self):
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.update()
        logger.debug("图片已重置居中")

    # ----- 坐标计算 -----
    def _image_top_left(self) -> QPointF:
        center = self._screen_center()
        disp_w, disp_h = self._display_size()
        left = center.x() + self.offset_x - disp_w / 2
        top = center.y() + self.offset_y - disp_h / 2
        return QPointF(left, top)

    def _screen_center_to_image_coord(self) -> tuple[float, float]:
        img_cx = self.img_w / 2 - self.offset_x / self.scale
        img_cy = self.img_h / 2 - self.offset_y / self.scale
        return img_cx, img_cy

    # ----- 标记操作 -----
    def record_mark(self):
        cx, cy = self._screen_center_to_image_coord()
        left = cx - self.rect_w / 2
        top = cy - self.rect_h / 2
        right = left + self.rect_w
        bottom = top + self.rect_h

        if not (0 <= left and 0 <= top and right <= self.img_w and bottom <= self.img_h):
            reply = QMessageBox.question(
                self, "越界提醒", "当前矩形区域超出图片边界，是否仍然记录？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        mark = MarkItem(id=self.next_mark_id, cx=cx, cy=cy)
        self.next_mark_id += 1
        self.marks.append(mark)
        self.update()
        logger.info(f"记录标记 ID={mark.id} ({cx:.2f}, {cy:.2f})")

    def undo_last_mark(self):
        if self.marks:
            removed = self.marks.pop()
            self.update()
            logger.info(f"撤销标记 ID={removed.id}")
        else:
            logger.debug("标记列表为空")

    # ----- 导入/导出 -----
    def on_export(self):
        path = self.show_export_dialog("extract.json")
        if not path:
            return
        data = {
            "image_path": self.image_path,
            "width": self.rect_w,
            "height": self.rect_h,
            "marks": [
                {"id": m.id, "cx": round(m.cx), "cy": round(m.cy)}
                for m in self.marks
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"导出成功：{path}")
        except Exception as e:
            logger.error(f"导出失败：{e}")
            QMessageBox.critical(self, "导出失败", f"无法写入文件：\n{e}")

    def on_import(self):
        path = self.show_import_dialog()
        if not path:
            return
        reply = QMessageBox.question(
            self, "导入确认", "导入将丢弃当前所有标记，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            new_marks = [MarkItem(id=m["id"], cx=m["cx"], cy=m["cy"]) for m in data["marks"]]
            self.marks = new_marks
            self.next_mark_id = max([m.id for m in new_marks], default=0) + 1
            self.update()
            logger.info(f"导入成功：{path}，标记数={len(self.marks)}")
        except Exception as e:
            logger.error(f"导入失败：{e}")
            QMessageBox.critical(self, "导入失败", f"无法读取文件：\n{e}")

    def on_undo(self):
        self.undo_last_mark()

    # ----- 鼠标事件（拖动图片）-----
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.dragging and self.last_mouse_pos is not None:
            delta = event.position() - self.last_mouse_pos
            self.offset_x += delta.x()
            self.offset_y += delta.y()
            self.last_mouse_pos = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ----- 键盘 -----
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Equal:
            self.reset_center()
        elif event.key() == Qt.Key.Key_Space:
            self.record_mark()
        else:
            super().keyPressEvent(event)

    # ----- 绘制 -----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 图片
        if self.scaled_pixmap:
            self.draw_scaled_pixmap(painter, self._image_top_left())

        # 已标记矩形
        if self.marks:
            pen = QPen(QColor(255, 0, 0))
            pen.setWidth(2)
            painter.setPen(pen)
            top_left = self._image_top_left()
            rect_w_disp = self.rect_w * self.scale
            rect_h_disp = self.rect_h * self.scale
            for mark in self.marks:
                left = top_left.x() + (mark.cx - self.rect_w / 2) * self.scale
                top = top_left.y() + (mark.cy - self.rect_h / 2) * self.scale
                painter.drawRect(int(round(left)), int(round(top)),
                                 int(round(rect_w_disp)), int(round(rect_h_disp)))

        # 屏幕中心矩形
        center = self._screen_center()
        rect_w_disp, rect_h_disp = self.rect_w * self.scale, self.rect_h * self.scale
        left = center.x() - rect_w_disp / 2
        top = center.y() - rect_h_disp / 2
        pen = QPen(QColor(0, 120, 255))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(int(round(left)), int(round(top)),
                         int(round(rect_w_disp)), int(round(rect_h_disp)))


def main_extract():
    app = QApplication(sys.argv)
    image_path, _ = QFileDialog.getOpenFileName(None, "选择要标记的图片", "",
                                                "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff)")
    if not image_path:
        sys.exit(0)
    rect_w, ok1 = QInputDialog.getInt(None, "矩形宽度", "请输入标记矩形的宽度（像素）:", 100, 1, 100000, 1)
    if not ok1:
        sys.exit(0)
    rect_h, ok2 = QInputDialog.getInt(None, "矩形高度", "请输入标记矩形的高度（像素）:", 100, 1, 100000, 1)
    if not ok2:
        sys.exit(0)
    window = ExtractWindow(image_path, rect_w, rect_h)
    window.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main_extract()