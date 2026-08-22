import sys
import os
import json
import logging
import time
import tempfile
import zipfile
import urllib.request
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, QUrl
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QImage, QMouseEvent, QKeyEvent, QCloseEvent
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import QApplication, QWidget, QFileDialog, QInputDialog, QMessageBox

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

    def __init__(self, dly_path: str):
        super().__init__()

        # ----- 从 .dly 包中读取三个 JSON 与媒体文件 -----
        with zipfile.ZipFile(dly_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            extract_data = json.loads(zf.read(manifest["extract_json"]))
            split_data = json.loads(zf.read(manifest["split_json"]))
            timeline_data = json.loads(zf.read(manifest["timeline_json"]))
            image_bytes = zf.read(manifest["image_file"])
            audio_bytes = zf.read(manifest["audio_file"])
            audio_suffix = os.path.splitext(manifest["audio_file"])[1] or ".bin"

        self.image_path = extract_data["image_path"]
        self.rect_w = int(extract_data["width"])
        self.rect_h = int(extract_data["height"])
        self.marks = extract_data["marks"]
        self.splits_data = split_data["splits"]
        self.extract_timings = timeline_data["extract_timings"]

        # 二值化阈值（extract.json 全局参数，缺失时默认 128）
        self.threshold = int(extract_data.get("threshold", 128))
        logger.info(f"二值化阈值：{self.threshold}")

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

        # ----- 加载原图（从包内字节流）并裁剪二值化子图（透明背景）-----
        original = QPixmap()
        if not original.loadFromData(image_bytes):
            raise ValueError(f"无法解码包内图片: {manifest['image_file']}")

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

        # ----- 音频播放器（音频解包到临时文件后播放）-----
        self._temp_audio_path: Optional[str] = None
        fd, self._temp_audio_path = tempfile.mkstemp(
            prefix="dly_audio_", suffix=audio_suffix
        )
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)
        logger.info(f"音频已解包到临时文件: {self._temp_audio_path}")

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setSource(QUrl.fromLocalFile(self._temp_audio_path))

        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # ----- 窗口设置（透明无边框，始终置顶）-----
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 关键：QWidget 默认焦点策略为 NoFocus，无边框窗口将永远无法获得键盘焦点，
        # 真实按键（P/M/Esc）根本不会到达 keyPressEvent。必须显式允许强焦点。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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

        return self._binarize_image(sub, self.threshold)

    def _binarize_image(self, image: QImage, threshold: int = 128) -> QPixmap:
        """二值化并设置 alpha：灰度大于阈值的像素透明，其余不透明。"""
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                gray = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
                if gray > threshold:
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
            # 默认单曲循环：回到起点并重置歌词显示状态，从头重新播放
            logger.info("音频播放结束，单曲循环：从头重新播放")
            self.player.setPosition(0)
            self.current_extract_id = None
            self.pending_extract_id = None
            self.extract_fade_out_alpha = 0.0
            self.split_alphas.clear()
            self.split_target_alphas.clear()
            self.split_fade_in_start.clear()
            self.player.play()
            self.timer.start()
            self.update()

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
                fade_out_ms = max(self.extract_fade_out_ms, 1)
                alpha_decrease = delta * 1000 / fade_out_ms
                self.extract_fade_out_alpha -= alpha_decrease
                # 必须用 <= 0.0：当减量恰好整除 1.0 时会精确落在 0.0，
                # 若只判断 < 0.0 将永远无法触发切换，导致不显示任何歌词
                if self.extract_fade_out_alpha <= 0.0:
                    self.extract_fade_out_alpha = 0.0
                    self._perform_extract_switch()
                self.update()

            # 2. split 淡入
            for split_id, target in list(self.split_target_alphas.items()):
                if target == 1.0 and split_id in self.split_alphas:
                    current_alpha = self.split_alphas[split_id]
                    if current_alpha < 1.0:
                        fade_in_ms = max(self.split_fade_in_ms, 1)
                        alpha_increase = delta * 1000 / fade_in_ms
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
    # 焦点管理：无边框透明窗口不会自动获得键盘焦点，
    # 在显示时主动激活并抢占焦点，保证快捷键（P/M/Esc）可用
    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.raise_()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    # ------------------------------------------------------------------
    # 鼠标事件：左键拖动，右键关闭
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # 点击窗口时重新获得焦点：拖动后按键（如 Esc）仍需生效
            self.activateWindow()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
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

    def closeEvent(self, event: QCloseEvent):
        """关闭窗口。

        注意：不要在 GUI 线程同步析构 QMediaPlayer（self.player = None 会立即
        删除 C++ 对象并等待 FFmpeg 引擎线程退出，可能长时间阻塞导致窗口
        "按 Esc 无响应/卡死"）。这里改用 deleteLater() 把销毁交给事件循环，
        窗口立即关闭，再延迟退出应用让销毁与临时音频清理有机会完成。
        """
        self.timer.stop()

        player = self.player
        audio_output = self.audio_output
        temp_path = self._temp_audio_path
        self.player = None
        self.audio_output = None
        self._temp_audio_path = None

        if player is not None:
            try:
                player.stop()
            except Exception:
                pass
            player.deleteLater()
        if audio_output is not None:
            audio_output.deleteLater()
        # 保留引用，避免解释器退出时对活跃播放器做同步析构
        self._shutdown_refs = (player, audio_output)

        if temp_path:
            def _try_remove_temp():
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                        logger.debug(f"已删除临时音频: {temp_path}")
                except OSError as e:
                    logger.warning(f"删除临时音频失败（文件可能仍被系统占用）: {e}")

            _try_remove_temp()  # 立即尝试（stop 后引擎可能已释放句柄）
            # 引擎释放文件句柄是异步的，延迟重试一次
            QTimer.singleShot(500, _try_remove_temp)

        # 让事件循环先处理 deleteLater 的延迟销毁，再退出应用
        QTimer.singleShot(800, lambda: QApplication.instance().quit())
        super().closeEvent(event)


def _download_dly(url: str, timeout: int = 30) -> str:
    """从 URL 下载 .dly 播放包到临时文件，返回临时文件路径。

    下载失败（网址无效、网络错误、返回内容不是 .dly）时抛出异常。
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Lyric-Player)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()

    # .dly 为 ZIP 容器，校验文件头，避免把非 .dly 内容当播放包使用
    if not data.startswith(b"PK"):
        raise ValueError("下载的内容不是有效的 .dly 播放包（ZIP 容器）")

    fd, tmp_path = tempfile.mkstemp(prefix="dly_remote_", suffix=".dly")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return tmp_path


def main_player():
    app = QApplication(sys.argv)
    # 关闭窗口后不立即退出：closeEvent 会延迟销毁播放器并清理临时文件，
    # 之后由 closeEvent 内的定时器显式退出，避免在解释器退出时同步析构播放器
    app.setQuitOnLastWindowClosed(False)

    # 1. 先询问 .dly 播放包的网址；留空则回退到本地文件选择
    url, ok = QInputDialog.getText(
        None,
        "输入 .dly 网址",
        "请输入 .dly 播放包的网址（留空则选择本地文件）：",
    )
    if not ok:
        sys.exit(0)
    url = url.strip()

    dly_path: Optional[str] = None
    downloaded_tmp: Optional[str] = None

    if url:
        # 2. 网址非空：先尝试从该网址获取 .dly 文件
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            downloaded_tmp = _download_dly(url)
            dly_path = downloaded_tmp
            logger.info(f"已从网址下载 .dly 文件: {url}")
        except Exception as e:
            logger.error(f"下载 .dly 文件失败：{url} - {e}")
            QMessageBox.critical(
                None, "网址无效", f"无法从该网址获取 .dly 文件：\n{e}"
            )
            sys.exit(1)
        finally:
            QApplication.restoreOverrideCursor()
    else:
        # 3. 网址为空：正常索要本地文件
        dly_path, _ = QFileDialog.getOpenFileName(
            None, "选择 .dly 播放包", "", "Lyric 播放包 (*.dly)"
        )
        if not dly_path:
            sys.exit(0)

    try:
        window = PlayerWindow(dly_path)
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.error(f"播放器初始化失败：{e}")
        QMessageBox.critical(None, "错误", f"初始化失败：\n{e}")
        sys.exit(1)
    finally:
        # PlayerWindow 在 __init__ 中已将 .dly 内容全部读入内存，
        # 这里可以安全删除下载产生的临时文件
        if downloaded_tmp is not None:
            try:
                os.remove(downloaded_tmp)
                logger.debug(f"已删除下载的临时文件: {downloaded_tmp}")
            except OSError:
                pass


if __name__ == "__main__":
    main_player()