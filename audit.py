"""감사 기록 — DB(audit_logs)와 앱 로그(journald → Alloy → Loki) 두 곳에 같이 남긴다.

DB 기록이 실패해도 서비스는 멈추지 않고, 로그에는 반드시 남는다.
"""
import json
import logging

from flask import request, session

from db import execute

log = logging.getLogger("filehub.audit")

_FROM_SESSION = object()


def audit(action, result, *, username=None, user_id=_FROM_SESSION,
          file_id=None, file_name=None, detail=None):
    """action: LOGIN / LOGOUT / ACCOUNT_LOCKED / UPLOAD / DOWNLOAD / UPDATE / DELETE
    result: SUCCESS / FAIL"""
    if user_id is _FROM_SESSION:
        user_id = session.get("user_id")
    username = username or session.get("username") or "-"
    ip = request.remote_addr or "-"          # ProxyFix 덕분에 실제 클라이언트 IP

    event = {
        "event": "audit",
        "action": action,
        "result": result,
        "user": username,
        "user_id": user_id,
        "ip": ip,
        "file_id": file_id,
        "file": file_name,
        "detail": detail,
    }
    log.info(json.dumps(event, ensure_ascii=False))

    try:
        execute(
            "INSERT INTO audit_logs "
            "(user_id, username, client_ip, action, result, file_id, file_name, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, username[:50], ip, action, result, file_id,
             (file_name or None) and file_name[:255],
             (detail or None) and detail[:255]),
        )
    except Exception:
        log.exception("audit_logs INSERT 실패")
