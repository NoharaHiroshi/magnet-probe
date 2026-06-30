# Magnet Probe · 磁力探测工具

参考 [btdig/dhtcrawler2](https://github.com/btdig/dhtcrawler2)，用 Python asyncio 复刻的一套
BitTorrent DHT 磁力探测工具。完整链路：

1. **DHT 嗅探**：加入 DHT 网络，被动收集别人 `get_peers` / `announce_peer` 查询中的 `info_hash`；
2. **Metadata 下载**：通过 BEP-9 (ut_metadata) + BEP-10 (扩展协议) 从 peer 下载并 SHA1 校验 `.torrent` 元数据；
3. **存储去重**：写入 MongoDB，`info_hash` 唯一去重，记录名称/文件列表/大小/热度；
4. **搜索 Web**：关键词模糊搜索（中英文）、多维排序，一键生成 `magnet:` 链接。

## 特性

- **高探测效率**：多 UDP 端口 + 多 node_id 扩大曝光；邻居 id 水平扩散；进程内 LRU 提前去重；
  announce 源走高优先级队列；每 info_hash **并行多 peer 拉取**（首个成功即取消其余）；可选 uvloop。
- **高查询效率**：`keywords`/`name`/`hot`/`length`/`created_at` 多索引；投影 + 分页；进程内 TTL 查询缓存。
- **模糊查询**：拉丁按词、**中文用 bi-gram 二元分词**，支持子串/前缀/中文模糊匹配。
- **排序**：相关度（热度代理）/ 热度 / 大小 / 时间。

## 安装

```bash
cd magnet-probe
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

需要一个 MongoDB（默认 `mongodb://localhost:27017`）。例如 Docker：

```bash
docker run -d --name mongo -p 27017:27017 mongo:7
```

## 运行

```bash
# 1) 启动爬虫（持续嗅探并写库，需公网 UDP）
python main.py crawl

# 2) 另开一个终端启动搜索服务
python main.py web
# 浏览器打开 http://127.0.0.1:8080
```

## 配置（环境变量，前缀 MP_）

| 变量 | 默认 | 说明 |
|------|------|------|
| `MP_DHT_PORTS` | `6881,6882` | DHT 监听端口（逗号分隔，多端口提速） |
| `MP_WORKERS` | `50` | metadata worker 数 |
| `MP_PEER_CONCURRENCY` | `4` | 每 info_hash 并行尝试 peer 数 |
| `MP_MONGO_URI` | `mongodb://localhost:27017` | MongoDB 地址 |
| `MP_MONGO_DB` | `magnet_probe` | 数据库名 |
| `MP_WEB_PORT` | `8080` | Web 端口 |

完整项见 `config.py`。

## 测试

```bash
python -m pytest tests/ -q      # 或 python tests/test_basic.py
```

## API

- `GET /api/search?q=&sort=relevance|hot|size|date&order=desc|asc&page=&size=`
- `GET /api/stats`

## 公网部署（重要）

DHT 爬虫**必须跑在公网 IP 的机器上**才能真正大量抓到元数据：家庭/NAT 网络收不到 `announce_peer`、
且连不上多数 peer，入库率几乎为 0。云 VPS 上一键部署见 **[DEPLOY.md](DEPLOY.md)**：

```bash
docker compose up -d --build      # mongo + crawler + web 一起起
sudo ufw allow 6881:6884/udp      # 放行 DHT 入站 UDP（收 announce_peer 的关键）
```

## 合规声明

本工具仅索引公开 DHT 网络中的**元信息**（名称/文件列表/大小），不下载任何实际文件内容。
请将其用于学术研究、网络测量等合法用途，并遵守你所在国家/地区的法律法规，
不得用于侵犯版权或其它违法行为。使用风险自负。
