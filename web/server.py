"""搜索 Web UI / API（FastAPI）。"""
import os
from urllib.parse import quote

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

import config
from storage.db import Store

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "index.html")


def magnet(infohash: str, name: str) -> str:
    link = "magnet:?xt=urn:btih:" + infohash
    if name:
        link += "&dn=" + quote(name)
    return link


def create_app() -> FastAPI:
    app = FastAPI(title="Magnet Probe")
    store = Store(config.MONGO_URI, config.MONGO_DB, config.CACHE_TTL, config.CACHE_SIZE)

    @app.on_event("startup")
    async def _startup():
        await store.ensure_indexes()

    @app.on_event("shutdown")
    async def _shutdown():
        store.close()

    @app.get("/", response_class=HTMLResponse)
    async def index():
        with open(_TEMPLATE, encoding="utf-8") as f:
            return f.read()

    @app.get("/api/search")
    async def search(
        q: str = Query("", description="关键词，支持中英文模糊"),
        sort: str = Query("relevance"),
        order: str = Query("desc"),
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
    ):
        result = await store.search(q, sort=sort, order=order, page=page, size=size)
        for it in result["items"]:
            it["magnet"] = magnet(it["infohash"], it["name"])
        return JSONResponse(result)

    @app.get("/api/stats")
    async def stats():
        return JSONResponse(await store.stats())

    return app


app = create_app()
