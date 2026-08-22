"""Wrap 阶段：将 extract.json、split.json、timeline.json、歌词图片、音频整合为一个 .dly 播放包。

.dly 是 ZIP 容器，内部结构：
    manifest.json   包描述（格式版本、文件清单、创建时间、标题）
    extract.json    歌词行标记数据
    split.json      片段分割数据
    timeline.json   时间轴数据
    <图片文件>      歌词原图
    <音频文件>      音乐

图片与音频的路径从 JSON 中自动定位：
    extract.json   -> image_path
    timeline.json  -> audio_path
"""

import sys
import os
import json
import logging
import zipfile
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

FORMAT_VERSION = 1


def package_dly(extract_path: str, split_path: str, timeline_path: str,
                out_path: str, title: Optional[str] = None) -> dict:
    """读取三个 JSON，定位图片与音频，打包为 .dly，返回 manifest。"""
    for p in (extract_path, split_path, timeline_path):
        if not os.path.isfile(p):
            raise ValueError(f"文件不存在：{p}")

    with open(extract_path, "r", encoding="utf-8") as f:
        extract_data = json.load(f)
    with open(timeline_path, "r", encoding="utf-8") as f:
        timeline_data = json.load(f)

    image_path = extract_data.get("image_path", "")
    audio_path = timeline_data.get("audio_path", "")
    if not image_path:
        raise ValueError("extract.json 中缺少 image_path")
    if not audio_path:
        raise ValueError("timeline.json 中缺少 audio_path")

    # 兼容 timeline.json 中可能存在的 file:// 前缀
    if audio_path.startswith("file://"):
        audio_path = audio_path[len("file://"):]
    for p, what in ((image_path, "图片"), (audio_path, "音频")):
        if not os.path.isfile(p):
            raise ValueError(f"{what}文件不存在：{p}")

    image_name = os.path.basename(image_path)
    audio_name = os.path.basename(audio_path)
    if image_name == audio_name:
        raise ValueError("图片与音频文件名相同，无法打包")

    if not title:
        title = os.path.splitext(audio_name)[0]

    manifest = {
        "format_version": FORMAT_VERSION,
        "title": title,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "extract_json": "extract.json",
        "split_json": "split.json",
        "timeline_json": "timeline.json",
        "image_file": image_name,
        "audio_file": audio_name,
    }

    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # JSON 数据压缩存储
        zf.write(extract_path, "extract.json", compress_type=zipfile.ZIP_DEFLATED)
        zf.write(split_path, "split.json", compress_type=zipfile.ZIP_DEFLATED)
        zf.write(timeline_path, "timeline.json", compress_type=zipfile.ZIP_DEFLATED)
        # 图片已压缩，音频（mp3 等）压缩无收益，原样存储
        zf.write(image_path, image_name, compress_type=zipfile.ZIP_STORED)
        zf.write(audio_path, audio_name, compress_type=zipfile.ZIP_STORED)

    logger.info(f"打包成功：{out_path}（{title}）")
    return manifest


def main_wrap():
    app = QApplication(sys.argv)

    extract_path, _ = QFileDialog.getOpenFileName(
        None, "选择 extract.json", "", "JSON 文件 (*.json)"
    )
    if not extract_path:
        sys.exit(0)

    split_path, _ = QFileDialog.getOpenFileName(
        None, "选择 split.json", "", "JSON 文件 (*.json)"
    )
    if not split_path:
        sys.exit(0)

    timeline_path, _ = QFileDialog.getOpenFileName(
        None, "选择 timeline.json", "", "JSON 文件 (*.json)"
    )
    if not timeline_path:
        sys.exit(0)

    # 从 JSON 中自动定位图片与音频
    try:
        with open(extract_path, "r", encoding="utf-8") as f:
            image_path = json.load(f).get("image_path", "")
        with open(timeline_path, "r", encoding="utf-8") as f:
            audio_path = json.load(f).get("audio_path", "")
        if audio_path.startswith("file://"):
            audio_path = audio_path[len("file://"):]
        default_name = os.path.splitext(os.path.basename(audio_path))[0] + ".dly"
    except Exception as e:
        QMessageBox.critical(None, "错误", f"读取 JSON 失败：\n{e}")
        sys.exit(1)

    out_path, _ = QFileDialog.getSaveFileName(
        None, "保存 .dly 播放包", default_name, "Lyric 播放包 (*.dly)"
    )
    if not out_path:
        sys.exit(0)
    if not out_path.lower().endswith(".dly"):
        out_path += ".dly"

    try:
        manifest = package_dly(extract_path, split_path, timeline_path, out_path)
        logger.info(f"包内文件清单：{manifest}")
        QMessageBox.information(
            None, "打包成功",
            f"已生成 {out_path}\n\n"
            f"标题：{manifest['title']}\n"
            f"图片：{manifest['image_file']}\n"
            f"音频：{manifest['audio_file']}\n\n"
            f"现在可以用 player 阶段直接打开该 .dly 文件播放。",
        )
    except Exception as e:
        logger.error(f"打包失败：{e}")
        QMessageBox.critical(None, "打包失败", f"无法打包：\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main_wrap()
