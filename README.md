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
  - **上传走生二进制直送 + 进度条**：文件以原始字节 `POST /api/audio-upload`（`Content-Type`
    非 JSON 即走二进制分支，文件名/sessionId/mimeType 走 `X-File-Name`/`X-Session-Id`/`X-File-Type`
    头、URL 编码），不再 base64-in-JSON——省掉约 33% 体积膨胀和构建/解析巨型字符串的开销，大文件
    保存明显更快。前端用 `XMLHttpRequest` 的 `upload.progress` 显示「正在上传 xx%」，避免上传期间
    面板像卡死（旧版正是因为看不到进度被反复点击，才起了并发运行）。旧的 base64 JSON 体仍兼容
    （实时录音分块 `/api/audio-chunk` 继续用 base64）。
  - **选文件即上传**：上传面板选中录音文件后**立即**自动上传（带进度），不需要单独的保存步骤
    （旧的「仅保存到本次日志」按钮已移除）。「开始转写并运行 Copilot」只在**上传完成后**才可点；
    未选文件 / 上传中一律 disabled，从源头杜绝「没传完就开跑」。
  - **运行/上传中锁定「清除日志」**：转写或上传进行中，「清除日志」按钮 disabled，后端
    `POST /api/logs/clear` 对在跑的 session 也直接拒绝（`409 Running`）。否则中途清日志会 `rmtree`
    正在写入的 session 目录、并清空前端 `sessionId`，导致「提前终止」失效、UI 卡在「正在提前终止」。
  - **手动「清除日志」不清提醒**：手动点「清除日志」只重置过程日志与会话，**保留 Copilot 提醒区**（提醒是
    结果产出，不应随日志一起抹掉）；「导出诊断包」在无会话时给出提示，而非凭空建一个空 session。
    （注意区分下面的「结束工作收尾」——那条路径会连提醒一起清。）
  - **工作状态锁 Tab / 配置**：定义「工作状态」为录音中、文件上传中、上传转写运行中（点「开始转写」到
    结束/提前终止前）、逐字稿运行中四种之一。`updateWorkingLock()`/`isBusy()` 在工作状态期间禁用三个
    模式 Tab、整个配置面板（提示词/间隔/引擎/Key/保存）与「清除日志」，杜绝中途切换上下文或改配置造成
    日志与会话错位。Tab 既 `disabled` 又在点击处理器内 `isBusy()` 兜底。
  - **结束工作收尾**：每条「结束工作」路径（停止录音 / 上传转写的 `finally`，含正常结束·提前终止·异常 /
    逐字稿运行的 `finally`）统一走 `finishWork()`：弹框问是否「导出诊断包」（文案点明*不导出将不保留本次
    结果*）→ 选导出则触发**导航式下载**（`<a download>` 指向 `/api/diagnostics?...&clearAfter=1`）并重置前端
    UI；不导出则调 `/api/logs/clear` 再重置 → 解锁 Tab/配置。跨次（如逐字稿调试反复改提示词）的结果对比改由
    下载的诊断包离线进行。停止录音前会先 `await` 在途切片上传完成，避免最后一片在清除时被删。
  - **导出+清除的原子化（修 Caddy 代理下导不出包）**：清除会 `rmtree` session 目录，与导出打包存在竞态
    （`ThreadingHTTPServer` 并发）。曾用 **fetch+Blob**「先取回再清」规避，但在 Caddy/HTTPS 代理 + 大文件下
    **静默失败**（下载在 `await` 之后才触发、丢了用户手势激活；本地 localhost 秒回所以没暴露）。现改为：导出走
    **浏览器导航下载**（下载管理器原生处理 basicauth/gzip/大文件/慢链路），清除竞态移到**服务端原子处理**——
    `GET /api/diagnostics?clearAfter=1` 在**把 zip 读进内存后、再 `clear_logs()` 删目录、最后发出内存里的 zip**，
    单请求内完成，前端无需 `await`，彻底消除竞态。
  - **「提前终止」**：因转写按实时配速、长音频耗时长，上传面板在运行期间显示「提前终止」
    按钮。点击后 `POST /api/coach-upload/cancel` 给该 session 置取消标志，后台在下一个 ASR
    事件处停止推流与 Copilot 评估，并把已转写部分正常落盘（`uploaded_audio_transcript.txt`、
    `coach_upload_completed{cancelled:true}`）。因此随后导出的诊断包即为「截止到终止时刻」的数据。
  - **单运行保护（同一 session 不并发）**：上传转写按实时配速可耗时数分钟，旧版「开始转写并运行
    pilot」按钮在上传阶段仍可点，重复点击会对同一 session 起多个并发运行，各自按自己的音频时钟出
    提醒、写进同一份日志，导致提醒 `elapsedMinutes` 乱序（实测 2,8,2,2,7）。现做两层防护：前端按钮
    **点击瞬间置灰**（覆盖整个上传+转写阶段，本次运行结束才恢复）；后端对同一 sessionId 的二次
    `POST /api/coach-upload` 直接拒绝（`409 AlreadyRunning`，记 `coach_upload_rejected` 事件）。
  - **ASR 原始帧捕获（排障用）**：`VolcAsrEngine` 把火山服务端 payload 原样作为 `asr_raw_payload`
    事件写入 `events.jsonl`（随诊断包导出），用于核对 `result.utterances[].definite`、`additions.speaker`
    等字段名而无需在 GUI 里抓 stdout。分两桶：前若干**早期（partial）帧** + 首批**含定稿/多 utterance 的帧**
    （partial→definite 转变处，去重 bug 就在这里）。均有上限，不会撑大日志。已实测确认 `definite` 字段存在。
  - **转写去重根治 + 说话人标注（`UtteranceAccumulator`）**：火山流式按 `result.utterances[]` 返回，每句带
    `definite`（false=中间/true=定稿）；定稿时会做 ITN/标点改写（如「90」→「九十」），使顶层累积 `result.text`
    的前缀**被改写**，旧的 `TranscriptCursor` 因 `startswith` 失败、overlap 又匹配不到而把整句**重复 append**。
    现改为：引擎只提交 **definite 句**（用稳定的 `(start_time,end_time)` 做键，避免列表滑动时漏/重），committed
    文本天然只增不改 → 下游 cursor 永不重复；流结束时再 `flush_partial()` 补回最后一个未定稿句，避免丢尾。
    说话人取自 **`utterances[].additions.speaker_id`**（实测字段名是 `speaker_id`，不是文档写的 `speaker`，且
    只在定稿句出现），按说话人切换插入 `[说话人N]` 前缀（`enable_speaker_labels` 默认开；N 是 ID 不是角色，
    销售/客户映射由使用方决定）。无 `utterances` 的旧变体回退到原 `result.text` 路径。
    **声纹聚类分离的启用条件**（流式文档+实测）：`enable_speaker_info:true` + `ssd_version:"200"` +
    `enable_nonstream:true`（`bigmodel_async` 属"双向流式优化接口"需要）+ language 不指定或 `zh-CN`。缺任一
    （尤其漏 `ssd_version`/`enable_nonstream`）会**恒定返回单一说话人 "0"**——四项齐全后实测 `test_01.mp3` 已分出
    `speaker_id` 0/1/…，且未影响转写。`asr_init_sent` 事件记录实际发送的 request 参数，便于核对开关是否真发出。
  - **鉴权错误友好化**：火山 ASR 握手被拒（`401/403`）由 `asr_volc.friendly_ws_error` 转成「火山 ASR
    鉴权失败：请检查 Key 与 Resource ID」；LLM API 鉴权失败（`401/403`）由 `deepseek_client.friendly_llm_error`
    转成「LLM 鉴权失败：请检查 Key 与 Base URL」——措辞用中性的「LLM」而非写死某家厂商。非鉴权错误原样透传，
    不掩盖真实原因。
  - **带 `[MM:SS]` 时间戳的逐字稿**：每次音频/录音转写都会在 session 目录额外落盘
    `uploaded_audio_transcript_timestamped.txt`（随诊断包导出）。它**按 Copilot 实际评估的窗口**
    分段，行首 `[MM:SS]` 是该窗口的音频时间位置。把它粘进「逐字稿调试」运行时，后台用
    `parse_timestamped_transcript()` 逐窗复现**完全相同**的 `(窗口文本, elapsed_minutes)` 序列
    （`elapsed_minutes = max(1, 秒//60)`，与上传路径同一公式），因此 Copilot 行为与原录音/音频
    文件一致；没有 `[MM:SS]` 标记的普通逐字稿仍按字数分块近似（向后兼容）。
  - **时间戳逐字稿内嵌 Copilot 提醒（`<ALERT>`）**：上传转写产生的每条 Copilot 提醒（含网页卡片显示的
    分钟·优先级·类型、message、建议提问、理由）会以 `<ALERT>…</ALERT>` 块写入
    `uploaded_audio_transcript_timestamped.txt`，**紧跟在产生它的那个 `[MM:SS]` 窗口之后**（按 alert 的
    `elapsedSeconds` 精确定位，alert 事件已补该字段）。这样诊断包的逐字稿能就地看到"哪段对话触发了哪条提醒"。
    **「逐字稿调试/分析」回放时会先 `re.sub` 剥掉 `<ALERT>` 块**（`_ALERT_BLOCK_RE`，web.py 与 realtime_runner
    两处解析器都做），所以提醒只是结果展示、**不会被当作逐字稿重新喂给 Copilot**。
- **已做 §6 第 1 步解耦**（见 `packaging/PACKAGING_NOTES.md`）：
  - `audio_sources.py` 的 `sounddevice` 改为惰性导入；薄后端导入链不再需要 PortAudio。
  - `pyproject.toml` 已删除指向未复制 `cli.py` 的悬空 `[project.scripts]` 入口。
  - `sounddevice` 从基础依赖移至可选 extra `mic`；薄后端 `pip install .` 不再安装它。
- `audio_sources.py` 依赖的 numpy/av 为外部 pip 包，由 `pip install .` 安装，不在源码复制范围。
- **LLM-only 交付**：根路径 `/` 直达 `backend.html`，`index.html` 重定向至此；
  默认引擎固定 DeepSeek LLM（不提供规则引擎），默认提示词由 `/api/default-config`
  下发。详见 `packaging/PACKAGING_NOTES.md`「安装包反馈修复」。
