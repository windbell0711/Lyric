import sys
import json
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from models import SplitItem, SplitRegion
from common import BaseImageWindow

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)


class SplitWindow(BaseImageWindow):
    def __init__(self, extract_json_path: str):
        self.extract_json_path = extract_json_path
        with open(extract_json_path, "r", encoding="utf-8") as f:
            extract_data = json.load(f)

        self.image_path = extract_data["image_path"]
        self.rect_w = int(extract_data["width"])
        self.rect_h = int(extract_data["height"])
        self.marks = extract_data["marks"]  # 列表，每项含 id, cx, cy

        # 加载原图
        original = QPixmap(self.image_path)
        if original.isNull():
            raise ValueError(f"无法加载原图: {self.image_path}")

        # 裁剪每个子图
        self.sub_images: List[QPixmap] = []
        for mark in self.marks:
            self.sub_images.append(self._crop_sub_image(original, mark["cx"], mark["cy"]))

        # 当前页索引
        self.current_index = 0
        # 分割数据
        self.split_items: List[SplitItem] = [
            SplitItem(extract_id=mark["id"], regions=[]) for mark in self.marks
        ]
        self.next_region_id = 1

        # 初始化基类：显示第一个子图
        first_sub = self.sub_images[0] if self.sub_images else QPixmap()
        super().__init__(first_sub, self.rect_w, self.rect_h)

        self.dragging_box = False
        self.box_start: Optional[QPointF] = None
        self.box_current: Optional[QPointF] = None

        self.setWindowTitle("分割器 - Split")
        self._fit_to_screen()
        logger.debug(f"Split 初始化：extract={extract_json_path}, 标记数={len(self.marks)}")

    # ----- 裁剪子图（越界填充透明）-----
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

    # ----- 翻页 -----
    def _fit_to_screen(self):
        if self.width() > 0 and self.height() > 0:
            margin = 0.9
            self.scale = min(
                self.width() * margin / self.rect_w,
                self.height() * margin / self.rect_h
            )
            self.scale = max(self.MIN_SCALE, min(self.MAX_SCALE, self.scale))
            self.apply_scale(self.scale)

    def go_to_previous(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.set_pixmap(self.sub_images[self.current_index], self.rect_w, self.rect_h)
            self._fit_to_screen()
            logger.debug(f"翻到上一页：{self.current_index+1}/{len(self.sub_images)}")
        else:
            QMessageBox.information(self, "提示", "已经是第一页了")

    def go_to_next(self):
        if self.current_index < len(self.sub_images) - 1:
            self.current_index += 1
            self.set_pixmap(self.sub_images[self.current_index], self.rect_w, self.rect_h)
            self._fit_to_screen()
            logger.debug(f"翻到下一页：{self.current_index+1}/{len(self.sub_images)}")
        else:
            QMessageBox.information(self, "提示", "已经是最后一页了")

    # ----- 缩放 -----
    def zoom(self, factor: float):
        if factor <= 0:
            return
        new_scale = self.scale * factor
        self.apply_scale(new_scale)
        logger.debug(f"Split 缩放：scale={self.scale:.4f}")

    # ----- 坐标换算 -----
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

    # ----- 分割操作 -----
    def add_region(self, rel_rect: QRectF):
        x = int(round(rel_rect.x()))
        y = int(round(rel_rect.y()))
        w = int(round(rel_rect.width()))
        h = int(round(rel_rect.height()))

        # 裁剪到有效范围
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x + w > self.rect_w:
            w = self.rect_w - x
        if y + h > self.rect_h:
            h = self.rect_h - y
        if w <= 0 or h <= 0:
            return

        region = SplitRegion(id=self.next_region_id, x=x, y=y, width=w, height=h)
        self.next_region_id += 1
        self.split_items[self.current_index].regions.append(region)
        self.update()
        logger.info(f"添加子框 ID={region.id} 到 extract {self.split_items[self.current_index].extract_id}")

    def undo_last_region(self):
        item = self.split_items[self.current_index]
        if item.regions:
            removed = item.regions.pop()
            self.update()
            logger.info(f"撤销子框 ID={removed.id}")
        else:
            QMessageBox.information(self, "提示", "当前页面没有可撤销的框")

    # ----- 导入/导出 -----
    def on_export(self):
        path = self.show_export_dialog("split.json")
        if not path:
            return
        data = {
            "image_path": self.image_path,
            "width": self.rect_w,
            "height": self.rect_h,
            "splits": [
                {
                    "extract_id": item.extract_id,
                    "regions": [
                        {"id": r.id, "x": r.x, "y": r.y, "width": r.width, "height": r.height}
                        for r in item.regions
                    ],
                }
                for item in self.split_items
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
            self, "导入确认", "导入将丢弃当前所有分割数据，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 检查是否匹配
            if data.get("image_path") != self.image_path:
                QMessageBox.warning(self, "警告", "导入文件与原图路径不同，可能无法正确显示")
            new_splits = []
            for s in data["splits"]:
                regions = [SplitRegion(id=r["id"], x=r["x"], y=r["y"], width=r["width"], height=r["height"])
                           for r in s["regions"]]
                new_splits.append(SplitItem(extract_id=s["extract_id"], regions=regions))
            self.split_items = new_splits
            max_id = max([r.id for item in new_splits for r in item.regions], default=0)
            self.next_region_id = max_id + 1
            self.current_index = 0
            self.set_pixmap(self.sub_images[0], self.rect_w, self.rect_h)
            self.update()
            logger.info(f"导入成功：{path}")
        except Exception as e:
            logger.error(f"导入失败：{e}")
            QMessageBox.critical(self, "导入失败", f"无法读取文件：\n{e}")

    def on_undo(self):
        self.undo_last_region()

    # ----- 鼠标框选 -----
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            rel = self._screen_to_sub_coord(event.position())
            if 0 <= rel[0] <= self.rect_w and 0 <= rel[1] <= self.rect_h:
                self.dragging_box = True
                self.box_start = event.position()
                self.box_current = event.position()
                self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.dragging_box:
            self.box_current = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.dragging_box:
            self.dragging_box = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self.box_start and self.box_current:
                rel_start = self._screen_to_sub_coord(self.box_start)
                rel_end = self._screen_to_sub_coord(self.box_current)
                left = min(rel_start[0], rel_end[0])
                top = min(rel_start[1], rel_end[1])
                right = max(rel_start[0], rel_end[0])
                bottom = max(rel_start[1], rel_end[1])
                rect = QRectF(left, top, right - left, bottom - top)
                if rect.width() > 1 and rect.height() > 1:
                    self.add_region(rect)
            self.box_start = None
            self.box_current = None
            self.update()

    # ----- 键盘 -----
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Left:
            self.go_to_previous()
        elif event.key() == Qt.Key.Key_Right:
            self.go_to_next()
        else:
            super().keyPressEvent(event)

    # ----- 绘制 -----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 图片
        if self.scaled_pixmap:
            self.draw_scaled_pixmap(painter, self._sub_image_top_left())

        # 已记录子框
        item = self.split_items[self.current_index]
        if item.regions:
            pen = QPen(QColor(255, 0, 0))
            pen.setWidth(2)
            painter.setPen(pen)
            for region in item.regions:
                top_left = self._sub_to_screen_coord(region.x, region.y)
                bottom_right = self._sub_to_screen_coord(region.x + region.width, region.y + region.height)
                painter.drawRect(QRectF(top_left, bottom_right))

        # 正在拖拽的框
        if self.dragging_box and self.box_start and self.box_current:
            pen = QPen(QColor(0, 120, 255))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            rect = QRectF(self.box_start, self.box_current).normalized()
            painter.drawRect(rect)


def main_split():
    app = QApplication(sys.argv)
    extract_path, _ = QFileDialog.getOpenFileName(None, "选择 extract.json", "", "JSON 文件 (*.json)")
    if not extract_path:
        sys.exit(0)
    window = SplitWindow(extract_path)
    window.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main_split()