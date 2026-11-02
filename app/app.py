"""filehub — 사내 공유 드라이브 웹 앱 (Flask)

실행: Gunicorn 이 app:app 을 불러서 실행 (systemd filehub.service)
흐름: 브라우저 → Nginx :80 → Gunicorn 127.0.0.1:8000 → 이 파일
저장: 파일 본문은 파일서버(NFS), 메타데이터·감사기록은 MariaDB
"""
import errno
import re
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
import logging
import os
import secrets
import shutil
import time
import uuid
import json
from datetime import timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import config
from db import close_db, execute, query

# 트레이스: 업로드 단계별 구간(span) — OpenTelemetry 가 없으면 아무것도 안 하는 가짜로 대체
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    tracer = trace.get_tracer("filehub")
except ImportError:
    import contextlib
    trace = None

    class _NoSpan:
        def set_attribute(self, *a, **k): pass
        def set_status(self, *a, **k): pass

    class _NoTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, name):
            yield _NoSpan()
    tracer = _NoTracer()


def current_trace_id():
    """지금 요청의 trace_id (감사 로그 → Tempo 연결용) — 없으면 None"""
    if trace is None:
        return None
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.trace_id else None

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

log = logging.getLogger("filehub")

audit_log = logging.getLogger("filehub.audit")
def log_audit(action, result, user=None, file=None, detail=""):
    """관제(Loki)가 수집하는 감사 이벤트 한 줄 — DB 에는 남기지 않는다"""
    cur = g.get("user")
    if user is None and cur:
        user = cur["username"]
    email = None
    if cur and cur["username"] == user:
        email = cur.get("email")
    elif user:
        # 로그인 실패·로그아웃처럼 g.user 가 없을 때는 아이디로 찾아본다 (없는 아이디면 None)
        try:
            row = query("SELECT email FROM users WHERE username = %s", (user,), one=True)
            email = row["email"] if row else None
        except Exception:
            pass
    audit_log.info(json.dumps({
        "action": action, "result": result, "user": user, "email": email,
        "ip": request.remote_addr, "file": file, "detail": detail,
        "trace_id": current_trace_id(),
    }, ensure_ascii=False))

if not config.SECRET_KEY:
    # 워커 3개가 서로 다른 키를 쓰면 로그인이 랜덤하게 풀리므로 아예 시작을 막는다
    raise RuntimeError("SECRET_KEY 가 비어 있습니다. /opt/filehub/.env 에 설정하세요.")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
# Nginx 가 붙여준 X-Forwarded-* 헤더를 읽어 실제 클라이언트 IP·프로토콜을 복원
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.teardown_appcontext(close_db)

IDLE_TIMEOUT = 30 * 60  
MAX_SESSION = 8 * 60 * 60

ROLE_LABELS = {"admin": "관리자", "staff": "직원"}

# 팀 (시연용) — 키는 DB(users.team · folders.dept · files.dept)에 들어가는 값, 값은 화면 이름
TEAMS = {"team1": "1팀", "team2": "2팀", "team4": "4팀", "team5": "5팀", "team6": "6팀"}
# 맨 위 폴더 태그 = 공용 + 팀
DEPARTMENTS = {"general": "공용", **TEAMS}

# 저장소는 NFS 하나 — config.STORAGES 에 다른 항목이 남아 있어도 앱은 쓰지 않는다
ACTIVE_STORAGE = "nfs"
STORAGES = {ACTIVE_STORAGE: config.STORAGES[ACTIVE_STORAGE]}

# 정렬: 화면 값 → (폴더 정렬 칸, 파일 정렬 칸, 기본 방향) — 사용자 값은 SQL 에 직접 넣지 않는다
SORTS = {
    "name": ("d.name", "f.original_name", "asc"),
    "who": ("d.name", "u.name", "asc"),
    "new": ("d.created_at", "f.updated_at", "desc"),
    "size": ("d.name", "f.size_bytes", "desc"),
}
SORT_LABELS = {"name": "이름순", "new": "최신순", "size": "크기순", "who": "올린 사람순"}

# 확장자 → 아이콘 글자 · 색 갈래
FILE_TYPES = {
    "pdf": ("PDF", "pdf"),
    "ppt": ("PPT", "ppt"), "pptx": ("PPTX", "ppt"),
    "xls": ("XLS", "xls"), "xlsx": ("XLSX", "xls"), "csv": ("CSV", "xls"),
    "doc": ("DOC", "doc"), "docx": ("DOCX", "doc"),
    "hwp": ("HWP", "hwp"), "hwpx": ("HWPX", "hwp"),
    "zip": ("ZIP", "zip"), "7z": ("7Z", "zip"), "gz": ("GZ", "zip"), "tar": ("TAR", "zip"),
    "png": ("PNG", "img"), "jpg": ("JPG", "img"), "jpeg": ("JPG", "img"), "gif": ("GIF", "img"),
    "txt": ("TXT", "txt"), "log": ("LOG", "txt"),
}


# ---------------------------------------------------------------------------
# 공통: CSRF, 로그인 확인, 권한
# ---------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_csrf():
    """POST 요청은 폼에 숨겨둔 토큰이 세션 값과 같아야 통과 (다른 사이트에서 위조한 요청 차단)"""
    if request.method != "POST":
        return
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
    if not sent or sent != session.get("csrf"):
        if request.endpoint == "login":
            flash("화면이 오래되어 다시 시도해야 합니다.", "error")
            return redirect(url_for("index"))
        abort(400, "잘못된 요청입니다 (CSRF 토큰 불일치). 페이지를 새로고침하세요.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        if not uid:
            if wants_json():
                return jsonify(ok=False, message="로그인이 필요합니다."), 401
            return redirect(url_for("index"))

        # 유휴 시간 · 최대 세션 시간 확인
        now = time.time()
        idle = now - session.get("last_seen", 0)
        age = now - session.get("login_at", 0)
        if idle > IDLE_TIMEOUT or age > MAX_SESSION:
            reason = "오래 사용하지 않아" if idle > IDLE_TIMEOUT else "로그인 후 시간이 오래 지나"
            log_audit("LOGOUT", "SUCCESS", user=session.get("username"), detail=f"세션 만료 ({reason})")
            session.clear()
            if wants_json():
                return jsonify(ok=False, message="세션이 만료되었습니다. 다시 로그인하세요."), 401
            flash(f"{reason} 자동 로그아웃되었습니다. 다시 로그인하세요.", "info")
            return redirect(url_for("index"))
        session["last_seen"] = now

        # 관리자가 계정을 잠그면 바로 반영되도록 매 요청 확인
        user = query("SELECT id, username, name, email, role, team, is_locked FROM users WHERE id = %s",
                     (uid,), one=True)
        if not user or user["is_locked"]:
            session.clear()
            flash("세션이 만료되었거나 계정이 잠겼습니다. 다시 로그인하세요.", "error")
            return redirect(url_for("index"))
        g.user = user
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def can_modify(f):
    """교체·삭제 권한: 관리자 전체, 직원은 본인이 올린 파일만"""
    user = g.get("user")
    if not user:
        return False
    if user["role"] == "admin":
        return True
    return f["uploaded_by"] == user["id"]


# ---------------------------------------------------------------------------
# 팀 접근 제한
#   관리자 = 전부 · 직원 = 공용 + 자기 팀 · 팀이 없는 직원 = 공용만
#   폴더·파일의 팀은 dept 칸으로 판단한다 (하위 폴더·파일은 부모 팀을 물려받는다).
#   dept 가 비어 있으면 공용(general)으로 본다.
# ---------------------------------------------------------------------------
def allowed_depts():
    """볼 수 있는 dept 목록 — 관리자는 None (제한 없음)"""
    user = g.get("user")
    if user and user["role"] == "admin":
        return None
    depts = ["general"]
    if user and user.get("team") in TEAMS:
        depts.append(user["team"])
    return depts


def can_see(dept):
    allowed = allowed_depts()
    return allowed is None or (dept or "general") in allowed


def dept_filter(alias):
    """SQL 조건 조각 + 인자: 관리자면 빈 조건"""
    allowed = allowed_depts()
    if allowed is None:
        return "", []
    marks = ", ".join(["%s"] * len(allowed))
    return f" AND ({alias}.dept IS NULL OR {alias}.dept IN ({marks}))", list(allowed)


def deny_other_team(dept, action, **audit_kw):
    log_audit(action, "FAIL", file=audit_kw.get("file_name"), detail=f"다른 팀 접근 ({dept})")
    abort(403, "다른 팀 폴더에는 접근할 수 없습니다.")


# ---------------------------------------------------------------------------
# 템플릿 도우미
# ---------------------------------------------------------------------------
@app.template_filter("filesize")
def filesize(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


@app.template_filter("dt")
def dt(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


@app.template_filter("md")
def md(value):
    """격자 카드용 짧은 날짜 — 10.20"""
    return value.strftime("%m.%d").lstrip("0") if value else ""


@app.template_filter("ftype")
def ftype(name):
    """파일 이름 → (아이콘 글자, 색 갈래)"""
    ext = name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    return FILE_TYPES.get(ext, ("FILE", "etc"))


def can_manage_folders():
    """새 폴더 · 폴더 지우기: DB 에 folders 표가 있을 때만, 관리자 · 직원"""
    return (g.get("user") is not None and g.user["role"] in ("admin", "staff")
            and folders_enabled())


@app.context_processor
def inject_globals():
    data = dict(csrf_token=csrf_token, can_modify=can_modify,
                ROLE_LABELS=ROLE_LABELS, STORAGES=STORAGES, DEPARTMENTS=DEPARTMENTS, TEAMS=TEAMS)
    if g.get("user"):
        # 왼쪽 폴더 트리 · 저장 공간 카드는 로그인한 모든 화면에 나온다
        data.update(folder_tree=folder_tree(), drive_storage=active_storage_status(),
                    can_manage_folders=can_manage_folders())
    return data


# ---------------------------------------------------------------------------
# 폴더 (DB folders 표)
#   folders 표가 아직 없으면 부서(DEPARTMENTS)를 「부서 폴더」로 보여 준다.
#   부서 폴더 id 는 음수 · 파일은 files.dept 로 고른다 · 새 폴더는 숨긴다.
# ---------------------------------------------------------------------------
_folders_cache = {"at": 0.0, "ok": False}


def folders_enabled():
    now = time.time()
    if now - _folders_cache["at"] > 60:
        try:
            query("SELECT 1 FROM folders LIMIT 1")
            ok = True
        except Exception:
            ok = False
        _folders_cache.update(at=now, ok=ok)
    return _folders_cache["ok"]


def dept_folders():
    return [dict(id=-(i + 1), name=label, parent_id=None, dept=key, created_at=None, children=[])
            for i, (key, label) in enumerate(DEPARTMENTS.items())]


def folder_tree():
    """왼쪽 트리용: [{id, name, children: [...]}] — 볼 수 있는 팀 폴더만"""
    if not folders_enabled():
        return sorted([d for d in dept_folders() if can_see(d["dept"])], key=lambda d: d["name"])
    cond, args = dept_filter("d")
    rows = query(f"SELECT d.id, d.name, d.parent_id FROM folders d WHERE 1=1{cond} ORDER BY d.name", args)
    nodes = {r["id"]: dict(r, children=[]) for r in rows}
    roots = []
    for n in nodes.values():
        parent = nodes.get(n["parent_id"])
        (parent["children"] if parent else roots).append(n)
    return roots


def get_folder_or_404(folder_id, action="ACCESS"):
    if not folders_enabled():
        folder = next((d for d in dept_folders() if d["id"] == folder_id), None)
    else:
        folder = query("SELECT * FROM folders WHERE id = %s", (folder_id,), one=True)
    if not folder:
        abort(404, "폴더를 찾을 수 없습니다.")
    if not can_see(folder["dept"]):
        deny_other_team(folder["dept"], action, file_name=f"[폴더] {folder['name']}")
    return folder


def folder_trail(folder):
    """빵부스러기: 맨 위 → 지금 폴더 (깊이 20 에서 끊는다 — 잘못된 순환 대비)"""
    trail = []
    while folder and len(trail) < 20:
        trail.append(folder)
        folder = (query("SELECT * FROM folders WHERE id = %s", (folder["parent_id"],), one=True)
                  if folder["parent_id"] else None)
    return list(reversed(trail))


# ---------------------------------------------------------------------------
# 저장소 (NFS)
# ---------------------------------------------------------------------------
def active_storage_status():
    """왼쪽 아래 「저장 공간」 카드: 지금 쓰는 저장소 하나"""
    found = storage_status(only=ACTIVE_STORAGE)
    return found[0] if found else None


def storage_path(storage, stored_name):
    if storage not in STORAGES:
        # 지금은 쓰지 않는 저장소에 있던 파일
        abort(404, "이 파일이 있던 저장소는 더 이상 쓰지 않습니다.")
    return os.path.join(STORAGES[storage]["path"], stored_name)


def storage_status(only=None):
    """저장소별 사용량 (only 를 주면 그 저장소 하나만)"""
    result = []
    for key, s in STORAGES.items():
        if only and key != only:
            continue
        info = {"key": key, "label": s["label"], "desc": s["desc"], "path": s["path"], "ok": False}
        try:
            du = shutil.disk_usage(s["path"])
            info.update(ok=os.access(s["path"], os.W_OK), total=du.total, used=du.used,
                        free=du.free, pct=round(du.used / du.total * 100, 1) if du.total else 0)
        except OSError as e:
            info["error"] = e.strerror or str(e)
        result.append(info)
    return result


def save_upload(fs, final_path):
    """임시 이름(.part)으로 다 쓴 다음 한 번에 이름을 바꾼다.
    업로드가 중간에 끊겨도 반쪽짜리 파일이 정상 파일처럼 보이지 않게 하기 위함."""
    tmp_path = os.path.join(os.path.dirname(final_path),
                            f".{os.path.basename(final_path)}.{uuid.uuid4().hex[:8]}.part")
    try:
        fs.save(tmp_path)
        os.replace(tmp_path, final_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return os.path.getsize(final_path)


def wants_json():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def get_file_or_404(file_id, action="ACCESS"):
    f = query("SELECT * FROM files WHERE id = %s AND is_deleted = 0", (file_id,), one=True)
    if not f:
        abort(404)
    if not can_see(f["dept"]):
        deny_other_team(f["dept"], action, file_id=f["id"], file_name=f["original_name"])
    return f


def folder_quota(folder_id):
    """이 폴더에 걸린 한도(가장 가까운 상위 폴더의 quota_mb)와 그 아래 전체 사용량 — 한도가 없으면 None"""
    if not folder_id or not folders_enabled():
        return None
    row = query("""
        WITH RECURSIVE up AS (
          SELECT id, parent_id, name, quota_mb, 0 AS depth FROM folders WHERE id = %s
          UNION ALL
          SELECT p.id, p.parent_id, p.name, p.quota_mb, up.depth + 1
          FROM folders p JOIN up ON p.id = up.parent_id WHERE up.depth < 20
        )
        SELECT id, name, quota_mb FROM up WHERE quota_mb IS NOT NULL ORDER BY depth LIMIT 1
    """, (folder_id,), one=True)
    if not row:
        return None
    used = query("""
        WITH RECURSIVE down AS (
          SELECT id FROM folders WHERE id = %s
          UNION ALL
          SELECT c.id FROM folders c JOIN down d ON c.parent_id = d.id
        )
        SELECT COALESCE(SUM(f.size_bytes), 0) AS used
        FROM files f JOIN down d ON f.folder_id = d.id WHERE f.is_deleted = 0
    """, (row["id"],), one=True)["used"]
    return dict(row, used=int(used), limit=row["quota_mb"] * 1024 * 1024)


# ---------------------------------------------------------------------------
# 로그인 / 로그아웃 / 회원가입
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("files"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    fail_msg = "아이디 또는 비밀번호가 올바르지 않습니다."

    if not username or not password:
        flash("아이디와 비밀번호를 입력하세요.", "error")
        return redirect(url_for("index"))

    user = query("SELECT * FROM users WHERE username = %s", (username,), one=True)

    # 1) 없는 아이디
    if not user:
        log_audit("LOGIN", "FAIL", user=username, detail="없는 아이디")
        flash(fail_msg, "error")
        return redirect(url_for("index"))

    # 2) 잠긴 계정 (가입 승인 대기 포함)
    if user["is_locked"]:
        log_audit("LOGIN", "FAIL", user=username, detail="잠긴 계정")
        flash("계정이 잠겨 있거나 가입 승인 대기 중입니다. 관리자에게 문의하세요.", "error")
        return redirect(url_for("index"))

    # 3) 비밀번호 틀림 → 실패 횟수 증가, 기준 도달 시 잠금
    if not check_password_hash(user["password_hash"], password):
        count = user["failed_count"] + 1
        locked = count >= config.LOCK_THRESHOLD
        execute("UPDATE users SET failed_count = %s, is_locked = %s WHERE id = %s",
                (count, 1 if locked else 0, user["id"]))
        log_audit("LOGIN", "FAIL", user=username, detail=f"비밀번호 오류 {count}회")
        if locked:
            log_audit("ACCOUNT_LOCKED", "FAIL", user=username, detail=f"{count}회 실패로 잠금")
            flash("로그인 실패가 반복되어 계정이 잠겼습니다. 관리자에게 문의하세요.", "error")
        else:
            flash(fail_msg, "error")
        return redirect(url_for("index"))

    # 4) 성공
    execute("UPDATE users SET failed_count = 0, last_login_at = NOW() WHERE id = %s", (user["id"],))
    session.clear()
    session.permanent = False          # 브라우저를 닫으면 쿠키 삭제
    session["login_at"] = time.time()
    session["last_seen"] = time.time()
    
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    log_audit("LOGIN", "SUCCESS", user=username)
    return redirect(url_for("files"))


@app.route("/logout", methods=["POST"])
def logout():
    if session.get("user_id"):
        log_audit("LOGOUT", "SUCCESS", user=session.get("username"))
    session.clear()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("index"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """회원가입 — 소속팀을 고르고 신청 · 관리자가 「승인」(잠금 해제)해야 로그인된다"""
    if session.get("user_id"):
        return redirect(url_for("files"))
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        username = (form.get("username") or "").strip()
        name = (form.get("name") or "").strip()
        email = (form.get("email") or "").strip().lower()
        team = form.get("team") or ""
        password = form.get("password") or ""
        error = None
        if not username or not name or not email or team not in TEAMS:
            error = "아이디, 이름, 이메일, 소속팀을 모두 입력하세요."
        elif len(email) > 255 or not EMAIL_RE.fullmatch(email):
            error = "이메일 형식이 올바르지 않습니다."
        elif not username.replace("_", "").replace(".", "").isalnum() or len(username) > 50:
            error = "아이디는 영문·숫자·_ . 만 쓸 수 있습니다 (50자 이내)."
        elif len(password) < 8:
            error = "비밀번호는 8자 이상이어야 합니다."
        elif password != form.get("password2"):
            error = "비밀번호 두 번 입력한 값이 다릅니다."
        elif query("SELECT id FROM users WHERE username = %s", (username,), one=True):
            error = f"'{username}' 은(는) 이미 있는 아이디입니다."
        elif query("SELECT id FROM users WHERE email = %s", (email,), one=True):
            error = "이미 가입된 이메일입니다."
        if error:
            flash(error, "error")
            return render_template("signup.html", form=form), 400
        execute("INSERT INTO users (username, password_hash, name, email, role, team, is_locked) "
                "VALUES (%s, %s, %s, %s, 'staff', %s, 1)",
                (username, generate_password_hash(password), name[:50], email, team))
        log_audit("SIGNUP", "SUCCESS", user=username, detail=TEAMS[team])
        flash("가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.", "success")
        return redirect(url_for("index"))
    return render_template("signup.html", form=form)


# ---------------------------------------------------------------------------
# 파일 목록 / 업로드 / 다운로드 / 교체 / 삭제
# ---------------------------------------------------------------------------
# 업로드(/upload) 는 파일 맨 아래에 있다
@app.route("/files")
@login_required
def files():
    """공유 드라이브: ?folder=<id> 그 폴더 안 / 없으면 맨 위 / ?q= 검색 / ?sort=name|who|new|size&dir=asc|desc"""
    folder_id = request.args.get("folder", type=int)
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "name")
    if sort not in SORTS:
        sort = "name"
    folder_col, file_col, default_dir = SORTS[sort]
    direction = request.args.get("dir", default_dir)
    if direction not in ("asc", "desc"):
        direction = default_dir
    # 폴더는 이름·날짜로만 정렬 (크기·올린 사람이 없으므로 이름순, 방향은 따른다)
    folder_order = f"{folder_col} {direction.upper()}"
    file_order = f"{file_col} {direction.upper()}, f.original_name"

    folder = get_folder_or_404(folder_id) if folder_id else None
    trail = folder_trail(folder) if folder else []

    count_sql = ("(SELECT COUNT(*) FROM folders c WHERE c.parent_id = d.id) + "
                 "(SELECT COUNT(*) FROM files x WHERE x.folder_id = d.id AND x.is_deleted = 0)")
    file_cond, file_args = dept_filter("f")
    dir_cond, dir_args = dept_filter("d")
    file_sql = ("SELECT f.*, u.name AS uploader_name, u.username AS uploader_username "
                "FROM files f JOIN users u ON u.id = f.uploaded_by WHERE f.is_deleted = 0" + file_cond)

    if not folders_enabled():
        # folders 표가 없을 때: 맨 위 = 부서 폴더 · 부서 폴더 안 = 그 부서(dept) 파일
        depts = list(DEPARTMENTS)
        counts = {r["dept"]: r["n"] for r in query(
            "SELECT dept, COUNT(*) AS n FROM files WHERE is_deleted = 0 GROUP BY dept")}
        all_depts = [dict(d, item_count=counts.get(d["dept"], 0)) for d in dept_folders() if can_see(d["dept"])]
        if q:
            subfolders = [d for d in all_depts if q in d["name"]]
            file_rows = query(f"{file_sql} AND f.original_name LIKE %s ORDER BY {file_order} LIMIT 500",
                              file_args + [f"%{q}%"])
        elif folder:
            subfolders = []
            file_rows = query(f"{file_sql} AND f.dept = %s ORDER BY {file_order} LIMIT 500",
                              file_args + [folder["dept"]])
        else:
            subfolders = sorted(all_depts, key=lambda d: d["name"], reverse=(direction == "desc" and sort == "name"))
            marks = ", ".join(["%s"] * len(depts))
            file_rows = query(f"{file_sql} AND (f.dept IS NULL OR f.dept NOT IN ({marks})) "
                              f"ORDER BY {file_order} LIMIT 500", file_args + depts)
    elif q:
        # 검색은 볼 수 있는 폴더 안에서만 이름으로 찾는다
        like = f"%{q}%"
        subfolders = query(f"SELECT d.*, {count_sql} AS item_count FROM folders d "
                           f"WHERE d.name LIKE %s{dir_cond} ORDER BY {folder_order} LIMIT 200",
                           [like] + dir_args)
        file_rows = query(f"{file_sql} AND f.original_name LIKE %s ORDER BY {file_order} LIMIT 500",
                          file_args + [like])
    else:
        # <=> 는 NULL 끼리도 같다고 보는 비교 (맨 위 = parent_id / folder_id 가 NULL)
        subfolders = query(f"SELECT d.*, {count_sql} AS item_count FROM folders d "
                           f"WHERE d.parent_id <=> %s{dir_cond} ORDER BY {folder_order}",
                           [folder_id] + dir_args)
        file_rows = query(f"{file_sql} AND f.folder_id <=> %s ORDER BY {file_order} LIMIT 500",
                          file_args + [folder_id])

    return render_template("files.html", folder=folder, trail=trail, subfolders=subfolders,
                           files=file_rows, q=q, sort=sort, direction=direction, SORT_LABELS=SORT_LABELS,
                           total_bytes=sum(f["size_bytes"] or 0 for f in file_rows))


@app.route("/folders", methods=["POST"])
@login_required
def create_folder():
    if not can_manage_folders():
        abort(403)
    name = " ".join((request.form.get("name") or "").split())[:100]
    parent_id = request.form.get("parent_id", type=int)
    parent = get_folder_or_404(parent_id, "FOLDER") if parent_id else None

    def done(ok, msg, status=200, folder_id=None):
        if wants_json():
            return jsonify(ok=ok, message=msg, folder_id=folder_id), status
        flash(msg, "success" if ok else "error")
        return redirect(url_for("files", folder=parent_id))

    if not name or name in (".", ".."):
        return done(False, "폴더 이름을 입력하세요.", 400)
    if query("SELECT id FROM folders WHERE name = %s AND parent_id <=> %s", (name, parent_id), one=True):
        return done(False, f"'{name}' 폴더가 이미 있습니다.", 409)

    _, new_id = execute("INSERT INTO folders (name, parent_id, dept, created_by) VALUES (%s, %s, %s, %s)",
                        (name, parent_id, (parent["dept"] or "general") if parent else "general", g.user["id"]))
    log_audit("FOLDER", "SUCCESS", file=name, detail="생성")
    return done(True, f"'{name}' 폴더를 만들었습니다.", folder_id=new_id)


@app.route("/folders/<int:folder_id>/delete", methods=["POST"])
@admin_required
def delete_folder(folder_id):
    if not folders_enabled():
        abort(404)
    folder = get_folder_or_404(folder_id)
    busy = query("SELECT (SELECT COUNT(*) FROM folders WHERE parent_id = %s) + "
                 "(SELECT COUNT(*) FROM files WHERE folder_id = %s AND is_deleted = 0) AS n",
                 (folder_id, folder_id), one=True)
    if busy["n"]:
        flash(f"'{folder['name']}' 안에 항목이 있어 지울 수 없습니다.", "error")
    else:
        execute("DELETE FROM folders WHERE id = %s", (folder_id,))
        log_audit("FOLDER", "SUCCESS", file=folder["name"], detail="삭제")
        flash(f"'{folder['name']}' 폴더를 지웠습니다.", "success")
    return redirect(url_for("files", folder=folder["parent_id"]))


@app.route("/download/<int:file_id>")
@login_required
def download(file_id):
    f = get_file_or_404(file_id, "DOWNLOAD")
    path = storage_path(f["storage"], f["stored_name"])
    if not os.path.isfile(path):
        abort(404, "저장소에서 파일을 찾을 수 없습니다.")
    log_audit("DOWNLOAD", "SUCCESS", file=f["original_name"])
    return send_file(path, as_attachment=True, download_name=f["original_name"])


@app.route("/files/<int:file_id>/replace", methods=["POST"])
@login_required
def replace(file_id):
    f = get_file_or_404(file_id, "UPDATE")
    if not can_modify(f):
        abort(403)

    fs = request.files.get("file")
    if not fs or not fs.filename:
        flash("교체할 파일을 선택하세요.", "error")
        return redirect(url_for("files", folder=f.get("folder_id")))

    path = storage_path(f["storage"], f["stored_name"])
    try:
        size = save_upload(fs, path)           # 같은 저장 이름에 덮어씀 (임시 파일 → 교체)
    except OSError as e:
        quota = e.errno in (errno.EDQUOT, errno.ENOSPC)
        log_audit("UPDATE", "FAIL", file=f["original_name"], detail="디스크 쿼터 초과" if quota else str(e))
        flash("팀 저장 공간(디스크 한도)이 가득 차 교체하지 못했습니다." if quota
              else "파일을 교체하지 못했습니다.", "error")
        return redirect(url_for("files", folder=f.get("folder_id")))

    execute("UPDATE files SET size_bytes = %s WHERE id = %s", (size, f["id"]))
    log_audit("UPDATE", "SUCCESS", file=f["original_name"], detail=f"{size} bytes")
    flash(f"'{f['original_name']}' 내용을 교체했습니다.", "success")
    return redirect(url_for("files", folder=f.get("folder_id")))


@app.route("/files/<int:file_id>/delete", methods=["POST"])
@login_required
def delete(file_id):
    f = get_file_or_404(file_id, "DELETE")
    if not can_modify(f):
        abort(403)

    # 소프트 삭제: 목록에서만 숨기고 파일과 기록은 남겨 둔다 (감사 추적용)
    execute("UPDATE files SET is_deleted = 1 WHERE id = %s", (f["id"],))
    log_audit("DELETE", "SUCCESS", file=f["original_name"])
    flash(f"'{f['original_name']}' 을(를) 삭제했습니다.", "success")
    return redirect(url_for("files", folder=f.get("folder_id")))


# ---------------------------------------------------------------------------
# 관리자: 사용자 관리, 감사 로그
# ---------------------------------------------------------------------------
@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role = request.form.get("role", "staff")
        team = request.form.get("team") or None
        password = request.form.get("password") or ""

        if not username or not name or not email or role not in ROLE_LABELS:
            flash("아이디, 이름, 이메일, 역할을 모두 입력하세요.", "error")
        elif len(email) > 255 or not EMAIL_RE.fullmatch(email):
            flash("이메일 형식이 올바르지 않습니다.", "error")
        elif team and team not in TEAMS:
            flash("소속팀을 다시 고르세요.", "error")
        elif len(password) < 8:
            flash("비밀번호는 8자 이상이어야 합니다.", "error")
        elif query("SELECT id FROM users WHERE username = %s", (username,), one=True):
            flash(f"'{username}' 은(는) 이미 있는 아이디입니다.", "error")
        elif query("SELECT id FROM users WHERE email = %s", (email,), one=True):
            flash("이미 가입된 이메일입니다.", "error")
        else:
            execute("INSERT INTO users (username, password_hash, name, email, role, team) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (username, generate_password_hash(password), name, email, role, team))
            log.info("사용자 생성: %s (%s) by %s", username, role, g.user["username"])
            flash(f"'{username}' 계정을 만들었습니다.", "success")
        return redirect(url_for("admin_users"))
    users = query("SELECT id, username, name, email, role, team, failed_count, is_locked, last_login_at, created_at "
                  "FROM users ORDER BY is_locked DESC, role, username")
    return render_template("admin_users.html", users=users)

@app.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_user_action(user_id, action):
    user = query("SELECT id, username FROM users WHERE id = %s", (user_id,), one=True)
    if not user:
        abort(404)

    if action == "unlock":
        execute("UPDATE users SET is_locked = 0, failed_count = 0 WHERE id = %s", (user_id,))
        log.info("잠금 해제: %s by %s", user["username"], g.user["username"])
        flash(f"'{user['username']}' 잠금을 해제했습니다.", "success")
    elif action == "team":
        team = request.form.get("team") or None
        if team and team not in TEAMS:
            abort(400)
        execute("UPDATE users SET team = %s WHERE id = %s", (team, user_id))
        flash(f"'{user['username']}' 소속팀을 {TEAMS.get(team, '없음')}(으)로 바꿨습니다.", "success")
    elif action == "lock":
        if user_id == g.user["id"]:
            flash("자기 자신은 잠글 수 없습니다.", "error")
        else:
            execute("UPDATE users SET is_locked = 1 WHERE id = %s", (user_id,))
            flash(f"'{user['username']}' 계정을 잠갔습니다.", "success")
    elif action == "password":
        password = request.form.get("password") or ""
        if len(password) < 8:
            flash("비밀번호는 8자 이상이어야 합니다.", "error")
        else:
            execute("UPDATE users SET password_hash = %s, failed_count = 0 WHERE id = %s",
                    (generate_password_hash(password), user_id))
            log.info("비밀번호 변경: %s by %s", user["username"], g.user["username"])
            flash(f"'{user['username']}' 비밀번호를 변경했습니다.", "success")
    else:
        abort(404)
    return redirect(url_for("admin_users"))


AUDIT_ACTIONS = ["LOGIN", "LOGOUT", "SIGNUP", "ACCOUNT_LOCKED", "ACCESS", "UPLOAD", "DOWNLOAD", "UPDATE",
                 "DELETE", "FOLDER"]




# ---------------------------------------------------------------------------
# 헬스체크 (로그인 불필요) — 관제에서 상태 확인용
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    status = {"storage": {}, "db": False}
    s = STORAGES[ACTIVE_STORAGE]
    status["storage"][ACTIVE_STORAGE] = os.access(s["path"], os.W_OK)
    try:
        query("SELECT 1")
        status["db"] = True
    except Exception as e:
        log.warning("health: DB 연결 실패: %s", e)
    healthy = status["db"] and all(status["storage"].values())
    return jsonify(ok=healthy, **status), (200 if healthy else 503)


# ---------------------------------------------------------------------------
# 오류 화면
# ---------------------------------------------------------------------------
@app.errorhandler(HTTPException)
def http_error(e):
    if e.code == 413:
        msg = f"파일이 너무 큽니다. 최대 {config.MAX_UPLOAD_MB} MB 까지 올릴 수 있습니다."
    elif e.code == 403:
        msg = (e.description if e.description and "permission" not in e.description
               else "이 작업을 할 권한이 없습니다.")
    elif e.code == 404:
        msg = e.description if e.description and "URL" not in e.description else "찾을 수 없습니다."
    else:
        msg = e.description or e.name
    if wants_json():
        return jsonify(ok=False, message=msg), e.code
    return render_template("error.html", code=e.code, message=msg), e.code


# ---------------------------------------------------------------------------
# 업로드
# ---------------------------------------------------------------------------
@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """파일 하나 올리기 — 화면의 JS 가 여러 파일을 하나씩 이 주소로 보낸다"""
    fs = request.files.get("file")
    folder_id = request.form.get("folder_id", type=int)
    storage = ACTIVE_STORAGE

    def done(ok, msg, status=200):
        if wants_json():
            return jsonify(ok=ok, message=msg), status
        flash(msg, "success" if ok else "error")
        return redirect(url_for("files", folder=folder_id))

    folder = get_folder_or_404(folder_id, "UPLOAD") if folder_id else None
    dept = (folder["dept"] if folder and folder["dept"] in DEPARTMENTS else "general")
    if not fs or not fs.filename:
        return done(False, "파일을 선택하세요.", 400)

    original_name = os.path.basename(fs.filename.replace("\\", "/"))[:255]
    incoming = request.content_length or 0
    with tracer.start_as_current_span("quota.check") as span:
        q = folder_quota(folder_id)
        over = bool(q and q["used"] + incoming > q["limit"])
        span.set_attribute("upload.size_mb", incoming // 1048576)
        if q:
            span.set_attribute("quota.limit_mb", q["quota_mb"])
        if over and trace is not None:
            span.set_status(Status(StatusCode.ERROR, "folder quota exceeded"))
    if over:
        left_mb = max(q["limit"] - q["used"], 0) // 1048576
        log_audit("UPLOAD", "FAIL", file=original_name,
                  detail=f"용량 한도 초과: {q['name']} {q['used'] // 1048576}MB/{q['quota_mb']}MB, 요청 {incoming // 1048576}MB")
        return done(False, f"'{q['name']}' 폴더 용량 한도({q['quota_mb']}MB)를 넘어 올릴 수 없습니다. 남은 용량 {left_mb}MB", 413)
    # 디스크에는 팀 디렉터리/UUID 로 저장 (한글·특수문자·중복 회피 + 팀별 XFS 쿼터 적용)
    # 예: team1/8b7d4e20-....  → 파일서버 /nfs/share/uploads/team1/ (팀마다 물리 용량 한도)
    stored_name = f"{dept}/{uuid.uuid4()}"
    path = storage_path(storage, stored_name)

    try:
        with tracer.start_as_current_span("nfs.save") as span:
            span.set_attribute("file.name", original_name)
            size = save_upload(fs, path)
            span.set_attribute("file.size_bytes", size)
    except OSError as e:
        if e.errno in (errno.EDQUOT, errno.ENOSPC):
            # 앱 한도(DB)는 통과했지만 파일서버의 팀별 디스크 한도(XFS 쿼터)에 걸린 경우
            log_audit("UPLOAD", "FAIL", file=original_name, detail=f"디스크 쿼터 초과 ({dept})")
            log.warning("업로드 디스크 쿼터 초과: %s", path)
            return done(False, f"{DEPARTMENTS.get(dept, dept)} 저장 공간(디스크 한도)이 가득 찼습니다. 관리자에게 문의하세요.", 507)
        log_audit("UPLOAD", "FAIL", file=original_name, detail="저장소 쓰기 실패")
        log.exception("업로드 저장 실패: %s", path)
        return done(False, f"{STORAGES[storage]['label']} 저장소에 쓰지 못했습니다.", 500)

    try:
        with tracer.start_as_current_span("db.insert"):
            if folders_enabled():
                _, file_id = execute(
                    "INSERT INTO files (original_name, stored_name, size_bytes, uploaded_by, storage, dept, folder_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (original_name, stored_name, size, g.user["id"], storage, dept, folder_id))
            else:
                # folders 표가 없을 때: 부서 폴더(음수 id) → dept 칸으로만 나눈다
                _, file_id = execute(
                    "INSERT INTO files (original_name, stored_name, size_bytes, uploaded_by, storage, dept) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (original_name, stored_name, size, g.user["id"], storage, dept))
    except Exception:
        log_audit("UPLOAD", "FAIL", file=original_name, detail="DB 기록 실패")
        os.remove(path)                         # DB 에 못 넣었으면 고아 파일이 남지 않게 삭제
        log.exception("files INSERT 실패")
        return done(False, "파일 정보를 DB에 기록하지 못했습니다.", 500)

    where = folder["name"] if folder else "공유 드라이브"
    log_audit("UPLOAD", "SUCCESS", file=original_name, detail=f"{where}, {size} bytes")
    return done(True, f"'{original_name}' 업로드 완료")
