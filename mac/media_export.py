# -*- coding: utf-8 -*-
"""Mac 微信 4.x 图片/视频媒体导出。

图片：msg/attach/.../Img/*.dat（V1/V2 AES+XOR）
视频：msg/video/YYYY-MM/*.mp4（通常明文）
路径索引：decrypted/hardlink/hardlink.db
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import struct
from pathlib import Path
from typing import Optional

V1_SIG = b"\x07\x08V1\x08\x07"
V2_SIG = b"\x07\x08V2\x08\x07"
V1_KEY = b"cfcd208495d565ef"

_MAGICS = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),
    (b"wxgf", "wxgf"),
]


def detect_ext(buf: bytes) -> Optional[str]:
    for sig, ext in _MAGICS:
        if buf.startswith(sig):
            return ext
    return None


def _aligned_aes(aes_size: int) -> int:
    """WeChat：已对齐时再补一整块 PKCS7。"""
    return aes_size + (16 - aes_size % 16) if aes_size % 16 else aes_size + 16


def decode_dat(
    data: bytes,
    aes_key: bytes | str | None = None,
    xor_byte: int | None = None,
) -> Optional[tuple[bytes, str]]:
    """解密 .dat → (bytes, ext)。"""
    if not data or len(data) < 6:
        return None

    # 旧版：单字节 XOR
    if data[:3] != b"\x07\x08\x56":
        for magic, ext in _MAGICS:
            if ext in ("webp", "wxgf"):
                continue
            key = data[0] ^ magic[0]
            if all(data[i] ^ key == magic[i] for i in range(len(magic))):
                return bytes(b ^ key for b in data), ext
        return None

    if len(data) < 15:
        return None

    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    if data[3] == 0x31:
        key = V1_KEY
    else:
        if not aes_key:
            return None
        key = aes_key.encode("ascii") if isinstance(aes_key, str) else aes_key
        key = key[:16]
        if len(key) != 16:
            return None

    try:
        from Crypto.Cipher import AES
    except ImportError:
        return None

    aligned = _aligned_aes(aes_size)
    enc = data[15 : 15 + aligned]
    if len(enc) < 16:
        return None
    try:
        plain = AES.new(key, AES.MODE_ECB).decrypt(enc[: len(enc) // 16 * 16])
    except Exception:
        return None
    if plain:
        pad = plain[-1]
        if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
            plain = plain[:-pad]

    mid = data[15 + aligned : len(data) - xor_size] if xor_size else data[15 + aligned :]
    tail = data[len(data) - xor_size :] if xor_size else b""
    if xor_byte is None:
        xor_byte = (tail[-1] ^ 0xD9) if tail else 0
    out = plain + mid + bytes(b ^ (xor_byte & 0xFF) for b in tail)
    ext = detect_ext(out)
    return (out, ext) if ext else None


def wxgf_to_jpeg(wxgf_bytes: bytes) -> Optional[bytes]:
    """把 wxgf（HEVC）转成 JPEG；优先调用本机 ffmpeg。"""
    idx = wxgf_bytes.find(b"\x00\x00\x00\x01")
    if idx < 0:
        idx = wxgf_bytes.find(b"\x00\x00\x01")
    if idx < 0:
        return None
    hevc = wxgf_bytes[idx:]

    import subprocess
    import tempfile

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        hevc_path = Path(tmp) / "frame.hevc"
        jpg_path = Path(tmp) / "frame.jpg"
        hevc_path.write_bytes(hevc)
        r = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "hevc",
                "-i",
                str(hevc_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(jpg_path),
            ],
            capture_output=True,
        )
        if r.returncode != 0 or not jpg_path.exists():
            return None
        data = jpg_path.read_bytes()
        return data if data.startswith(b"\xff\xd8\xff") else None


def decode_to_displayable(
    data: bytes,
    aes_key: bytes | str | None = None,
    xor_byte: int | None = None,
) -> Optional[tuple[bytes, str]]:
    """解密 .dat；若结果是 wxgf，尽量转成 jpg。"""
    decoded = decode_dat(data, aes_key, xor_byte)
    if not decoded:
        return None
    img, ext = decoded
    if ext == "wxgf":
        jpg = wxgf_to_jpeg(img)
        if jpg:
            return jpg, "jpg"
        return img, "wxgf"
    return img, ext


def account_wxid_from_folder(account_dir: Path) -> str:
    """wxid_xxx_08c6 → wxid_xxx"""
    name = account_dir.name
    if "_" in name:
        prefix, suffix = name.rsplit("_", 1)
        if len(suffix) == 4 and all(c in "0123456789abcdef" for c in suffix.lower()):
            return prefix
    return name


def find_uin(account_dir: Path) -> Optional[int]:
    """从 kvcomm/key_<uin>_*.statistic 找 uin，并用账号目录后缀校验。"""
    account_id = account_dir.name
    suffix = account_id.rsplit("_", 1)[-1] if "_" in account_id else ""
    roots = [
        account_dir.parent.parent / "app_data",
        Path.home()
        / "Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data",
    ]
    seen: list[int] = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("key_*_*.statistic"):
            m = re.search(r"key_(\d+)_", f.name)
            if not m:
                continue
            uin = int(m.group(1))
            if uin == 0:
                continue
            seen.append(uin)
            if suffix and hashlib.md5(str(uin).encode()).hexdigest()[:4] == suffix:
                return uin
    return seen[0] if seen else None


def derive_image_key(account_dir: Path) -> tuple[Optional[bytes], Optional[int]]:
    uin = find_uin(account_dir)
    if uin is None:
        return None, None
    wxid = account_wxid_from_folder(account_dir)
    key = hashlib.md5(f"{uin}{wxid}".encode()).hexdigest()[:16].encode("ascii")
    return key, uin & 0xFF


def extract_hashes_from_packed(packed: bytes | None) -> list[str]:
    """从 packed_info_data 里抠 32 位 hex（文件名/md5）。"""
    if not packed:
        return []
    # 直接是 ascii hex
    text = ""
    try:
        text = packed.decode("ascii", errors="ignore")
    except Exception:
        pass
    found = re.findall(r"[0-9a-f]{32}", text.lower())
    # 也可能嵌在二进制里：连续 32 个 hex ascii 字节
    found += re.findall(rb"[0-9a-f]{32}", packed.lower())
    out = []
    for h in found:
        s = h.decode() if isinstance(h, bytes) else h
        if s not in out:
            out.append(s)
    return out


class HardlinkIndex:
    def __init__(self, hardlink_db: Path, account_dir: Path):
        self.account_dir = account_dir
        self.images: dict[str, list[Path]] = {}  # hash_or_stem -> paths
        self.videos: dict[str, list[Path]] = {}
        if not hardlink_db.exists():
            return
        conn = sqlite3.connect(str(hardlink_db))
        try:
            dirmap = {
                rowid: name
                for rowid, name in conn.execute("SELECT rowid, username FROM dir2id")
            }
            for md5, fname, d1, d2 in conn.execute(
                "SELECT md5, file_name, dir1, dir2 FROM image_hardlink_info_v4"
            ):
                p = self._image_path(dirmap, d1, d2, fname)
                if p:
                    stem = Path(fname).stem.lower()
                    self.images.setdefault(stem, []).append(p)
                    if md5:
                        self.images.setdefault(md5.lower(), []).append(p)
            for md5, fname, d1, d2 in conn.execute(
                "SELECT md5, file_name, dir1, dir2 FROM video_hardlink_info_v4"
            ):
                p = self._video_path(dirmap, d1, d2, fname)
                if p:
                    stem = Path(fname).stem.lower()
                    self.videos.setdefault(stem, []).append(p)
                    if md5:
                        self.videos.setdefault(md5.lower(), []).append(p)
        finally:
            conn.close()

    def _image_path(self, dirmap, d1, d2, fname) -> Optional[Path]:
        a, b = dirmap.get(d1), dirmap.get(d2)
        if not a or not b:
            return None
        p = self.account_dir / "msg" / "attach" / a / b / "Img" / fname
        return p if p.exists() else None

    def _video_path(self, dirmap, d1, d2, fname) -> Optional[Path]:
        # video: dir1 通常是 YYYY-MM，dir2 可能为 0
        month = dirmap.get(d1) or ""
        # 优先按月份目录
        candidates = []
        if re.fullmatch(r"\d{4}-\d{2}", month):
            candidates.append(self.account_dir / "msg" / "video" / month / fname)
        # 兜底全盘按文件名找
        if not any(c.exists() for c in candidates):
            hit = list((self.account_dir / "msg" / "video").rglob(fname))
            candidates.extend(hit)
        for c in candidates:
            if c.exists():
                return c
        return None

    def lookup_video(self, hashes: list[str]) -> Optional[Path]:
        for h in hashes:
            for p in self.videos.get(h.lower(), []):
                if p.suffix.lower() == ".mp4":
                    return p
            for p in self.videos.get(h.lower(), []):
                return p
        # 兜底：hardlink 未收录或导出后才下载时，按文件名在 msg/video 下找
        video_root = self.account_dir / "msg" / "video"
        if video_root.is_dir():
            for h in hashes:
                for pat in (f"{h}.mp4", f"{h}.MP4"):
                    hits = list(video_root.rglob(pat))
                    if hits:
                        return hits[0]
        return None

    def lookup_image(self, hashes: list[str]) -> Optional[Path]:
        for h in hashes:
            for p in self.images.get(h.lower(), []):
                if "_t." in p.name or "_h." in p.name:
                    continue
                return p
            for p in self.images.get(h.lower(), []):
                return p
        # 兜底：按文件名在 attach 下找
        attach_root = self.account_dir / "msg" / "attach"
        if attach_root.is_dir():
            for h in hashes:
                for pat in (f"{h}.dat", f"{h}_t.dat", f"{h}_h.dat"):
                    hits = [p for p in attach_root.rglob(pat)]
                    # 优先原图
                    for p in hits:
                        if "_t." not in p.name and "_h." not in p.name:
                            return p
                    if hits:
                        return hits[0]
        return None


class MediaExporter:
    def __init__(self, account_dir: Path, decrypted_dir: Path, out_dir: Path):
        self.account_dir = account_dir
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "images").mkdir(exist_ok=True)
        (self.out_dir / "videos").mkdir(exist_ok=True)
        self.aes_key, self.xor_byte = derive_image_key(account_dir)
        self.index = HardlinkIndex(decrypted_dir / "hardlink" / "hardlink.db", account_dir)
        self.ok_img = self.fail_img = self.ok_vid = self.fail_vid = 0
        if self.aes_key:
            print(f"图片 AES key 已派生（xor={self.xor_byte:#x}）")
        else:
            print("警告：未能派生图片 AES key，V2 图片可能无法解密")

    def export_image(self, packed: bytes | None, chat_id: str, create_time: int, local_id: int) -> str:
        hashes = extract_hashes_from_packed(packed)
        src = self.index.lookup_image(hashes)
        if not src:
            # 兜底：该会话 attach 目录里按时间找（弱匹配，仅作补充）
            self.fail_img += 1
            return ""
        try:
            data = src.read_bytes()
        except OSError:
            self.fail_img += 1
            return ""

        decoded = decode_to_displayable(data, self.aes_key, self.xor_byte)
        if not decoded:
            # 再试自动从 JPEG 尾推断 xor
            decoded = decode_to_displayable(data, self.aes_key, None)
        if not decoded:
            self.fail_img += 1
            return ""

        img, ext = decoded
        name = f"{chat_id}_{create_time}_{local_id}.{ext}"
        dest = self.out_dir / "images" / name
        dest.write_bytes(img)
        self.ok_img += 1
        return str(dest)

    def export_video(self, packed: bytes | None, chat_id: str, create_time: int, local_id: int) -> str:
        hashes = extract_hashes_from_packed(packed)
        src = self.index.lookup_video(hashes)
        if not src:
            self.fail_vid += 1
            return ""
        ext = src.suffix.lower() or ".mp4"
        dest = self.out_dir / "videos" / f"{chat_id}_{create_time}_{local_id}{ext}"
        try:
            # 微信源文件常是只读；copy2 会把只读权限带过来，重跑时无法覆盖
            if dest.exists():
                dest.chmod(0o644)
            shutil.copyfile(src, dest)
            dest.chmod(0o644)
        except OSError:
            self.fail_vid += 1
            return ""
        self.ok_vid += 1
        return str(dest)
