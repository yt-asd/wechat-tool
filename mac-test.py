# -*- coding: utf-8 -*-
"""
【macOS 专用】微信聊天记录导出 Excel（WeChat 4.x / 4.1+）

实现目录：mac/。Windows 版请见 win/（尚未实现），请勿在 Windows 上运行本脚本。

流程：提取密钥 → 解密 SQLCipher 数据库 → 导出文本/图片/视频到 Excel + 媒体目录

日常刷新最新记录（复用密钥，重新解密+导出）：
  python3 mac-test.py --fresh

只导出最近 N 天（如最近 7 天）：
  python3 mac-test.py --fresh --days 7

指定精确范围（与 --days 互斥）：
  python3 mac-test.py --fresh --start 2026-08-01 --end 2026-08-12

只导出指定联系人/群（按备注/昵称/wxid 模糊匹配，可多次）：
  python3 mac-test.py --fresh --chat 文件传输助手
  python3 mac-test.py --fresh --chat 妈 --chat 爱心小屋
  python3 mac-test.py --fresh --chat 47745322040@chatroom

首次使用前：
  1. 完全退出微信
  2. sudo codesign --force --deep --sign - /Applications/微信.app
  3. 重新打开微信并登录
  4. pip3 install -r requirements.txt
  5. python3 mac-test.py

微信 4.1+ 首次提密钥时，脚本会要求你在微信里「退出登录 → 再登录」一次
（用来触发 passphrase 计算，LLDB 才能抓到）。之后会缓存，不必重复。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform != "darwin":
    raise SystemExit("本脚本仅支持 macOS。")

MAC_DIR = Path(__file__).resolve().parent / "mac"
sys.path.insert(0, str(MAC_DIR))

import pandas as pd
from config import load_config
from decrypt_db import decrypt_database
from key_utils import get_key_info, strip_key_metadata
from media_export import MediaExporter

try:
    import zstandard as _zstd
except ImportError:
    _zstd = None

_zstd_dctx = _zstd.ZstdDecompressor() if _zstd else None
GROUP_PREFIX_RE = re.compile(r"^((?:wxid_[\w]+|[\w-]+@chatroom|gh_[\w]+)):\n")

CST = timezone(timedelta(hours=8))
MSG_TYPES = {
    1: "文本",
    3: "图片",
    34: "语音",
    42: "名片",
    43: "视频",
    47: "表情",
    48: "位置",
    49: "链接/文件/小程序",
    50: "语音/视频通话",
    10000: "系统提示",
    10002: "撤回消息",
}

# 导出到 Excel 的消息类型
EXPORT_TYPES = {1, 3, 34, 43}


def wechat_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True, text=True)
        return r.returncode == 0 and bool(r.stdout.strip())
    except OSError:
        return False


def _load_valid_keys(keys_file: Path) -> dict:
    if not keys_file.exists():
        return {}
    try:
        with open(keys_file, encoding="utf-8") as f:
            return strip_key_metadata(json.load(f))
    except (json.JSONDecodeError, OSError):
        return {}


def extract_keys(db_dir: str, force: bool = False, timeout: int = 300) -> Path:
    """用 wcdb-key-tool 提取密钥（兼容 4.0 内存扫描 + 4.1+ LLDB/PBKDF2）。"""
    keys_file = MAC_DIR / "all_keys.json"
    existing = _load_valid_keys(keys_file)
    if existing and not force:
        print(f"已存在有效密钥 {len(existing)} 个，跳过提取：{keys_file}")
        print("（加 --refresh-keys 可强制重提）")
        return keys_file

    if not wechat_running():
        raise SystemExit("未检测到运行中的 WeChat，请先打开并登录 Mac 微信。")

    tool = MAC_DIR / "wcdb_key_tool_macos.py"
    if not tool.exists():
        raise SystemExit(f"缺少取钥工具：{tool}")

    # 清掉上次失败留下的空文件，避免工具误判
    if keys_file.exists() and not existing:
        keys_file.unlink()

    print("正在提取数据库密钥（需要 sudo，兼容微信 4.1+）...")
    print("若提示 task_for_pid / attach 失败，请先退出微信后执行：")
    print("  sudo codesign --force --deep --sign - /Applications/微信.app")
    print("然后重新打开微信再运行。\n")
    print("【微信 4.1+ 首次】当提示「需要捕获 passphrase」时：")
    print("  在微信里：设置 → 退出登录 → 再重新登录（不要直接关掉微信进程）\n")

    env = os.environ.copy()
    # 避免 sudo 后 HOME 变成 /var/root，导致 passphrase 缓存路径错乱
    sudo_user = env.get("SUDO_USER")
    if not sudo_user:
        env.setdefault("HOME", str(Path.home()))

    cmd = [
        "sudo",
        "-E",
        sys.executable,
        str(tool),
        "extract",
        "--db-dir",
        db_dir,
        "--output",
        str(keys_file),
        "--timeout",
        str(timeout),
    ]
    try:
        subprocess.check_call(cmd, cwd=str(MAC_DIR), env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"密钥提取失败（退出码 {e.returncode}）。"
            "请确认已重签微信，并在提示时完成「退出登录→重新登录」。"
        ) from e

    keys = _load_valid_keys(keys_file)
    if not keys:
        raise SystemExit("密钥提取结果为空。请加 --refresh-keys 重试，并在提示时重新登录微信。")

    try:
        os.chown(keys_file, os.getuid(), os.getgid())
    except OSError:
        pass

    print(f"密钥提取完成：{len(keys)} 个数据库")
    return keys_file


def decrypt_all(cfg: dict, force: bool = False) -> Path:
    """解密 db_storage 下所有有密钥的数据库。"""
    out_dir = Path(cfg["decrypted_dir"])
    keys_file = Path(cfg["keys_file"])
    db_dir = Path(cfg["db_dir"])

    contact_db = out_dir / "contact" / "contact.db"
    if contact_db.exists() and not force:
        print(f"已存在解密结果，跳过解密：{out_dir}（加 --refresh-decrypt 可强制重解）")
        return out_dir

    if not keys_file.exists():
        raise SystemExit(f"缺少密钥文件：{keys_file}，请先提取密钥。")
    if not db_dir.is_dir():
        raise SystemExit(f"微信数据库目录不存在：{db_dir}")

    with open(keys_file, encoding="utf-8") as f:
        keys = strip_key_metadata(json.load(f))

    db_files = []
    for root, _, files in os.walk(db_dir):
        for name in files:
            if name.endswith(".db") and not name.endswith(("-wal", "-shm")):
                path = Path(root) / name
                rel = path.relative_to(db_dir).as_posix()
                db_files.append((rel, path, path.stat().st_size))
    db_files.sort(key=lambda x: x[2])

    print(f"开始解密 {len(db_files)} 个数据库 → {out_dir}")
    ok_n = fail_n = 0
    for rel, path, size in db_files:
        key_info = get_key_info(keys, rel)
        if not key_info:
            print(f"  SKIP {rel}（无密钥）")
            fail_n += 1
            continue
        enc_key = bytes.fromhex(key_info["enc_key"])
        out_path = out_dir / rel
        print(f"  解密 {rel} ({size / 1024 / 1024:.1f}MB) ...", end=" ", flush=True)
        if decrypt_database(str(path), str(out_path), enc_key):
            print("OK")
            ok_n += 1
        else:
            print("FAIL")
            fail_n += 1

    print(f"解密完成：成功 {ok_n}，失败/跳过 {fail_n}")
    return out_dir


def load_contacts(decrypted_dir: Path) -> dict[str, dict]:
    contact_db = decrypted_dir / "contact" / "contact.db"
    if not contact_db.exists():
        raise SystemExit(f"找不到联系人库：{contact_db}（请先完成解密）")

    users: dict[str, dict] = {}
    conn = sqlite3.connect(str(contact_db))
    try:
        rows = conn.execute(
            "SELECT username, nick_name, remark, alias FROM contact"
        ).fetchall()
    finally:
        conn.close()

    for username, nick, remark, alias in rows:
        users[username] = {
            "nickname": nick or "",
            "remark": remark or "",
            "alias": alias or "",
            "display": (remark or nick or username),
        }
    return users


def detect_my_wxid(
    decrypted_dir: Path, users: dict[str, dict], account_dir: Path | None = None
) -> str | None:
    """优先从账号目录名推断自己 wxid（contact.local_type=0 会误命中公众号/系统号）。"""
    if account_dir:
        wxid = account_dir.name
        if "_" in wxid:
            prefix, suffix = wxid.rsplit("_", 1)
            if len(suffix) == 4 and all(
                c in "0123456789abcdef" for c in suffix.lower()
            ):
                wxid = prefix
        if wxid:
            return wxid

    # 兜底：数据库里找（可能命中非本人）
    contact_db = decrypted_dir / "contact" / "contact.db"
    try:
        conn = sqlite3.connect(str(contact_db))
        row = conn.execute(
            "SELECT username FROM contact WHERE username LIKE 'wxid_%' AND local_type = 0 LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return row[0]
    except sqlite3.Error:
        pass
    for u in users:
        if u.startswith("wxid_"):
            return u
    return None


def _decompress_text(content: bytes) -> str | None:
    """解 WCDB zstd 压缩文本；失败返回 None。"""
    if not content:
        return ""
    if _zstd_dctx is None:
        return None
    try:
        out = _zstd_dctx.decompress(content, max_output_size=1 << 24)
    except Exception:
        # 有些条目可能不整帧，用流式兜底
        try:
            with _zstd_dctx.stream_reader(content) as r:
                out = r.read()
        except Exception:
            return None
    try:
        return out.decode("utf-8", errors="replace")
    except Exception:
        return None


def _strip_group_prefix(text: str) -> str:
    """去掉群聊文本前缀 'wxid_xxx:\\n'。"""
    return GROUP_PREFIX_RE.sub("", text or "", count=1)


def _decode_content(content, ct) -> str:
    """按 WCDB_CT 解出明文字符串（ct=4 为 zstd）。失败返回 ''。"""
    if isinstance(content, bytes):
        if ct == 4:
            return _decompress_text(content) or ""
        return ""
    return content or ""


def _voice_text(content, ct) -> str:
    """解析语音消息 XML → '[语音 N″]'。无时长则 '[语音]'。"""
    xml = _strip_group_prefix(_decode_content(content, ct))
    m = re.search(r'voicelength="(\d+)"', xml)
    if m:
        try:
            sec = max(1, round(int(m.group(1)) / 1000))
            return f"[语音 {sec}″]"
        except ValueError:
            pass
    return "[语音]"


def get_message_dbs(decrypted_dir: Path) -> list[Path]:
    """只返回真正的聊天分片库，如 message_0.db；排除 resource/media/fts 等。"""
    msg_dir = decrypted_dir / "message"
    if not msg_dir.is_dir():
        return []
    skip_tokens = ("fts", "resource", "media", "weclaw")
    return sorted(
        p
        for p in msg_dir.iterdir()
        if (
            p.suffix == ".db"
            and p.name.startswith("message_")
            and all(tok not in p.name for tok in skip_tokens)
        )
    )


def display_name(users: dict[str, dict], wxid: str) -> str:
    info = users.get(wxid)
    return info["display"] if info else wxid


def _resolve_writable_excel_path(output_excel: Path) -> Path:
    """目标 Excel 被占用/不可写时，自动改用带序号的新文件名。"""
    if not output_excel.exists():
        return output_excel
    try:
        with open(output_excel, "ab"):
            return output_excel
    except OSError:
        pass
    stem, suffix = output_excel.stem, output_excel.suffix or ".xlsx"
    for i in range(2, 100):
        candidate = output_excel.with_name(f"{stem}_{i}{suffix}")
        try:
            with open(candidate, "xb"):
                pass
            candidate.unlink()
            print(f"提示：{output_excel.name} 正被占用，已改用 {candidate.name}")
            return candidate
        except FileExistsError:
            continue
        except OSError:
            continue
    raise SystemExit(f"无法写入输出文件：{output_excel}（请先关闭已打开的 Excel）")


def export_chats_to_excel(
    decrypted_dir: Path,
    output_excel: Path,
    account_dir: Path | None = None,
    media_dir: Path | None = None,
    export_media: bool = True,
    my_wxid: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    chat_filter: list[str] | None = None,
) -> None:
    users = load_contacts(decrypted_dir)
    if not my_wxid:
        my_wxid = detect_my_wxid(decrypted_dir, users, account_dir)
    print(f"当前账号 wxid：{my_wxid or '(未识别)'}")

    if _zstd_dctx is None:
        print("提示：未安装 zstandard，zstd 压缩文本将跳过（pip3 install zstandard）")

    media: MediaExporter | None = None
    if export_media:
        if not account_dir or not account_dir.is_dir():
            print(f"警告：账号目录无效（{account_dir}），跳过媒体导出")
        else:
            out = media_dir or Path("media_export").resolve()
            media = MediaExporter(account_dir, decrypted_dir, out)
            print(f"媒体输出目录：{out}")

    hash_to_user = {
        f"Msg_{hashlib.md5(u.encode()).hexdigest()}": u for u in users
    }
    md5_to_user = {hashlib.md5(u.encode()).hexdigest(): u for u in users}

    # 聊天对象过滤：匹配 wxid / 备注 / 昵称（群聊同样适用），支持模糊
    allowed_talkers: set[str] | None = None
    if chat_filter:
        allowed_talkers = set()
        for kw in chat_filter:
            kw_l = kw.lower()
            hits = [
                u
                for u, info in users.items()
                if kw_l in u.lower()
                or kw_l in info["display"].lower()
                or kw_l in info["nickname"].lower()
                or kw_l in info["remark"].lower()
            ]
            if hits:
                allowed_talkers.update(hits)
            else:
                # 不在通讯录也可能是群（只凭表名），先按 id 加进去
                if kw.endswith("@chatroom") or kw.startswith("wxid_"):
                    allowed_talkers.add(kw)
                else:
                    print(f"  提示：未匹配到聊天对象「{kw}」（按备注/昵称/wxid 模糊查找）")
        if not allowed_talkers:
            raise SystemExit(
                "未匹配到任何聊天对象，已中止导出。"
                "请检查 --chat 关键词（备注/昵称/wxid/群名），或去掉 --chat 导出全部。"
            )
        names = sorted(allowed_talkers)
        preview = ", ".join(names[:20])
        more = f" …共 {len(names)} 个" if len(names) > 20 else ""
        print(f"筛选聊天对象 {len(names)} 个：{preview}{more}")

    rows: list[dict] = []
    dbs = get_message_dbs(decrypted_dir)
    if not dbs:
        raise SystemExit(f"未找到消息库：{decrypted_dir / 'message'}")

    type_filter = ",".join(str(t) for t in sorted(EXPORT_TYPES))

    for db_path in dbs:
        print(f"读取 {db_path.name} ...")
        conn = sqlite3.connect(str(db_path))
        try:
            tables = [
                t[0]
                for t in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
            ]
            if not tables:
                print("  跳过（无 Msg_ 表）")
                continue

            try:
                name2id = {
                    rowid: uname
                    for rowid, uname in conn.execute(
                        "SELECT rowid, user_name FROM Name2Id"
                    )
                }
            except sqlite3.OperationalError:
                print("  跳过（无 Name2Id 表）")
                continue

            for table in tables:
                talker = hash_to_user.get(table)
                if not talker and table.startswith("Msg_"):
                    talker = md5_to_user.get(table[4:], table)
                # 聊天对象过滤
                if allowed_talkers is not None and talker not in allowed_talkers:
                    continue
                chat_name = (
                    display_name(users, talker) if talker in users else (talker or table)
                )
                chat_key = (
                    talker if talker and talker in users else table.replace("Msg_", "")[:12]
                )
                # 时间范围过滤（SQL 层，create_time 是秒级时间戳）
                time_cond = ""
                params: tuple = ()
                if start_ts is not None and end_ts is not None:
                    time_cond = " AND create_time BETWEEN ? AND ?"
                    params = (start_ts, end_ts)
                elif start_ts is not None:
                    time_cond = " AND create_time >= ?"
                    params = (start_ts,)
                elif end_ts is not None:
                    time_cond = " AND create_time <= ?"
                    params = (end_ts,)

                try:
                    msgs = conn.execute(
                        f"""
                        SELECT local_id, create_time, real_sender_id, local_type,
                               message_content, packed_info_data, WCDB_CT_message_content
                        FROM {table}
                        WHERE local_type IN ({type_filter}) AND create_time > 0{time_cond}
                        ORDER BY create_time ASC
                        """,
                        params,
                    ).fetchall()
                except sqlite3.Error:
                    # 旧库可能无 WCDB_CT 列
                    try:
                        msgs = [
                            (*m, 1)
                            for m in conn.execute(
                                f"""
                                SELECT local_id, create_time, real_sender_id, local_type,
                                       message_content, packed_info_data
                                FROM {table}
                                WHERE local_type IN ({type_filter}) AND create_time > 0{time_cond}
                                ORDER BY create_time ASC
                                """,
                                params,
                            ).fetchall()
                        ]
                    except sqlite3.Error:
                        continue

                for (
                    local_id,
                    create_time,
                    sender_id,
                    local_type,
                    content,
                    packed,
                    ct,
                ) in msgs:
                    sender_wxid = name2id.get(sender_id, "")
                    if my_wxid and sender_wxid == my_wxid:
                        sender = "我"
                    else:
                        sender = (
                            display_name(users, sender_wxid) if sender_wxid else chat_name
                        )

                    msg_text = ""
                    media_path = ""
                    type_name = MSG_TYPES.get(local_type, str(local_type))

                    if local_type == 1:
                        if isinstance(content, bytes):
                            if ct == 4:
                                msg_text = _strip_group_prefix(
                                    (_decompress_text(content) or "")
                                ).strip()
                            else:
                                continue
                        else:
                            msg_text = _strip_group_prefix(content or "").strip()
                        if not msg_text:
                            continue
                    elif local_type == 3:
                        msg_text = "[图片]"
                        if media:
                            media_path = media.export_image(
                                packed, chat_key, create_time, local_id
                            )
                            if not media_path:
                                msg_text = "[图片-未找到本地文件]"
                    elif local_type == 34:
                        # 语音：macOS 不缓存音频文件，仅解析 XML 提取时长
                        msg_text = _voice_text(content, ct)
                    elif local_type == 43:
                        msg_text = "[视频]"
                        if media:
                            media_path = media.export_video(
                                packed, chat_key, create_time, local_id
                            )
                            if not media_path:
                                msg_text = "[视频-未找到本地文件]"

                    rows.append(
                        {
                            "聊天对象": chat_name,
                            "聊天对象ID": talker or table,
                            "发送者": sender,
                            "发送者ID": sender_wxid or talker or "",
                            "发送时间": datetime.fromtimestamp(
                                create_time, tz=CST
                            ).strftime("%Y-%m-%d %H:%M:%S"),
                            "消息类型": type_name,
                            "消息内容": msg_text,
                            "媒体路径": media_path,
                        }
                    )
        finally:
            conn.close()

    if media:
        print(
            f"媒体导出：图片成功 {media.ok_img} / 失败 {media.fail_img}，"
            f"视频成功 {media.ok_vid} / 失败 {media.fail_vid}"
        )

    if not rows:
        print("没有提取到任何消息。")
        return

    df = pd.DataFrame(rows)
    output_excel = _resolve_writable_excel_path(output_excel)
    df.to_excel(output_excel, index=False)
    print(f"导出完成：{output_excel}（共 {len(df)} 条）")


def main():
    parser = argparse.ArgumentParser(description="macOS 微信聊天记录导出 Excel")
    parser.add_argument("-o", "--output", default="wechat_backup_mac.xlsx", help="输出 Excel 路径")
    parser.add_argument("--media-dir", default="media_export", help="图片/视频导出目录")
    parser.add_argument("--no-media", action="store_true", help="不导出图片/视频，仅文本")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="刷新最新数据：复用已有密钥，强制重新解密并导出（日常推荐）",
    )
    parser.add_argument("--refresh-keys", action="store_true", help="强制重新提取密钥")
    parser.add_argument("--refresh-decrypt", action="store_true", help="强制重新解密数据库")
    parser.add_argument("--skip-keys", action="store_true", help="跳过密钥提取（需已有 all_keys.json）")
    parser.add_argument("--skip-decrypt", action="store_true", help="跳过解密（需已有 decrypted/）")
    parser.add_argument("--my-wxid", default=None, help="自己的 wxid（可选，一般可自动识别）")
    parser.add_argument("--timeout", type=int, default=300, help="4.1+ 等待重新登录的超时秒数（默认 300）")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help="只导出最近 N 天（含今天），与 --start/--end 互斥",
    )
    parser.add_argument(
        "--chat",
        action="append",
        default=None,
        metavar="关键词",
        help="只导出指定聊天对象/群，可多次使用；按备注/昵称/wxid 模糊匹配",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="起始日期（含），格式 YYYY-MM-DD [HH:MM:SS]，默认不限",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="结束日期（含），格式 YYYY-MM-DD [HH:MM:SS]，默认不限",
    )
    args = parser.parse_args()

    def _to_ts(s: str | None, end_of_day: bool = False) -> int | None:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=CST)
                if fmt == "%Y-%m-%d" and end_of_day:
                    dt = dt + timedelta(days=1) - timedelta(seconds=1)
                return int(dt.timestamp())
            except ValueError:
                continue
        raise SystemExit(f"日期格式无法识别：{s}（支持 YYYY-MM-DD [HH:MM:SS]）")

    if args.days is not None:
        if args.days <= 0:
            raise SystemExit("--days 必须是正整数")
        if args.start or args.end:
            raise SystemExit("--days 与 --start/--end 不能同时使用")
        now = datetime.now(CST)
        # 最近 N 天 = 从今天 00:00 往前推 N-1 天
        start_dt = (now - timedelta(days=args.days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        args.start_ts = int(start_dt.timestamp())
        args.end_ts = int(now.timestamp())
        print(f"时间范围：最近 {args.days} 天（{start_dt:%Y-%m-%d} ~ {now:%Y-%m-%d %H:%M:%S}）")
    else:
        args.start_ts = _to_ts(args.start)
        args.end_ts = _to_ts(args.end, end_of_day=True)
    if args.start_ts and args.end_ts and args.start_ts > args.end_ts:
        raise SystemExit("--start 不能晚于 --end")

    if args.fresh:
        # 日常刷新：密钥一般不变，但加密库/媒体索引会变
        args.skip_keys = True
        args.refresh_decrypt = True
        args.skip_decrypt = False

    print("加载配置...")
    cfg = load_config()
    print(f"微信数据库目录：{cfg['db_dir']}")

    if not args.skip_keys:
        extract_keys(db_dir=cfg["db_dir"], force=args.refresh_keys, timeout=args.timeout)
    elif not _load_valid_keys(Path(cfg["keys_file"])):
        raise SystemExit(
            "没有可用密钥。请先完整跑一次：python3 mac-test.py"
            "（4.1+ 可能需要退出登录再登录）"
        )

    if not args.skip_decrypt:
        decrypt_all(cfg, force=args.refresh_decrypt or args.fresh)

    account_dir = Path(cfg.get("wechat_base_dir") or Path(cfg["db_dir"]).parent)
    export_chats_to_excel(
        decrypted_dir=Path(cfg["decrypted_dir"]),
        output_excel=Path(args.output).resolve(),
        account_dir=account_dir,
        media_dir=Path(args.media_dir).resolve(),
        export_media=not args.no_media,
        my_wxid=args.my_wxid,
        start_ts=args.start_ts,
        end_ts=args.end_ts,
        chat_filter=args.chat,
    )

if __name__ == "__main__":
    main()
