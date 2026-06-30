FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 默认跑爬虫；compose 里会按服务覆盖 command
CMD ["python", "main.py", "crawl"]
