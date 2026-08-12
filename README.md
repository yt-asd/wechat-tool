# wechat-tool

把电脑微信里的聊天记录导出成 **Excel 表格**，并尽量导出图片、视频。

> 适合：想备份聊天记录、不会编程的人。  
> 只要按下面步骤「复制 → 粘贴 → 回车」即可。

---

## 先看这里：你用的是哪种电脑？

| 你的电脑 | 看哪一节 | 每次运行这条命令 |
| --- | --- | --- |
| **苹果 Mac** | [Mac 使用教程](#mac-使用教程) | `python3 mac-test.py --fresh` |
| **Windows 电脑** | [Windows 使用教程](#windows-使用教程) | `python win-test.py --fresh` |

⚠️ **千万不要混用**：Mac 不要跑 `win-test.py`，Windows 不要跑 `mac-test.py`。

---

## 导出后你会得到什么？

在工具文件夹里会出现：

| 文件/文件夹 | 是什么 |
| --- | --- |
| `wechat_backup_mac.xlsx` 或 `wechat_backup_win.xlsx` | 用 Excel / WPS 打开就能看的聊天记录 |
| `media_export/images/` | 导出的图片 |
| `media_export/videos/` | 导出的视频 |

表格里大致有：和谁聊的、谁发的、时间、文字内容、图片/视频路径。

**说明（很重要）：**

- **语音**：表格里一般只显示类似 `[语音 5″]`（有时长），**导不出语音文件本身**（电脑微信通常不保存语音文件）。
- **很久以前的图片/视频**：如果微信里已经看不到了，这里也可能找不到，会写成「未找到本地文件」。这是正常的。

---

## 开始前准备（两种电脑都要）

1. **电脑上已安装微信**，并且能正常登录。
2. **安装 Python 3.9 或更高版本**（推荐 **3.10 / 3.11 / 3.12**）：
   - 先在终端/命令行输入下面命令看版本：
     - Mac：`python3 --version`
     - Windows：`python --version`
   - 显示类似 `Python 3.12.x` 且 **≥ 3.9** 就可以
   - 没有安装，或版本低于 3.9：到 [python.org](https://www.python.org/downloads/) 下载安装  
     - Windows 安装时务必勾选 **“Add Python to PATH”**
     - 不建议用已经停止维护的 3.8 及以下
3. 把本工具下载到电脑（例如解压到桌面的 `wechat-tool` 文件夹）。

---

# Windows 使用教程

按顺序做，做完第 1～4 步，以后平时只用第 5 步。

## 第 1 步：打开「管理员」命令窗口

1. 按键盘 `Win` 键，搜索 **「命令提示符」** 或 **「PowerShell」**
2. **右键** → 选择 **「以管理员身份运行」**（很重要，否则可能提不出密钥）
3. 进入工具文件夹（把路径改成你自己的）：

```text
cd 桌面上的路径\wechat-tool
```

例如工具在 `D:\wechat-tool`，就输入：

```text
cd /d D:\wechat-tool
```

## 第 2 步：安装需要的小工具（只做一次）

```text
pip install -r requirements.txt
```

如果很慢或失败，换成：

```text
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

看到一堆 `Successfully installed ...` 就说明好了。

## 第 3 步：打开并登录微信

保持微信登录状态，不要退出。

## 第 4 步：第一次导出（会稍慢）

在刚才的管理员窗口里输入：

```text
python win-test.py
```

耐心等它跑完。成功后，文件夹里会出现 Excel 和 `media_export`。

### 如果图片都解不开？

1. 在微信里随便点开几张聊天图片（让电脑缓存一下）
2. 再运行：

```text
python windows/find_image_key_windows.py
```

3. 然后再导出一次：

```text
python win-test.py --fresh
```

## 第 5 步：以后日常怎么用（最常用）

每次想更新最新聊天记录，先打开微信并登录，再用**管理员窗口**进入工具目录，运行：

```text
python win-test.py --fresh
```

### 常用「加料」命令（可复制）

```text
:: 只要文字，不要图片视频（更快）
python win-test.py --fresh --no-media

:: 只要最近 7 天
python win-test.py --fresh --days 7

:: 只要某个人或某个群（名字写成你微信里的备注/群名）
python win-test.py --fresh --chat 文件传输助手

:: 最近 7 天 + 指定联系人/群
python win-test.py --fresh --chat 张三 --days 7
```

---

# Mac 使用教程

按顺序做。Mac **第一次**会多一步「重新签名微信」，之后就不用了。

## 第 1 步：打开「终端」

打开「启动台」→ 搜索 **「终端」** → 打开。  
（或按 `Command + 空格`，输入「终端」回车。）

进入工具文件夹（改成你的实际路径）：

```bash
cd ~/Desktop/wechat-tool
```

## 第 2 步：安装需要的小工具（只做一次）

```bash
pip3 install -r requirements.txt
```

如果很慢或失败，换成：

```bash
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### （可选）让图片更好打开

有些图片格式比较特殊。如果你装了 Homebrew，可以再执行：

```bash
brew install ffmpeg
```

没有 Homebrew 也没关系，可以先跳过。

## 第 3 步：重新签名微信（只做一次，Mac 必做）

> 作用一句话：允许本工具读取微信运行时的密钥。  
> **App Store 下载的微信经常会失败**，建议用 [微信官网 Mac 版](https://mac.weixin.qq.com/) 安装。

1. **完全退出微信**：顶部菜单点「微信」→「退出微信」（或 `Command + Q`）。  
   ⚠️ 只关窗口不算退出。
2. 在终端执行（会要你输入开机密码，输入时屏幕不一定显示字符，输完回车即可）：

```bash
sudo codesign --force --deep --sign - /Applications/微信.app
```

如果提示找不到文件，试试：

```bash
sudo codesign --force --deep --sign - /Applications/WeChat.app
```

3. 重新打开微信并登录。

如果提示 `Operation not permitted`：多半是 App Store 版限制，请改用官网版微信后再做第 3 步。

## 第 4 步：第一次导出

```bash
python3 mac-test.py
```

### 如果提示要你「退出登录再登录」

这是第一次抓密钥时的正常要求：

1. 打开微信 → **设置**
2. 点 **退出登录**
3. 再重新登录

不要直接强制关掉微信进程。成功一次后，密钥会记住，以后通常不用再做。

## 第 5 步：以后日常怎么用（最常用）

打开微信并登录，在终端进入工具目录后运行：

```bash
python3 mac-test.py --fresh
```

### 常用「加料」命令（可复制）

```bash
# 只要文字，不要图片视频（更快）
python3 mac-test.py --fresh --no-media

# 只要最近 7 天
python3 mac-test.py --fresh --days 7

# 只要某个人或某个群（名字写成你微信里的备注/群名）
python3 mac-test.py --fresh --chat 文件传输助手

# 最近 7 天 + 指定联系人/群
python3 mac-test.py --fresh --chat 张三 --days 7
```

---

## 命令小抄（看不懂参数时看这里）

两边意思一样，只是前面的文件名不同：

| 你想做什么 | Mac | Windows |
| --- | --- | --- |
| 更新最新记录（日常） | `python3 mac-test.py --fresh` | `python win-test.py --fresh` |
| 只要文字 | 加上 `--no-media` | 加上 `--no-media` |
| 最近 N 天 | 加上 `--days 7` | 加上 `--days 7` |
| 只要某个人/群 | 加上 `--chat 备注名` | 加上 `--chat 备注名` |
| 指定日期范围 | `--start 2026-08-01 --end 2026-08-12` | 同左 |
| 换个 Excel 文件名 | `-o 我的备份.xlsx` | 同左 |

> `--chat` 写的名字要尽量和微信备注/群名一致。  
> 如果提示「未匹配到任何聊天对象」，说明名字写错了，不会导出全部（这是保护你的隐私）。

---

## 常见问题（先看这里再问人）

### Windows

**1. 提示权限不够 / 提不出密钥**  
请用「以管理员身份运行」打开命令窗口，并确认微信已登录。

**2. 图片都是解不开的**  
先在微信里点开几张图，再运行 `python windows/find_image_key_windows.py`，然后 `python win-test.py --fresh`。

**3. 我把 Mac 的命令拿到 Windows 跑了**  
不行。Windows 请用 `win-test.py`。

### Mac

**1. 提示 0 个密钥 / 失败**  
确认已经做过「重新签名」，并且按提示在微信里「退出登录 → 再登录」。

**2. codesign 一直失败**  
换官网版微信，不要用 App Store 版。

**3. 图片是 `.wxgf` 打不开**  
安装 `ffmpeg`（见上面可选步骤）后再 `--fresh` 导出一次。

**4. 提示找不到图片/视频**  
微信本地已经没有这份文件了（没下载，或太久被清掉）。不是工具坏了。

### 两边通用

**Excel 打不开？**  
用 Excel、WPS 或 Numbers 打开 `wechat_backup_*.xlsx`。

**想重新来一遍（密钥也重提）？**

- Mac：`python3 mac-test.py --refresh-keys`
- Windows：`python win-test.py --refresh-keys`

---

## 隐私与安全（请认真看）

- 导出的 Excel、图片、视频、密钥文件都在**你自己电脑**上。
- 这些文件等于你的聊天记录，**不要发给别人，也不要上传到网盘公开分享**。
- 如果把本工具发到 GitHub，请把仓库设为 **Private（私有）**，并且不要把导出结果提交上去。
- 本工具只建议用于**备份你自己的聊天记录**。

---

## 给愿意深入了解的人（可跳过）

技术流程简述：读取微信进程中的数据库密钥 → 解密本地数据库 → 导出到 Excel / 媒体目录。

| 平台 | 入口文件 | 代码目录 |
| --- | --- | --- |
| Mac | `mac-test.py` | `mac/` |
| Windows | `win-test.py` | `windows/` |

更细的参数：

| 参数 | 作用 |
| --- | --- |
| `--fresh` | 复用已有密钥，重新解密并导出（日常推荐） |
| `--no-media` | 不导出图片/视频 |
| `--days N` | 最近 N 天 |
| `--start` / `--end` | 日期范围（不要和 `--days` 一起用） |
| `--chat 关键词` | 按备注/昵称/群名筛选，可写多次 |
| `--refresh-keys` | 强制重新提取密钥 |
| `--refresh-decrypt` | 强制重新解密数据库 |
| `--skip-keys` / `--skip-decrypt` | 跳过对应步骤（已有结果时） |
| `--my-wxid` | 手动指定自己的微信 ID（一般不用） |
| `--timeout` | 仅 Mac：等待重新登录的秒数（默认 300） |
