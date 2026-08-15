"""
WeChat 4.0 数据库解密器

使用从进程内存提取的per-DB enc_key解密SQLCipher 4加密的数据库
参数: SQLCipher 4, AES-256-CBC, HMAC-SHA512, reserve=80, page_size=4096
密钥来源: all_keys.json (由find_all_keys.py从内存提取)
"""
import hashlib, struct, os, sys, json
import hmac as hmac_mod
from Crypto.Cipher import AES

import functools
print = functools.partial(print, flush=True)

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80  # IV(16) + HMAC(64)
SQLITE_HDR = b'SQLite format 3\x00'

from config import load_config
from key_utils import get_key_info, strip_key_metadata

CHUNK_PAGES = 256  # 每次读写 1MB，减少系统调用
SKIP_NAME_TOKENS = ("fts", "resource", "media", "weclaw")
MAX_AUTO_WORKERS = 6


def derive_mac_key(enc_key, salt):
    """从enc_key派生HMAC密钥"""
    mac_salt = bytes(b ^ 0x3a for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def decrypt_page(enc_key, page_data, pgno):
    """解密单个页面，输出4096字节的标准SQLite页面"""
    iv = page_data[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]

    if pgno == 1:
        encrypted = page_data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        page = bytearray(SQLITE_HDR + decrypted + b'\x00' * RESERVE_SZ)
        # 保留 reserve=80, B-tree 基于 usable_size=4016 构建
        return bytes(page)
    else:
        encrypted = page_data[:PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b'\x00' * RESERVE_SZ


def decrypt_database(db_path, out_path, enc_key, quiet=False):
    """解密整个数据库文件。先写临时文件再替换，避免半成品被当成有效缓存。"""
    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ

    if file_size % PAGE_SZ != 0:
        if not quiet:
            print(f"  [WARN] 文件大小 {file_size} 不是 {PAGE_SZ} 的倍数")
        total_pages += 1

    with open(db_path, "rb") as fin:
        page1 = fin.read(PAGE_SZ)

    if len(page1) < PAGE_SZ:
        if not quiet:
            print("  [ERROR] 文件太小")
        return False

    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    p1_hmac_data = page1[SALT_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    p1_stored_hmac = page1[PAGE_SZ - HMAC_SZ : PAGE_SZ]
    hm = hmac_mod.new(mac_key, p1_hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    if hm.digest() != p1_stored_hmac:
        if not quiet:
            print(f"  [ERROR] Page 1 HMAC验证失败! salt: {salt.hex()}")
        return False

    if not quiet:
        print(f"  HMAC OK, {total_pages} pages")

    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = out_path + ".tmp"
    try:
        with open(db_path, "rb") as fin, open(tmp_path, "wb") as fout:
            pgno = 0
            while True:
                raw = fin.read(PAGE_SZ * CHUNK_PAGES)
                if not raw:
                    break
                if len(raw) % PAGE_SZ:
                    raw += b"\x00" * (PAGE_SZ - len(raw) % PAGE_SZ)
                out_buf = bytearray(len(raw))
                for off in range(0, len(raw), PAGE_SZ):
                    pgno += 1
                    dec = decrypt_page(enc_key, raw[off : off + PAGE_SZ], pgno)
                    out_buf[off : off + PAGE_SZ] = dec
                    if pgno == 1 and dec[:16] != SQLITE_HDR and not quiet:
                        print("  [WARN] 解密后header不匹配!")
                    if not quiet and pgno % 10000 == 0:
                        print(
                            f"  进度: {pgno}/{total_pages} ({100 * pgno / total_pages:.1f}%)"
                        )
                fout.write(out_buf)
        os.replace(tmp_path, out_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    return True


def is_export_needed_db(rel, include_hardlink=True):
    """导出聊天记录真正需要的库：联系人、消息、以及媒体索引。"""
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    if not name.endswith(".db"):
        return False
    lower = name.lower()
    if any(tok in lower for tok in SKIP_NAME_TOKENS):
        return False
    if rel.startswith("contact/"):
        return True
    if rel.startswith("message/") and name.startswith("message_"):
        return True
    if include_hardlink and rel.startswith("hardlink/"):
        return True
    return False


def output_is_current(src, dst):
    """源库大小和修改时间都没变，则可以复用已解密文件。"""
    if not os.path.exists(dst):
        return False
    try:
        src_st, dst_st = os.stat(src), os.stat(dst)
    except OSError:
        return False
    return dst_st.st_size == src_st.st_size and dst_st.st_mtime >= src_st.st_mtime


def _decrypt_one(job):
    rel, src, dst, key_hex = job
    ok = decrypt_database(src, dst, bytes.fromhex(key_hex), quiet=True)
    return rel, ok


def decrypt_export_dbs(
    db_dir,
    out_dir,
    keys,
    force=False,
    include_hardlink=True,
    workers=0,
):
    """只解密导出会用到的库；未变化的跳过；多个库可并行。"""
    jobs = []
    skipped_unused = 0
    skipped_fresh = 0
    skipped_nokey = 0

    for root, _, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db") or name.endswith(("-wal", "-shm")):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, db_dir).replace("\\", "/")
            if not is_export_needed_db(rel, include_hardlink=include_hardlink):
                skipped_unused += 1
                continue
            key_info = get_key_info(keys, rel)
            if not key_info:
                print(f"  SKIP {rel}（无密钥）")
                skipped_nokey += 1
                continue
            dst = os.path.join(out_dir, *rel.split("/"))
            size = os.path.getsize(path)
            if not force and output_is_current(path, dst):
                skipped_fresh += 1
                continue
            jobs.append((rel, path, dst, key_info["enc_key"], size))

    if skipped_unused:
        print(f"跳过 {skipped_unused} 个与导出无关的库（fts/media/resource 等）")
    if skipped_fresh:
        print(f"复用 {skipped_fresh} 个未变化的解密结果")
    if not jobs:
        if skipped_fresh:
            print("解密结果已是最新。加 --refresh-decrypt 可强制重解")
        elif skipped_nokey:
            print("没有可解密的导出数据库。")
        return out_dir

    jobs.sort(key=lambda x: x[4], reverse=True)
    cpu = os.cpu_count() or 2
    if workers is None or workers <= 0:
        n_workers = min(cpu, MAX_AUTO_WORKERS, len(jobs))
    else:
        n_workers = max(1, min(int(workers), len(jobs)))

    total_mb = sum(j[4] for j in jobs) / 1024 / 1024
    print(
        f"开始解密 {len(jobs)} 个数据库（{total_mb:.1f}MB），"
        f"{n_workers} 进程 → {out_dir}"
    )

    ok_n = fail_n = 0
    if n_workers == 1:
        for rel, path, dst, key_hex, size in jobs:
            print(
                f"  解密 {rel} ({size / 1024 / 1024:.1f}MB) ...",
                end=" ",
                flush=True,
            )
            if decrypt_database(path, dst, bytes.fromhex(key_hex), quiet=False):
                print("OK")
                ok_n += 1
            else:
                print("FAIL")
                fail_n += 1
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        pool_jobs = [(rel, path, dst, key_hex) for rel, path, dst, key_hex, _sz in jobs]
        size_map = {j[0]: j[4] for j in jobs}
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_decrypt_one, job): job[0] for job in pool_jobs}
            for fut in as_completed(futs):
                rel = futs[fut]
                try:
                    rel, ok = fut.result()
                except Exception as e:
                    print(f"  FAIL {rel}: {e}")
                    fail_n += 1
                    continue
                mb = size_map.get(rel, 0) / 1024 / 1024
                print(f"  {'OK' if ok else 'FAIL'} {rel} ({mb:.1f}MB)")
                if ok:
                    ok_n += 1
                else:
                    fail_n += 1

    print(
        f"解密完成：成功 {ok_n}，失败 {fail_n}，"
        f"复用 {skipped_fresh}，无关跳过 {skipped_unused}"
    )
    return out_dir


def main():
    print("=" * 60)
    print("  WeChat 4.0 数据库解密器")
    print("=" * 60)

    cfg = load_config()
    db_dir = cfg["db_dir"]
    out_dir = cfg["decrypted_dir"]
    keys_file = cfg["keys_file"]

    # 加载密钥
    if not os.path.exists(keys_file):
        print(f"[ERROR] 密钥文件不存在: {keys_file}")
        print("请先运行: python wcdb_key_tool_windows.py extract")
        sys.exit(1)

    with open(keys_file) as f:
        keys = json.load(f)

    keys = strip_key_metadata(keys)
    print(f"\n加载 {len(keys)} 个数据库密钥")
    print(f"输出目录: {out_dir}")
    os.makedirs(out_dir, exist_ok=True)

    # 收集所有DB文件
    db_files = []
    for root, dirs, files in os.walk(db_dir):
        for f in files:
            if f.endswith('.db') and not f.endswith('-wal') and not f.endswith('-shm'):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, db_dir)
                sz = os.path.getsize(path)
                db_files.append((rel, path, sz))

    db_files.sort(key=lambda x: x[2])  # 从小到大

    print(f"找到 {len(db_files)} 个数据库文件\n")

    success = 0
    failed = 0
    total_bytes = 0

    for rel, path, sz in db_files:
        key_info = get_key_info(keys, rel)
        if not key_info:
            print(f"SKIP: {rel} (无密钥)")
            failed += 1
            continue

        enc_key = bytes.fromhex(key_info["enc_key"])
        out_path = os.path.join(out_dir, rel)

        print(f"解密: {rel} ({sz/1024/1024:.1f}MB) ...", end=" ")

        ok = decrypt_database(path, out_path, enc_key)
        if ok:
            # SQLite验证
            try:
                import sqlite3
                conn = sqlite3.connect(out_path)
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                conn.close()
                table_names = [t[0] for t in tables]
                print(f"  OK! 表: {', '.join(table_names[:5])}", end="")
                if len(table_names) > 5:
                    print(f" ...共{len(table_names)}个", end="")
                print()
                success += 1
                total_bytes += sz
            except Exception as e:
                print(f"  [WARN] SQLite验证失败: {e}")
                failed += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"结果: {success} 成功, {failed} 失败, 共 {len(db_files)} 个")
    print(f"解密数据量: {total_bytes/1024/1024/1024:.1f}GB")
    print(f"解密文件在: {out_dir}")


if __name__ == '__main__':
    main()
