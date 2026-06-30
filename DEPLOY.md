# 公网部署指南（VPS）

DHT 磁力爬虫**必须跑在有公网 IP 的机器上**才能真正大量抓到元数据。原因：

- 家庭/办公网络在 NAT 后，**收不到 `announce_peer`**（那是"已确认持有种子、且可连接的做种者"，是下载元数据成功率最高的来源）；
- NAT 后向公网 peer 发起的 TCP 多被防火墙拦截，连接大面积超时。

放到公网 IP 的 VPS 上，这两个问题都消失：能持续收到 announce_peer，也能连上真实做种者，入库率会从"几乎为 0"变成"每分钟稳定增长"。

> 实测对照：本机 NAT 下 11 分钟抓到 8468 个 info_hash 但 0 入库；同样代码在公网 VPS 上可正常入库。代码已通过本地回放真实种子数据验证（BEP-9/10 协议正确）。

---

## 一、推荐：Docker Compose 一键部署（Linux VPS）

前置：一台公网 VPS（Ubuntu/Debian 等 Linux），已装 Docker + Docker Compose。

```bash
# 1) 上传/克隆代码到 VPS
scp -r magnet-probe user@your-vps:/opt/   # 或 git clone
ssh user@your-vps
cd /opt/magnet-probe

# 2) 一键起三件套：mongo + crawler + web
docker compose up -d --build

# 3) 看日志确认在抓
docker compose logs -f crawler
# 应看到 found= 持续增长，且陆续出现 "saved ..." 行
```

`docker-compose.yml` 已配置：
- `mongo`：数据卷持久化，仅绑定 `127.0.0.1:27017`（不对公网开放数据库）；
- `crawler`/`web`：`network_mode: host`，让 DHT 用稳定公网 UDP 端口收发。

### 必须放开的防火墙 / 安全组

在云厂商**安全组**和 VPS 本机防火墙都放行 DHT 的**入站 UDP**端口（收 announce_peer 的关键）：

```bash
# ufw 示例
sudo ufw allow 6881:6884/udp
```

- 入站：UDP 6881-6884（DHT）。
- 出站：默认全放行即可（metadata 走任意高位 TCP 出站）。
- **不要**对公网开放 27017（MongoDB）和 8080（Web，见下文反代）。

---

## 二、对外暴露 Web（nginx 反代 + 鉴权）

抓到的内容较敏感，**不要**把 8080 直接裸露公网。用 nginx 反代并加 HTTP Basic Auth：

```bash
sudo apt install nginx apache2-utils -y
sudo htpasswd -c /etc/nginx/.htpasswd admin   # 设置用户名密码
```

`/etc/nginx/sites-available/magnet`：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        auth_basic "restricted";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/magnet /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# 建议再用 certbot 上 HTTPS：sudo certbot --nginx -d your-domain.com
```

---

## 三、备选：不用 Docker，用 systemd 直接跑

```bash
cd /opt/magnet-probe
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 另需一个 MongoDB（apt 装或单独 docker 跑）
```

`/etc/systemd/system/magnet-crawler.service`：

```ini
[Unit]
Description=Magnet Probe Crawler
After=network.target mongod.service

[Service]
WorkingDirectory=/opt/magnet-probe
Environment=MP_MONGO_URI=mongodb://127.0.0.1:27017
Environment=MP_DHT_PORTS=6881,6882,6883,6884
Environment=MP_WORKERS=300
ExecStart=/opt/magnet-probe/.venv/bin/python main.py crawl
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/magnet-web.service`：把 `ExecStart` 改成 `... main.py web`、加 `Environment=MP_WEB_HOST=127.0.0.1`。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now magnet-crawler magnet-web
sudo systemctl status magnet-crawler
```

---

## 四、调优（公网环境）

通过环境变量（前缀 `MP_`）按 VPS 配置调整，详见 `config.py`：

| 变量 | 建议值 | 说明 |
|------|--------|------|
| `MP_DHT_PORTS` | `6881,6882,6883,6884` | 多端口 = 多 node_id，更大曝光，更多 announce |
| `MP_WORKERS` | `200`~`500` | metadata 并发 worker（按内存/带宽调） |
| `MP_PEER_CONCURRENCY` | `8` | 每个 hash 并行尝试的 peer 数 |
| `MP_MAX_PER_SECOND` | `1500` | 每个 DHT 端口每秒主动外发上限 |
| `MP_QUEUE_MAX` | `5000` | 有界队列（背压，防内存爆） |

运行一段时间后验证入库：

```bash
docker exec -it magnet-probe-mongo-1 mongosh magnet_probe --eval 'db.torrents.countDocuments()'
```

---

## 五、腾讯云具体步骤（CVM 或 轻量应用服务器）

> 强烈建议选**境外地域**（如新加坡、硅谷、首尔）：① 国内服务器对外开放 80/443 需 ICP 备案，境外免备案；② 部分国内网络对 P2P/DHT 流量有干扰。下面以 **Ubuntu 22.04** 镜像为例。

### 6.1 买机器

**方案 A：轻量应用服务器 Lighthouse（最简单，自带防火墙 UI）**

1. 控制台 → 搜索「轻量应用服务器」→ **新建**。
2. 地域：选**境外**（如「新加坡」）；镜像：**系统镜像 → Ubuntu 22.04**（或「应用镜像 → Docker」可省装 Docker）。
3. 套餐：DHT 吃带宽，建议选**带宽 ≥ 5Mbps**、内存 ≥ 2GB 的套餐。
4. 设置 root/ubuntu 密码 → 购买。

**方案 B：云服务器 CVM**

1. 控制台 → 「云服务器 CVM」→ **新建** → 选境外地域、Ubuntu 22.04、按量计费或包月。
2. 公网：勾选「分配免费公网 IP」，带宽按需。
3. 记下实例所属的**安全组**（下一步要改）。

### 6.2 放行端口（关键：DHT 入站 UDP）

**Lighthouse：** 实例详情页 → **「防火墙」** 标签 → **添加规则**：

| 应用类型 | 协议 | 端口 | 来源 | 说明 |
|---------|------|------|------|------|
| 自定义 | **UDP** | **6881-6884** | `0.0.0.0/0` | DHT，收 announce_peer 的关键 |
| HTTP | TCP | 80 | `0.0.0.0/0` | Web 反代（可选） |
| HTTPS | TCP | 443 | `0.0.0.0/0` | Web HTTPS（可选） |
| SSH | TCP | 22 | **你的 IP** | 登录，建议限来源 |

**CVM：** 控制台 → 「云服务器」→ 左侧「安全组」→ 找到实例绑定的安全组 → **入站规则 → 添加规则**，加同样几条（类型选「自定义」，协议端口填 `UDP:6881-6884`、`TCP:80`、`TCP:443`、`TCP:22`）。

> ⚠️ **不要**放行 `27017`（Mongo）和 `8080`（Web 原始端口）——它们只走本机/反代。

### 6.3 登录并装 Docker

控制台点「登录」用 OrcaTerm 网页终端，或本地 `ssh ubuntu@你的公网IP`：

```bash
# 装 Docker + compose 插件
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exec sudo su - $USER   # 免 sudo
docker version && docker compose version                 # 确认可用
```

> 国内地域若拉镜像慢，可在 `/etc/docker/daemon.json` 配腾讯云镜像加速器
> `{"registry-mirrors":["https://mirror.ccs.tencentyun.com"]}` 后 `sudo systemctl restart docker`。

### 6.4 上传代码并启动

```bash
# 本地把项目传上去（在你的 Mac 上执行）
scp -r magnet-probe ubuntu@你的公网IP:/home/ubuntu/

# 回到服务器
cd ~/magnet-probe
docker compose up -d --build

# 看日志：found= 持续增长，几分钟内应出现 "saved ..." 行
docker compose logs -f crawler
```

验证入库数量：

```bash
docker exec -it magnet-probe-mongo-1 mongosh magnet_probe --eval 'db.torrents.countDocuments()'
```

### 6.5 对外访问 Web（域名 + HTTPS，可选）

1. **解析域名**：腾讯云「DNSPod / 云解析 DNS」→ 给你的域名加一条 **A 记录**指向服务器公网 IP。
2. 按本文「二、nginx 反代 + 鉴权」配好 nginx（加 Basic Auth），再上 HTTPS：

```bash
sudo apt install -y nginx apache2-utils certbot python3-certbot-nginx
sudo htpasswd -c /etc/nginx/.htpasswd admin
# 写好 /etc/nginx/sites-available/magnet（见上文）后：
sudo certbot --nginx -d your-domain.com
```

> 若用国内地域且要对外开 80/443，需先完成 **ICP 备案**；境外地域无此要求。

### 6.6 关机省钱 / 维护

```bash
docker compose down       # 停止（保留数据卷）
docker compose up -d      # 再次启动
docker compose pull && docker compose up -d --build   # 更新代码后重建
```

---

## 六、合规

仅索引公开 DHT 网络的**元信息**（名称/文件列表/大小），不下载实际文件内容。
请确保部署地与使用方式符合当地法律法规，对外服务务必加访问控制，风险自负。
