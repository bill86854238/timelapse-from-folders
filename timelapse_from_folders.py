#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timelapse generator for nested date folders (e.g., 2023-07-26) containing images.
- Auto-fixes files without extension if they are JPEG (by header signature).
- Sorts frames by file modified time (fallback when filenames are random).
- Optionally overlays timestamp and source label.
- Writes one video per date folder, mirroring the folder name.
Tested on Windows (NAS paths) & macOS. Requires: opencv-python, pillow, tqdm
"""
import os
import sys
import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import cv2  # opencv-python
from PIL import Image, ImageOps  # pillow
from tqdm import tqdm

JPEG_MAGIC = (0xFF, 0xD8, 0xFF)

def is_jpeg_file_by_header(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            b = f.read(3)
            return len(b) == 3 and tuple(b) == JPEG_MAGIC
    except Exception:
        return False

def maybe_fix_missing_extension(path: Path) -> Path:
    """
    If file has no suffix and looks like a JPEG by header, rename to .jpg.
    Returns (possibly new) path. Skips when rename would collide.
    """
    if path.suffix:
        return path
    if is_jpeg_file_by_header(path):
        new_path = path.with_suffix(".jpg")
        if new_path.exists():
            # If target exists, keep original to avoid overwriting
            return path
        try:
            path.rename(new_path)
            return new_path
        except Exception:
            # Some filesystems disallow rename across mounts; copy then remove
            try:
                shutil.copy2(path, new_path)
                path.unlink(missing_ok=True)
                return new_path
            except Exception:
                return path
    return path

def list_images(folder: Path, allowed_exts: List[str]) -> List[Path]:
    files = []
    for p in folder.iterdir():
        if p.is_dir():
            continue
        # Try fix extension first if none
        pp = maybe_fix_missing_extension(p) if p.suffix == "" else p
        ext = pp.suffix.lower().lstrip(".")
        if ext in allowed_exts:
            files.append(pp)
    # Sort by mtime (ns for tie-break)
    files.sort(key=lambda x: (x.stat().st_mtime_ns, x.name))
    return files

def load_image_bgr_with_orientation(path: Path):
    """
    Load with Pillow to apply EXIF orientation (transpose), then convert to BGR numpy for cv2.
    """
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        # Ensure 3 channels
        if im.mode != "RGB":
            im = im.convert("RGB")
        import numpy as np
        arr = np.array(im)  # RGB
        bgr = arr[:, :, ::-1].copy()
        return bgr

def draw_label(img, text, bottom_left=(20, 20), font_scale=0.7, thickness=2):
    h, w = img.shape[:2]
    x, y = bottom_left
    # position from bottom
    y = h - y
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    # background box for readability
    pad = 6
    cv2.rectangle(img, (x - pad, y - th - pad), (x + tw + pad, y + pad//2), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

def ensure_size(img, target_w: int, target_h: int) -> Tuple:
    """
    Resize to fit exact (w,h) for VideoWriter; keep aspect by padding (letterbox) if needed.
    """
    h, w = img.shape[:2]
    if w == target_w and h == target_h:
        return img
    # scale to fit within box
    scale = min(target_w / w, target_h / h)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    # pad to exact size (center)
    top = (target_h - new_h) // 2
    bottom = target_h - new_h - top
    left = (target_w - new_w) // 2
    right = target_w - new_w - left
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return padded

def pick_writer_size(first_img, target_width: int) -> Tuple[int, int]:
    h, w = first_img.shape[:2]
    if target_width <= 0:
        return w, h
    # keep aspect
    new_w = target_width
    new_h = int(round(h * (target_width / w)))
    # enforce even dimensions for some codecs
    if new_w % 2: new_w += 1
    if new_h % 2: new_h += 1
    return new_w, new_h

def make_video_for_folder(folder: Path, out_dir: Path, fps: int, width: int, overlay_time: bool, src_label: str, codec: str, overwrite: bool, allowed_exts: List[str]):
    images = list_images(folder, allowed_exts)
    if not images:
        return False, f"[SKIP] {folder.name}: 沒有符合的圖片"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{folder.name}.mp4"
    if out_path.exists() and not overwrite:
        return False, f"[SKIP] {out_path.name}: 已存在（使用 --overwrite 可覆蓋）"

    # Load first frame to decide size
    first = load_image_bgr_with_orientation(images[0])
    W, H = pick_writer_size(first, width)
    fourcc = cv2.VideoWriter_fourcc(*codec)  # e.g., 'mp4v'
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (W, H))
    if not writer.isOpened():
        return False, f"[ERR ] 無法開啟寫入器，可能缺少編碼器：codec={codec} path={out_path}"

    for p in tqdm(images, desc=f"{folder.name}", unit="img", leave=False):
        try:
            img = load_image_bgr_with_orientation(p)
        except Exception as e:
            print(f"[WARN] 無法讀取 {p.name}: {e}")
            continue
        frame = ensure_size(img, W, H)
        if overlay_time:
            ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            draw_label(frame, f"{ts}")
        if src_label:
            draw_label(frame, src_label, bottom_left=(20, 50), font_scale=0.6, thickness=2)
        writer.write(frame)

    writer.release()
    return True, f"[OK  ] {out_path}  ({len(images)} frames @ {fps}fps)"

def scan_date_folders(root: Path) -> List[Path]:
    # Date folder pattern like 2023-07-26; but we just take first-level subfolders
    return [p for p in root.iterdir() if p.is_dir()]

def main():
    parser = argparse.ArgumentParser(description="為每個日期資料夾輸出一支縮時影片（.mp4）")
    parser.add_argument("--root", required=True, help=r"根目錄（包含多個日期資料夾）")
    parser.add_argument("--out", required=True, help=r"輸出根目錄（影片會以 <日期>.mp4 命名）")
    parser.add_argument("--fps", type=int, default=24, help="每秒影格數（預設 24）")
    parser.add_argument("--width", type=int, default=1280, help="輸出影片寬度，0 表示使用原圖寬（預設 1280）")
    parser.add_argument("--ext", nargs="*", default=["jpg", "jpeg", "png"], help="要納入的副檔名（小寫，不含點）")
    parser.add_argument("--no-time", action="store_true", help="不要在畫面左下角覆蓋時間戳")
    parser.add_argument("--label", default="", help="來源標籤（例如：SpotCam 1號機），空字串則不顯示")
    parser.add_argument("--codec", default="mp4v", help="影片編碼 FourCC，Windows 建議 mp4v / XVID（預設 mp4v）")
    parser.add_argument("--overwrite", action="store_true", help="若輸出檔已存在則覆蓋")
    args = parser.parse_args()

    root = Path(args.root)
    out_root = Path(args.out)
    if not root.exists():
        print(f"[ERR ] root 不存在：{root}")
        sys.exit(2)

    folders = scan_date_folders(root)
    if not folders:
        print(f"[ERR ] 在 {root} 下找不到任何子資料夾")
        sys.exit(3)

    print(f"共 {len(folders)} 個資料夾。開始處理…")
    ok_cnt = 0
    for folder in sorted(folders):
        ok, msg = make_video_for_folder(
            folder=folder,
            out_dir=out_root,
            fps=args.fps,
            width=args.width,
            overlay_time=(not args.no_time),
            src_label=args.label,
            codec=args.codec,
            overwrite=args.overwrite,
            allowed_exts=args.ext,
        )
        print(msg)
        if ok:
            ok_cnt += 1
    print(f"完成：成功 {ok_cnt} / {len(folders)}")

if __name__ == "__main__":
    main()
