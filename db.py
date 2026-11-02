"""MariaDB 연결 — 요청마다 1개 연결을 열고, 요청이 끝나면 닫는다."""
import pymysql
from flask import g

import config


def get_db():
    if "db" not in g:
        g.db = pymysql.connect(
            **config.DB,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=5,
        )
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def query(sql, args=None, one=False):
    """SELECT — 행 목록(dict) 반환, one=True 면 첫 행 또는 None"""
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=None):
    """INSERT/UPDATE — 영향받은 행 수, INSERT 면 lastrowid 도 같이 반환"""
    with get_db().cursor() as cur:
        affected = cur.execute(sql, args)
        return affected, cur.lastrowid
