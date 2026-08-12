# -*- coding: utf-8 -*-
"""从运行中的 Weixin.exe 内存提取 V2 图片 AES key。

用法（管理员权限，微信已登录且刚打开过几张图片）：
  python find_image_key_windows.py

成功后会把 image_aes_key / image_xor_key 写入同目录 config.json。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("本脚本仅支持 Windows。")

from Crypto.Cipher import AES

from config import load_config
from media_export import V2_SIG, guess_xor_from_thumbnails

RE_KEY16 = re.compile(rb"(?<![0-9A-Za-z])([0-9A-Za-z]{16})(?![0-9A-Za-z])")
RE_KEY32 = re.compile(rb"(?<![0-9A-Za-z])([0-9A-Za-z]{32})(?![0-9A-Za-z])")
IMAGE_MAGICS = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"wxgf")

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def _get_pids() -> list[int]:
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pids = []
    for line in r.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.strip('"').split('","')
        if len(parts) >= 2:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                continue
    return pids


def _find_v2_ciphertext(account_dir: Path) -> bytes | None:
    """取最近修改的 V2 .dat 的首个 AES 密文块（16B）。"""
    attach = account_dir / "msg" / "attach"
    if not attach.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for p in attach.rglob("*.dat"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, p))
    candidates.sort(reverse=True)
    for _, p in candidates[:200]:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) < 31 or data[:6] != V2_SIG:
            continue
        aes_size = struct.unpack_from("<I", data, 6)[0]
        if aes_size < 16:
            continue
        return data[15:31]
    return None


def _valid_aes_candidate(key: bytes, ciphertext: bytes) -> bool:
    if len(key) != 16 or len(ciphertext) != 16:
        return False
    try:
        plain = AES.new(key, AES.MODE_ECB).decrypt(ciphertext)
    except Exception:
        return False
    return any(plain.startswith(m) for m in IMAGE_MAGICS)


def extract_image_aes_key(account_dir: Path | None = None) -> tuple[str | None, int | None]:
    cfg = load_config()
    if account_dir is None:
        account_dir = Path(cfg.get("wechat_base_dir") or Path(cfg["db_dir"]).parent)

    ciphertext = _find_v2_ciphertext(account_dir)
    if not ciphertext:
        print("[!] 未找到 V2 图片样本。请先在微信里打开几张图片，确保本地有 .dat 缓存。")
        return None, None
    print(f"[*] 使用 V2 密文块做校验: {ciphertext.hex()}")

    xor_key = guess_xor_from_thumbnails(account_dir)
    if xor_key is not None:
        print(f"[*] 推测 XOR key = 0x{xor_key:02x}")

    pids = _get_pids()
    if not pids:
        print("[!] Weixin.exe 未运行，请先登录微信并打开几张图片。")
        return None, xor_key

    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    MEM_COMMIT = 0x1000
    READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_uint64),
            ("AllocationBase", ctypes.c_uint64),
            ("AllocationProtect", wt.DWORD),
            ("_pad1", wt.DWORD),
            ("RegionSize", ctypes.c_uint64),
            ("State", wt.DWORD),
            ("Protect", wt.DWORD),
            ("Type", wt.DWORD),
            ("_pad2", wt.DWORD),
        ]

    def read_mem(h, addr, sz):
        buf = ctypes.create_string_buffer(sz)
        n = ctypes.c_size_t(0)
        if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
            return buf.raw[: n.value]
        return None

    def enum_regions(h):
        regs = []
        addr = 0
        mbi = MBI()
        while addr < 0x7FFFFFFFFFFF:
            if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 200 * 1024 * 1024:
                regs.append((mbi.BaseAddress, mbi.RegionSize))
            nxt = mbi.BaseAddress + mbi.RegionSize
            if nxt <= addr:
                break
            addr = nxt
        return regs

    seen: set[bytes] = set()
    t0 = time.time()
    for pid in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            print(f"[WARN] 无法打开进程 PID={pid}（请以管理员身份运行）")
            continue
        print(f"[*] 扫描 PID={pid} ...")
        try:
            for base, size in enum_regions(h):
                data = read_mem(h, base, size)
                if not data:
                    continue
                for rx in (RE_KEY16, RE_KEY32):
                    for m in rx.finditer(data):
                        raw = m.group(1)
                        # 32 字符时尝试前/后 16
                        candidates = [raw] if len(raw) == 16 else [raw[:16], raw[16:]]
                        for cand in candidates:
                            if cand in seen:
                                continue
                            seen.add(cand)
                            if _valid_aes_candidate(cand, ciphertext):
                                key = cand.decode("ascii")
                                print(f"[+] 找到图片 AES key: {key}（耗时 {time.time() - t0:.1f}s）")
                                return key, xor_key
        finally:
            kernel32.CloseHandle(h)

    print(f"[!] 未找到有效 AES key（候选 {len(seen)} 个，{time.time() - t0:.1f}s）")
    print("    请在微信中打开几张原图后再重试。")
    return None, xor_key


def save_to_config(aes_key: str, xor_key: int | None) -> None:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            cfg = {}
    cfg["image_aes_key"] = aes_key
    if xor_key is not None:
        cfg["image_xor_key"] = int(xor_key)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    print(f"[+] 已写入 {CONFIG_FILE}")


def main():
    aes, xor = extract_image_aes_key()
    if not aes:
        raise SystemExit(1)
    save_to_config(aes, xor)


if __name__ == "__main__":
    main()
