# 公网部署指南（替代 Windows 安装包的另一条路）

把这个 **LLM-only 薄后端**部署到一台有公网 IP 的机器：一个常驻进程，
所有人访问到的就是同一份（已修复的）代码，无需逐台装包。

适用场景：用户能接受「上传音频 / 贴逐字稿 → DeepSeek 教练」为主链路。
是否要「浏览器里直接实时录音」决定你选哪套 HTTPS（见下）。

## 架构

```
浏览器 ──HTTPS──> Caddy(:443, 公网) ──HTTP──> web.py(127.0.0.1:8765)
```

- `web.py` 只绑回环，不直接面向公网；TLS 由前面的 Caddy 终结。
- systemd 常驻 `web.py`；Caddy 单独跑（也建议做成服务）。

## 为什么需要 HTTPS

`http://公网IP` 不是浏览器安全上下文 → **实时录音 tab 被拒**；上传音频 /
逐字稿链路 http 也能用。要让录音可用且流量加密，就得 HTTPS。

两套方案（本目录都给了）：

| | 文件 | 浏览器警告 | 需要 | 选它当… |
|---|---|---|---|---|
| **A. nip.io + 自动证书** | `Caddyfile.nip-io` | 无（受信任）| 公网 80+443 可达 | 想体验干净、给别人用 |
| **B. 自签 (`tls internal`)** | `Caddyfile.selfsigned` | 有（每浏览器点一次「继续」）| 仅 443 | 只自己/小团队测、不想开 80 |

两套都建立安全上下文，录音都能用；区别只是 A 无警告、B 有一次性警告。

## 部署步骤

```bash
# 1. 建专用系统用户 + 目录（服务以非 root 跑；sales-retro.service 即按此设计）
sudo useradd --system --create-home --shell /usr/sbin/nologin sales
sudo mkdir -p /opt/sales-retro/data/outputs
sudo cp -r src /opt/sales-retro/src
sudo chown -R sales:sales /opt/sales-retro

# 2. 建 venv 装薄后端（缺省用 uv —— 更快，本项目实测路径）
curl -LsSf https://astral.sh/uv/install.sh | sh          # 装 uv（已装可跳过）
uv venv /opt/sales-retro/venv
uv pip install --python /opt/sales-retro/venv/bin/python /opt/sales-retro/src  # 不带 [mic]，无需 PortAudio
#   无 uv 时的传统等价：
#     python3 -m venv /opt/sales-retro/venv
#     /opt/sales-retro/venv/bin/pip install /opt/sales-retro/src
sudo rm -rf /opt/sales-retro/src/build /opt/sales-retro/src/*.egg-info   # 清 setuptools 残留（硬约束 1）
sudo chown -R sales:sales /opt/sales-retro
#   前端 web_static 靠 pyproject 的 [tool.setuptools.package-data] 进 wheel——别删那行

# 3. 常驻 web.py（把 <DEPLOY_USER> 替成第 1 步的 sales）
sed 's/<DEPLOY_USER>/sales/g' deploy/sales-retro.service \
  | sudo tee /etc/systemd/system/sales-retro.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now sales-retro
systemctl status sales-retro            # 应 active (running)
curl -s http://127.0.0.1:8765/api/default-config | grep -o '"coachEngine": "[a-z]*"'
# 期望 "coachEngine": "llm"

# 4. 装 Caddy（官方仓库，自带 caddy.service，开机自启），选 A 或 B
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
#   A: 改 Caddyfile.nip-io 里的 203-0-113-45 为你的 IP（点改连字符）
#   B: 改 Caddyfile.selfsigned 里的 203.0.113.45 为你的 IP
sed 's/203-0-113-45/<你的IP连字符>/g' deploy/Caddyfile.nip-io \
  | sudo tee /etc/caddy/Caddyfile >/dev/null     # 方案 B 改用 Caddyfile.selfsigned + 203.0.113.45
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy && systemctl is-active caddy

# 5. 给 Caddy 加 HTTP basic auth（强烈建议——web.py 无鉴权且公网可达）
#   生成密码哈希（明文不落盘；交互输入或 --plaintext）：
caddy hash-password --plaintext '你的强密码'
#   把站点块（/etc/caddy/Caddyfile 里 <host> { ... } ）改成：
#     <host> {
#         basic_auth {
#             yitang_sales <上一步输出的 $2a$... 哈希>
#         }
#         reverse_proxy 127.0.0.1:8765
#         encode zstd gzip
#     }
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
#   验证：无凭证应 401，带 -u 用户:密码 应 200
curl -s -o /dev/null -w '%{http_code}\n' https://<你的IP连字符>.nip.io/
```

访问：A → `https://<你的IP连字符>.nip.io`；B → `https://<你的IP>`（点过警告后）。
均需输 basic auth 用户/密码（第 5 步设的）。改/加/删账号 = 改 Caddyfile 的
`basic_auth { }` 段（`caddy hash-password` 生成新哈希）→ `systemctl reload caddy`。

## 防火墙 / 安全组（务必）

- 放行 **443**（必须）。方案 A 还需放行 **80**（Let's Encrypt ACME 挑战）。
- `web.py` 无鉴权 + 公网可达 → **必做部署步骤 5 的 Caddy basic auth**；
  更严可再把 443/80 的**来源限制为你自己的 IP** / 加白名单。
- `8765` **不要**对公网开放（它只需 127.0.0.1）。

## 安全红线（这个 app 特有）

1. **不要在服务器配真实 key**：`/api/default-config` 把服务端 env 的
   `DEEPSEEK_API_KEY`/火山 key **明文**回前端。`sales-retro.service` 里的
   `EnvironmentFile` 默认注释掉——保持注释。让每个用户在页面 Key 弹窗自填。
2. **会谈数据留存**：录音/逐字稿堆在 `/opt/sales-retro/data/outputs/web_sessions`，
   公网机器上属敏感数据，定期清理或加密盘。
3. **ICP 备案**：国内主机用域名走 80/443 涉及备案；nip.io 主机名 + 非备案 IP
   场景灰色，正式对外/长期用请走备案合规路径。测试期 IP/nip.io 一般不触发。

## 验证清单（部署后）

- `systemctl status sales-retro` → running。
- `curl -s http://127.0.0.1:8765/ | grep -o 薄后台代理版` → 命中（根路由对）。
- `curl ... /api/default-config` → `coachEngine` 为 `llm`、`prompt` 非空。
- 浏览器开 HTTPS 地址：根路径直达薄后台页、提示词自动填、引擎只有
  DeepSeek LLM、上传后有「开始转写并运行 Copilot」；录音 tab 不报安全上下文错。
- 填自己的 DeepSeek（+火山）key，跑一遍上传/逐字稿 → 有提醒产出。

## 与 Windows 安装包的关系

两条路并存、互不影响：

- Windows 包（`packaging/`）：每用户本机一个 app，localhost 天然安全上下文。
- 公网部署（本目录）：一处部署多人用，需自管 HTTPS/安全/数据留存。

按场景选其一即可。
