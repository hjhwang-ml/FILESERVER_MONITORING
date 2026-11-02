"""설정값 — 실제 값은 /opt/filehub/.env 에서 systemd 가 환경변수로 넣어준다."""
import os

DB = dict(
    host=os.environ.get("DB_HOST", "192.168.56.103"),
    port=int(os.environ.get("DB_PORT", "3306")),
    user=os.environ.get("DB_USER", "filehub_app"),
    password=os.environ.get("DB_PASS", ""),
    database=os.environ.get("DB_NAME", "filehub"),
)

# 세션 쿠키 서명 키 — 워커 3개가 같은 값을 써야 하므로 반드시 .env 에 고정
SECRET_KEY = os.environ.get("SECRET_KEY", "")


STORAGES = {
    "nfs": {
        "label": "",
        "desc": "",
        "path": os.environ.get("NFS_DIR", "/mnt/nfs/uploads"),
    },
    "iscsi": {
        "label": "iSCSI",
        "desc": "파일서버 sdc · 블록 디스크",
        "path": os.environ.get("ISCSI_DIR", "/mnt/iscsi/uploads"),
    },
}
DEFAULT_STORAGE = "nfs"

# 업로드 최대 크기(MB) — Nginx client_max_body_size 와 맞출 것
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))

# 연속 로그인 실패 몇 회에 잠글지
LOCK_THRESHOLD = int(os.environ.get("LOCK_THRESHOLD", "5"))

# SSL 적용 후 1 로 바꾸면 쿠키가 HTTPS 에서만 전송됨
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
