# sales_02 — 前端 + 薄后端（精简副本）

从 `sales_retro` 抽取出的「前端 + 薄后端」最小可运行子集。**仅含运行所需源码，不含安装/打包文件。**

## 目录结构

```
sales_02/
  src/                       # 可运行源程序
    pyproject.toml
    sales_retro_agent/
      __init__.py
      web.py                 # 薄后端入口（HTTP server + API）
      asr_volc.py            # 火山 ASR WebSocket 引擎
      asr_types.py           # ASR 类型（asr_volc / realtime_runner 依赖，原清单遗漏）
      audio_sources.py       # 音频解码/分帧（依赖 numpy/av；麦克风路径才用 sounddevice）
      coach_debug.py
      config.py
      deepseek_client.py     # DeepSeek 调用（llm_coach 间接依赖）
      llm_debug.py           # deepseek_client 依赖（原清单遗漏）
      llm_coach.py           # LLM 教练
      realtime_coach.py      # 规则引擎教练
      realtime_runner.py
      text_diff.py
      volc_protocol.py
      web_static/            # 前端
        index.html  app.js   # 纯前端版
        backend.html backend.js  # 薄后端版
        styles.css
  packaging/                 # 打包（与 src/ 解耦）
    launcher.py              # 冻结应用入口
    sales_retro.spec         # PyInstaller one-folder 配置
    installer.iss            # Inno Setup → 单个 setup.exe（按用户安装）
    build.ps1 / build.sh     # Windows / Linux 构建
    PACKAGING_NOTES.md       # 计划与进度
  deploy/                    # 公网部署（与 src/、packaging/ 三者解耦）
    DEPLOY.md                # 公网 IP 部署指南 + 清单（先读这个）
    sales-retro.service      # systemd 常驻单元（app 绑回环）
    Caddyfile.nip-io         # HTTPS-over-IP A：nip.io + 自动证书（无警告）
    Caddyfile.selfsigned     # HTTPS-over-IP B：自签（一次性警告）
    gen-selfsigned-cert.sh   # 可选：带 IP SAN 的自签证书生成
```

两条交付路径并存，按场景二选一：
- **本机一键包**：`packaging/`（每用户本机一个 app，localhost 天然安全上下文）。
- **公网部署**：`deploy/DEPLOY.md`（一处部署多人用，自管 HTTPS/安全/数据留存）。

## 运行

```bash
cd src
pip install .            # 薄后端：不含 sounddevice/PortAudio
# pip install ".[mic]"   # 仅当需要 CLI 麦克风采集时再装
python -m sales_retro_agent.web --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765/backend.html
```

## 本机一键包（PyInstaller one-folder）

```powershell
# Windows（最终交付物，须在 Windows 上构建，PyInstaller 不跨平台）
cd packaging; .\build.ps1
# 装了 Inno Setup 6 → 产物 packaging\Output\SalesRetro-Setup.exe（单个安装包）
# 未装 Inno Setup → 仅产物 packaging\dist\SalesRetro\（双击 SalesRetro.exe 即用）
# 安装为按用户级（无需管理员），开始菜单出现快捷方式，运行后自动开 backend.html
```

```bash
# Linux/macOS（仅作 spec 交叉验证，非 Windows 交付物）
cd packaging && ./build.sh
```

无 Windows 机器时，打 tag 让 CI 出安装包：

```bash
git push origin master                 # 普通 push 不触发构建
git tag v0.1.0 && git push origin v0.1.0   # 打 v* tag → windows runner 出 setup.exe
```

产物在该 tag 的 GitHub Release / Actions artifact 中下载。private 仓库可用
（Actions 支持 private，Windows runner 按 2x 计额度，测试用量足够）。

详见 `packaging/PACKAGING_NOTES.md`（含进度、风险、后续验收步骤）。

## 说明

- 复制的是 `web.py` 的完整 import 闭包；`asr_types.py`、`llm_debug.py` 是运行必需但原清单未列出的模块，已补入。
- **上传录音解码用 PyAV（`av`）**：其 wheel 自带 ffmpeg 库，无需终端用户另装系统 ffmpeg，
  可解 wav/flac/ogg/mp3/m4a/webm 等浏览器与手机常见格式（旧版用 `soundfile` 读不了
  webm/opus、m4a/aac）。上传文件会先整段解码成 16k 单声道 PCM **再**连火山 ASR，
  避免「连上会话却来不及发第一个音频包」触发火山 `45000081 waiting next packet
  timeout` 超时断连。发包按**音频原始速度**配速（`realtime=True`）：火山 SAUC 是
  流式引擎，按收包节奏出结果；若全速一次性灌入会冲垮其缓冲区，只识别开头几秒就
  收尾（实测 113 分钟音频只出 608 字）。因此上传一段 N 分钟音频，转写约需 N 分钟。
  - **「提前终止」**：因转写按实时配速、长音频耗时长，上传面板在运行期间显示「提前终止」
    按钮。点击后 `POST /api/coach-upload/cancel` 给该 session 置取消标志，后台在下一个 ASR
    事件处停止推流与 Copilot 评估，并把已转写部分正常落盘（`uploaded_audio_transcript.txt`、
    `coach_upload_completed{cancelled:true}`）。因此随后导出的诊断包即为「截止到终止时刻」的数据。
- **已做 §6 第 1 步解耦**（见 `packaging/PACKAGING_NOTES.md`）：
  - `audio_sources.py` 的 `sounddevice` 改为惰性导入；薄后端导入链不再需要 PortAudio。
  - `pyproject.toml` 已删除指向未复制 `cli.py` 的悬空 `[project.scripts]` 入口。
  - `sounddevice` 从基础依赖移至可选 extra `mic`；薄后端 `pip install .` 不再安装它。
- `audio_sources.py` 依赖的 numpy/av 为外部 pip 包，由 `pip install .` 安装，不在源码复制范围。
- **LLM-only 交付**：根路径 `/` 直达 `backend.html`，`index.html` 重定向至此；
  默认引擎固定 DeepSeek LLM（不提供规则引擎），默认提示词由 `/api/default-config`
  下发。详见 `packaging/PACKAGING_NOTES.md`「安装包反馈修复」。
