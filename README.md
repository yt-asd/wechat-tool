# wechat-tool

macOS 微信聊天记录导出工具（WeChat 4.x / 4.1+）：提取密钥 → 解密 SQLCipher 数据库 → 导出 文本/图片/视频/语音 到 Excel + 媒体目录。

## 功能

- 导出文本、图片、视频、语音（含时长）
- 支持 zstd 压缩文本（`WCDB_CT=4`）
- 按时间范围 / 聊天对象（联系人或群）过滤
- 图片 `.dat`（V1/V2 AES+XOR）解密，`wxgf` 自动转 JPEG
- 密钥缓存，日常增量刷新

## 环境要求

- macOS，微信 4.x（4.1+ 需重新签名以允许内存读取）
- Python 3.9+
- 可选：`ffmpeg`（用于 `wxgf` 转图片）

```bash
pip3 install -r requirements.txt
```

## 首次使用

1. 完全退出微信
2. 重新签名以绕过 Hardened Runtime（官网版微信）：
   ```bash
   sudo codesign --force --deep --sign - /Applications/微信.app
   ```
3. 重新打开微信并登录
4. 运行：
   ```bash
   python3 mac-test.py
   ```
   微信 4.1+ 首次提密钥时，按提示在微信里「退出登录 → 再登录」一次（用于触发 passphrase 计算），之后会缓存。

## 日常使用

```bash
# 刷新最新记录（复用密钥，重新解密+导出）
python3 mac-test.py --fresh

# 只看文本、不导出媒体
python3 mac-test.py --fresh --no-media

# 最近 N 天
python3 mac-test.py --fresh --days 7

# 精确日期范围
python3 mac-test.py --fresh --start 2026-08-01 --end 2026-08-12

# 指定联系人 / 群（按备注、昵称、wxid 模糊匹配，可多次）
python3 mac-test.py --fresh --chat 文件传输助手
python3 mac-test.py --fresh --chat 妈 --chat 某群名 --days 7
```

## 输出

- `wechat_backup_mac.xlsx`：聊天明细（聊天对象/发送者/时间/类型/内容/媒体路径）
- `media_export/images/`、`media_export/videos/`：解密后的图片与视频

## 参数

| 参数 | 说明 |
| --- | --- |
| `-o, --output` | 输出 Excel 路径（默认 `wechat_backup_mac.xlsx`） |
| `--media-dir` | 图片/视频导出目录（默认 `media_export`） |
| `--no-media` | 不导出图片/视频，仅文本 |
| `--fresh` | 复用密钥，强制重新解密并导出（日常推荐） |
| `--days N` | 只导出最近 N 天（含今天） |
| `--start / --end` | 精确日期范围（与 `--days` 互斥） |
| `--chat 关键词` | 指定联系人/群，可多次 |
| `--refresh-keys` | 强制重新提取密钥 |
| `--refresh-decrypt` | 强制重新解密数据库 |
| `--skip-keys / --skip-decrypt` | 跳过对应步骤（需已有产物） |
| `--my-wxid` | 手动指定自己 wxid（一般可自动识别） |
| `--timeout` | 4.1+ 等待重新登录的超时秒数（默认 300） |

## 说明与限制

- **语音**：macOS 版微信不缓存语音文件，仅能从消息 XML 解析出时长，Excel 中显示为 `[语音 N″]`，无法导出音频。
- **图片/视频**：仅导出本地已缓存的文件；微信会定期清理未收藏的旧媒体，已过期的会标注「未找到本地文件」。
- 本工具仅用于个人数据备份。请妥善保管生成的密钥、解密数据库与导出文件，勿泄露他人。

## 安全

`.gitignore` 已排除所有运行产物（密钥 `all_keys.json`、`config.json`、`decrypted/`、`media_export/`、`*.xlsx`）。仓库仅包含工具代码，请勿将个人数据提交入库。
