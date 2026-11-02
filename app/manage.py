#!/usr/bin/env python3
"""filehub 관리용 명령 — 첫 관리자 계정 만들기 등 (웹에 로그인하기 전 단계에서 사용)

사용법 (웹서버에서, filehub 계정으로):
  cd /opt/filehub/app
  sudo -u filehub /opt/filehub/venv/bin/python manage.py create-user admin "관리자" admin
  sudo -u filehub /opt/filehub/venv/bin/python manage.py passwd admin
  sudo -u filehub /opt/filehub/venv/bin/python manage.py unlock partner01
  sudo -u filehub /opt/filehub/venv/bin/python manage.py list-users
  sudo -u filehub /opt/filehub/venv/bin/python manage.py check
"""
import argparse
import getpass
import os
import re
import sys

ENV_FILE = os.environ.get("FILEHUB_ENV", "/opt/filehub/.env")


def load_env(path):
    """systemd 밖에서 실행할 때도 .env 값을 쓰도록 직접 읽어 환경변수에 넣는다"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env(ENV_FILE)

import pymysql  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import config  # noqa: E402

ROLES = ("admin", "staff")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def connect():
    return pymysql.connect(**config.DB, charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)


def ask_password():
    while True:
        pw = getpass.getpass("비밀번호 (8자 이상): ")
        if len(pw) < 8:
            print("  8자 이상이어야 합니다.")
            continue
        if pw != getpass.getpass("비밀번호 확인: "):
            print("  두 번 입력한 값이 다릅니다.")
            continue
        return pw


def ask_email():
    while True:
        email = input("이메일: ").strip().lower()
        if len(email) <= 255 and EMAIL_RE.fullmatch(email):
            return email
        print("  이메일 형식이 올바르지 않습니다.")


def cmd_create_user(args):
    email = ask_email()
    pw = ask_password()
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s OR email = %s", (args.username, email))
        if cur.fetchone():
            sys.exit(f"'{args.username}' 또는 '{email}' 은(는) 이미 있습니다.")
        cur.execute("INSERT INTO users (username, password_hash, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                    (args.username, generate_password_hash(pw), args.name, email, args.role))
    print(f"생성 완료: {args.username} ({args.role}, {email})")


def cmd_passwd(args):
    pw = ask_password()
    with connect() as db, db.cursor() as cur:
        n = cur.execute("UPDATE users SET password_hash = %s, failed_count = 0 WHERE username = %s",
                        (generate_password_hash(pw), args.username))
    print("변경 완료" if n else f"'{args.username}' 을(를) 찾을 수 없습니다.")


def cmd_unlock(args):
    with connect() as db, db.cursor() as cur:
        n = cur.execute("UPDATE users SET is_locked = 0, failed_count = 0 WHERE username = %s",
                        (args.username,))
    print("잠금 해제 완료" if n else f"'{args.username}' 을(를) 찾을 수 없습니다.")


def cmd_list_users(_args):
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT username, name, email, role, is_locked, failed_count, last_login_at "
                    "FROM users ORDER BY role, username")
        rows = cur.fetchall()
    if not rows:
        print("사용자가 없습니다. create-user 로 관리자를 먼저 만드세요.")
    for r in rows:
        lock = "잠김" if r["is_locked"] else "정상"
        print(f"{r['username']:<16} {r['name']:<12} {r['email'] or '-':<28} {r['role']:<6} {lock}  "
              f"실패 {r['failed_count']}  마지막 로그인 {r['last_login_at'] or '-'}")


def cmd_check(_args):
    """설치 점검: DB 접속, 테이블·storage 칼럼, 저장소 쓰기 권한"""
    ok = True
    try:
        with connect() as db, db.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = {list(r.values())[0] for r in cur.fetchall()}
            missing = {"users", "files", "folders"} - tables
            print(f"[DB]      {config.DB['user']}@{config.DB['host']}/{config.DB['database']} 접속 OK")
            if missing:
                ok = False
                print(f"[DB]      테이블 없음: {', '.join(sorted(missing))}")
            cur.execute("SHOW COLUMNS FROM files LIKE 'storage'")
            if cur.fetchone():
                print("[DB]      files.storage 칼럼 OK")
            else:
                ok = False
                print("[DB]      files.storage 칼럼 없음 → deploy/add_storage_column.sql 실행 필요")
            cur.execute("SHOW COLUMNS FROM users LIKE 'email'")
            if cur.fetchone():
                print("[DB]      users.email 칼럼 OK")
            else:
                ok = False
                print("[DB]      users.email 칼럼 없음 → DB 서버에서 ALTER 실행 필요")
    except Exception as e:
        ok = False
        print(f"[DB]      접속 실패: {e}")

    # 앱은 NFS 하나만 쓴다 — 예전 iSCSI 항목이 config 에 남아 있어도 점검하지 않음
    s = config.STORAGES["nfs"]
    w = os.access(s["path"], os.W_OK)
    ok &= w
    print(f"[nfs   ]  {s['path']} {'쓰기 OK' if w else '쓰기 불가 (마운트·권한 확인)'}")

    print(f"[secret]  SECRET_KEY {'설정됨' if config.SECRET_KEY else '없음 (config.py 확인)'}")
    ok &= bool(config.SECRET_KEY)
    print("\n전체:", "정상" if ok else "문제 있음")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description="filehub 관리 명령")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create-user", help="계정 생성 (이메일·비밀번호는 실행 중에 입력)")
    c.add_argument("username")
    c.add_argument("name")
    c.add_argument("role", choices=ROLES)
    c.set_defaults(func=cmd_create_user)

    c = sub.add_parser("passwd", help="비밀번호 변경")
    c.add_argument("username")
    c.set_defaults(func=cmd_passwd)

    c = sub.add_parser("unlock", help="잠금 해제")
    c.add_argument("username")
    c.set_defaults(func=cmd_unlock)

    sub.add_parser("list-users", help="계정 목록").set_defaults(func=cmd_list_users)
    sub.add_parser("check", help="설치 점검").set_defaults(func=cmd_check)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()