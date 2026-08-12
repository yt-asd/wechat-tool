# wechat-tool

微信聊天记录导出工具：提取密钥 → 解密 SQLCipher 数据库 → 导出 文本/图片/视频/语音 到 Excel + 媒体目录。

> **当前仅实现 macOS 版**（入口：`mac-test.py`，实现目录：`mac/`）。  
> Windows 版规划放在 `win/`（尚未实现），两边入口与取钥方式不同，请勿混用。

| 平台 | 入口脚本 | 实现目录 | 状态 |
| --- | --- | --- | --- |
| **macOS** | `mac-test.py` | `mac/` | ✅ 可用（WeChat 4.x / 4.1+） |
| Windows | （待定，如 `win-test.py`） | `win/` | ❌ 未实现 |

## 功能（macOS）

- 导出文本、图片、视频、语音（含时长）
- 支持 zstd 压缩文本（`WCDB_CT=4`）
- 按时间范围 / 聊天对象（联系人或群）过滤
- 图片 `.dat`（V1/V2 AES+XOR）解密，`wxgf` 自动转 JPEG
- 密钥缓存，日常增量刷新

## 目录结构

```
wechat-tool/
├── mac-test.py          # 【macOS】主入口
├── mac/                 # 【macOS】实现（取钥 / 解密 / 媒体）
│   ├── wcdb_key_tool_macos.py
│   ├── find_all_keys_macos.c
│   ├── decrypt_db.py
│   ├── media_export.py
│   ├── config.py
│   └── key_utils.py
├── win/                 # 【Windows】预留，尚未实现
├── requirements.txt
└── README.md
```

运行产物（密钥、解密库、媒体、Excel）均在本地生成，已被 `.gitignore` 排除，不会入库。

---

# macOS 版

以下整节仅适用于 macOS，Windows 用户请忽略。

## 环境要求（macOS）

| 依赖 | 说明 |
| --- | --- |
| **macOS** | 仅本平台（依赖 `codesign` / LLDB / `task_for_pid` 读进程内存） |
| 微信 for Mac | 4.x（4.1+ 必须重新签名，见下文）。**建议用官网版**，App Store 版带 Hardened Runtime，`codesign` 可能失败 |
| Python | 3.9+（推荐 3.11/3.12，需带 `pip3`） |
| ffmpeg | 可选，用于把 `wxgf` 图片转成 JPEG（不装则保留 `wxgf` 原样） |

> Windows 环境要求与取钥方式不同（不走 `codesign`/LLDB），后续单独写在 `win/` 文档里。

## 环境部署（macOS）

### 1. 安装 Python 依赖

```bash
# 克隆后进入目录
cd wechat-tool

# 安装依赖（pandas / openpyxl / pycryptodome / zstandard）
pip3 install -r requirements.txt
```

如果提示权限不足或想隔离环境，建议用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 之后运行都用：python mac-test.py（注意是 python 不是 python3）
```

> 公司网络/代理下若 `pip` 装不上，可加镜像：
> `pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 2. 安装 ffmpeg（可选，图片转码用）【macOS】

```bash
brew install ffmpeg
```

没有 Homebrew 的话先装：`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`

### 3. 重新签名微信（4.1+ 必需，一次性）【macOS 独有】

> 作用：去掉 Hardened Runtime 限制，允许脚本读取微信进程内存来取密钥。  
> **此步骤仅 macOS 需要**，Windows 版不会走 `codesign`。

```bash
# 1) 完全退出微信（菜单 微信 → 退出，或 Cmd+Q，别只关窗口）

# 2) 重新签名（注意应用名可能是「微信.app」或「WeChat.app」）
sudo codesign --force --deep --sign - /Applications/微信.app

# 3) 重新打开微信并登录
```

如果 `codesign` 报 `Operation not permitted`（常见于 App Store 版），改用官网版微信：卸载后到 [微信官网](https://mac.weixin.qq.com/) 下载安装，再执行上面的签名命令。

### 4. 验证部署（macOS）

```bash
python3 mac-test.py
```

首次运行微信 4.1+ 会提示「需要捕获 passphrase」，此时在微信里：**设置 → 退出登录 → 再重新登录**（不要直接关进程）。密钥抓到后会缓存到 `mac/all_keys.json`，之后不必重复。

## 日常使用（macOS）

入口固定为 `mac-test.py`（不要拿去在 Windows 上跑）。

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

## 输出（macOS）

- `wechat_backup_mac.xlsx`：聊天明细（聊天对象/发送者/时间/类型/内容/媒体路径）
- `media_export/images/`、`media_export/videos/`：解密后的图片与视频

## 参数（macOS · `mac-test.py`）

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
| `--timeout` | 4.1+ 等待重新登录的超时秒数（默认 300）【macOS 独有】 |

## 说明与限制（macOS）

- **语音**：macOS 版微信不缓存语音文件，仅能从消息 XML 解析出时长，Excel 中显示为 `[语音 N″]`，无法导出音频。
- **图片/视频**：仅导出本地已缓存的文件；微信会定期清理未收藏的旧媒体，已过期的会标注「未找到本地文件」。
- 本工具仅用于个人数据备份。请妥善保管生成的密钥、解密数据库与导出文件，勿泄露他人。

## 常见问题（macOS）

**1. 提取密钥时显示「0 个数据库 / 0 unique keys」**
多为微信 4.1+ 改变了密钥存储方式。脚本会改用 LLDB 抓取 passphrase——请在提示时于微信内「设置 → 退出登录 → 再登录」，并确认已执行过 `codesign` 重签。

**2. `codesign` 报 `Operation not permitted` / `resource fork... detached`**【macOS 独有】
是 App Store 版微信的 Hardened Runtime 限制。先 `sudo xattr -cr /Applications/微信.app` 再重签；仍失败则改用官网版微信。

**3. 提示 `task_for_pid / attach 失败`**【macOS 独有】
微信未重签或未完全退出。退出微信 → 重新 `codesign` → 重开微信登录后再跑。

**4. 导出的图片是 `.wxgf` 打不开**
没装 `ffmpeg`。`brew install ffmpeg` 后重新 `--fresh` 导出即可自动转成 JPEG。

**5. 图片/视频显示「未找到本地文件」**
该媒体在本地无缓存（未下载或已被微信清理）。脚本已做兜底全盘检索，仍找不到则说明源文件确实不存在，属正常。

**6. 想重新提取密钥 / 重新解密**
```bash
python3 mac-test.py --refresh-keys      # 重新提密钥（4.1+ 需再次退出登录）
python3 mac-test.py --refresh-decrypt   # 用现有密钥重新解密
```

**7. 能在 Windows 上跑 `mac-test.py` 吗？**
不能。脚本开头会检测 `sys.platform != "darwin"` 并直接退出。Windows 请等 `win/` 实现。

---

# Windows 版

> **尚未实现。** 规划目录：`win/`，入口另行约定（如 `win-test.py`）。  
> Windows 取钥、数据路径、媒体格式与 macOS 均不同，请勿把 `mac/` 下的脚本拿到 Windows 使用，也不要把 Windows 实现塞进 `mac/`。

## 安全

`.gitignore` 已排除所有运行产物（密钥 `all_keys.json`、`config.json`、`decrypted/`、`media_export/`、`*.xlsx`）。仓库仅包含工具代码，请勿将个人数据提交入库。建议把本仓库设为 **Private**。
