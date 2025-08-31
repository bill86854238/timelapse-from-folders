#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timelapse generator for:
- A root folder containing multiple date subfolders (e.g., 2023-07-26), or
- A single folder of images.

Features:
- Fix files without extension if they are actually JPEG (by magic header FFD8FF)
- Sort frames by file modified time (mtime)
- Optional timestamp and source label overlay
- Letterbox to target width while preserving aspect
- Time-of-day filtering:  --time-start 07:30  --time-end 18:30
- Weekday filtering:      --weekdays mon-fri  (also supports 一-五 / 1-5 / mon,wed,fri ...)
- One MP4 per subfolder (or per the single folder)

Requires: opencv-python, Pillow, tqdm
"""

import argparse
import shutil
from datetime import datetime, time
from pathlib import Path
from typing import List, Tuple, Optional, Set

import cv2
from PIL import Image, ImageOps
from tqdm import tqdm

JPEG_MAGIC = (0xFF, 0xD8, 0xFF)

# ---------- helpers: file type / listing ----------

def is_jpeg_file_by_header(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            b = f.read(3)
            return len(b) == 3 and tuple(b) == JPEG_MAGIC
    except Exception:
        return False

def maybe_fix_missing_extension(path: Path) -> Path:
    """If no suffix and looks like JPEG, rename/copy to .jpg."""
    if path.suffix:
        return path
    if is_jpeg_file_by_header(path):
        new_path = path.with_suffix(".jpg")
        if new_path.exists():
            return path
        try:
            path.rename(new_path)
            return new_path
        except Exception:
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
        pp = maybe_fix_missing_extension(p) if p.suffix == "" else p
        ext = pp.suffix.lower().lstrip(".")
        if ext in allowed_exts:
            files.append(pp)
    # sort by last modified time (ns) then name
    files.sort(key=lambda x: (x.stat().st_mtime_ns, x.name))
    return files

# ---------- helpers: time-of-day + weekday parsing ----------

def parse_hhmm(s: Optional[str]) -> Optional[time]:
    if not s:
        return None
    s = s.strip()
    try:
        hh, mm = s.split(":")
        hh = int(hh); mm = int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
        return time(hour=hh, minute=mm)
    except Exception:
        raise argparse.ArgumentTypeError(f"無法解析時間（請用 HH:MM，例如 07:30），收到：{s}")

def in_time_window(dt: datetime, start: Optional[time], end: Optional[time]) -> bool:
    """True if dt.time() is within [start, end]. Supports crossing midnight."""
    if start is None and end is None:
        return True
    t = dt.time()
    if start is None:
        return t <= end
    if end is None:
        return t >= start
    if start <= end:
        return start <= t <= end
    else:
        # crosses midnight, e.g. 22:00-05:30
        return (t >= start) or (t <= end)

def parse_weekdays(s: Optional[str]) -> Optional[Set[int]]:
    """
    Parse weekdays into a set of Python weekday indices (Mon=0..Sun=6).
    Accepts:
      - English: mon, tue, wed, thu, fri, sat, sun (also 'monday' etc.)
      - Ranges:  mon-fri, fri-mon (wrap)
      - Numbers: 1-5 (Mon=1..Sun=7), or 0-6 (Mon=0..Sun=6)
      - Chinese: 一-五, 週一, 星期三, 六, 日/天, 逗號分隔
    Examples:
      mon-fri   | 一-五  | 1-5
      mon,wed,fri | 一,三,五 | 1,3,5
    """
    if not s:
        return None

    s = s.lower().strip()
    # normalize Chinese prefixes and separators
    for pre in ("星期", "週", "周"):
        s = s.replace(pre, "")
    s = s.replace("、", ",").replace("，", ",").replace(" ", "")

    en = {
        "mon":0, "monday":0,
        "tue":1, "tues":1, "tuesday":1,
        "wed":2, "wednesday":2,
        "thu":3, "thur":3, "thurs":3, "thursday":3,
        "fri":4, "friday":4,
        "sat":5, "saturday":5,
        "sun":6, "sunday":6,
    }
    zh = {"一":0, "二":1, "三":2, "四":3, "五":4, "六":5, "日":6, "天":6}

    def tok_to_idx_list(tok: str):
        if not tok:
            return []
        # range like a-b
        if "-" in tok:
            a, b = tok.split("-", 1)
            a_list = tok_to_idx_list(a)
            b_list = tok_to_idx_list(b)
            if not a_list or not b_list:
                return []
            ai, bi = a_list[0], b_list[0]
            res = []
            i = ai
            while True:
                res.append(i)
                if i == bi:
                    break
                i = (i + 1) % 7
            return res
        # single
        if tok in en:
            return [en[tok]]
        if tok in zh:
            return [zh[tok]]
        if tok.isdigit():
            n = int(tok)
            # prefer 1-7 mapping if 7 present or 1..7
            if 1 <= n <= 7:
                return [ (n - 1) % 7 ]
            if 0 <= n <= 6:
                return [ n ]
        return []

    out: Set[int] = set()
    for part in s.split(","):
        out.update(tok_to_idx_list(part))
    return out or None

def in_weekdays(dt: datetime, wanted: Optional[Set[int]]) -> bool:
    if not wanted:
        return True
    return dt.weekday() in wanted

def filter_by_window(files: List[Path], tstart: Optional[time], tend: Optional[time], wdays: Optional[Set[int]]) -> List[Path]:
    if tstart is None and tend is None and not wdays:
        return files
    kept = []
    for p in files:
        ts = datetime.fromtimestamp(p.stat().st_mtime)  # local time
        if in_time_window(ts, tstart, tend) and in_weekdays(ts, wdays):
            kept.append(p)
    return kept

# ---------- helpers: imaging ----------

def load_image_bgr_with_orientation(path: Path):
    import numpy as np
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        arr = np.array(im)  # RGB
        return arr[:, :, ::-1].copy()  # BGR

def draw_label(img, text, bottom_left=(20, 20), font_scale=0.7, thickness=2):
    h, w = img.shape[:2]
    x, y = bottom_left
    y = h - y
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    pad = 6
    cv2.rectangle(img, (x - pad, y - th - pad), (x + tw + pad, y + pad//2), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

def ensure_size(img, target_w: int, target_h: int):
    h, w = img.shape[:2]
    if w == target_w and h == target_h:
        return img
    scale = min(target_w / w, target_h / h)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    top = (target_h - new_h) // 2
    bottom = target_h - new_h - top
    left = (target_w - new_w) // 2
    right = target_w - new_w - left
    return cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0))

def pick_writer_size(first_img, target_width: int) -> Tuple[int, int]:
    h, w = first_img.shape[:2]
    if target_width <= 0:
        W, H = w, h
    else:
        W = target_width
        H = int(round(h * (target_width / w)))
    if W % 2: W += 1
    if H % 2: H += 1
    return W, H

# ---------- main processing ----------

def make_video_for_folder(folder: Path, out_dir: Path, fps: int, width: int,
                          overlay_time: bool, src_label: str, codec: str, overwrite: bool,
                          allowed_exts: List[str],
                          tstart: Optional[time], tend: Optional[time], wdays: Optional[Set[int]]):
    images = list_images(folder, allowed_exts)
    images = filter_by_window(images, tstart, tend, wdays)
    if not images:
        return False, f"[SKIP] {folder}: 時間/星期範圍內沒有可用圖片"

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{folder.name}.mp4"
    if out_path.exists() and not overwrite:
        return False, f"[SKIP] {out_path.name}: 已存在（--overwrite 可覆蓋）"

    first = load_image_bgr_with_orientation(images[0])
    W, H = pick_writer_size(first, width)
    fourcc = cv2.VideoWriter_fourcc(*codec)
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

def scan_target_folders(root: Path) -> List[Path]:
    subs = [p for p in root.iterdir() if p.is_dir()]
    if subs:
        return subs
    imgs = list_images(root, ["jpg", "jpeg", "png"])
    if imgs:
        return [root]
    return []

def main():
    parser = argparse.ArgumentParser(description="為每個日期資料夾（或單一資料夾）輸出縮時影片")
    parser.add_argument("--root", required=True, help=r"根目錄（包含多個日期資料夾，或單一含圖的資料夾）")
    parser.add_argument("--out", required=True, help=r"輸出根目錄（影片會以 <資料夾名>.mp4 命名）")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--ext", nargs="*", default=["jpg", "jpeg", "png"])
    parser.add_argument("--no-time", action="store_true")
    parser.add_argument("--label", default="")
    parser.add_argument("--codec", default="mp4v")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--time-start", type=parse_hhmm, default=None, help="時間篩選開始（HH:MM，例如 07:30）")
    parser.add_argument("--time-end", type=parse_hhmm, default=None, help="時間篩選結束（HH:MM，例如 18:30）")
    parser.add_argument("--weekdays", type=str, default=None,
                        help="星期篩選，例如：mon-fri / 一-五 / 1-5 / mon,wed,fri（Mon=週一）")

    args = parser.parse_args()

    root = Path(args.root)
    out_root = Path(args.out)
    if not root.exists():
        print(f"[ERR ] root 不存在：{root}")
        raise SystemExit(2)

    wdays = parse_weekdays(args.weekdays)

    folders = scan_target_folders(root)
    if not folders:
        print(f"[ERR ] 在 {root} 下找不到子資料夾或圖片")
        raise SystemExit(3)

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
            allowed_exts=[e.lower() for e in args.ext],
            tstart=args.time_start,
            tend=args.time_end,
            wdays=wdays,
        )
        print(msg)
        if ok:
            ok_cnt += 1
    print(f"完成：成功 {ok_cnt} / {len(folders)}")

if __name__ == "__main__":
    main()
