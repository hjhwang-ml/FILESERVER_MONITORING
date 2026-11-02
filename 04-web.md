# 04. 웹/WAS 서버 (Nginx + Gunicorn + Flask)

> HTTPS, NFS 업로드, MariaDB 연동, OpenTelemetry 트레이스  
> 기준 : Rocky Linux 10, `app/` 소스 (2026-10-30), 2026-10-30 실제 구성

[← 03. DB서버](03-db.md) · [README](../README.md) · [05. 관제서버 →](05-monitoring.md)

---

## 목차

- [0. 이 문서로 만드는 것](#0-이-문서로-만드는-것)
- [1. 기본 준비](#1-기본-준비)
- [2. 앱 배치와 파이썬 환경](#2-앱-배치와-파이썬-환경)
- [3. Gunicorn 을 systemd 서비스로 (WAS)](#3-gunicorn-을-systemd-서비스로-was)
- [4. HTTPS 인증서](#4-https-인증서)
- [5. Nginx (앞단 웹 서버)](#5-nginx-앞단-웹-서버)
- [6. 방화벽](#6-방화벽-사용자망은-dmz-서버망은-work)
- [7. 화면에서 확인](#7-화면에서-확인)
- [8. 앱 동작 설정](#8-앱-동작-설정-apppy-안의-값)
- [9. OpenTelemetry 트레이스 → Tempo](#9-opentelemetry-트레이스--관제-tempo)
- [10. 자주 막히는 곳](#10-자주-막히는-곳)

---

## 0. 이 문서로 만드는 것

```
브라우저
   │ HTTPS 443 (사설 CA "filehub Root CA" 서명 인증서)
   ▼
[웹서버 192.168.0.220 / 192.168.56.101]
  Nginx ── SSL 처리 · 업로드 크기 제한 · 정적 파일 · JSON 접근 로그
   │ http://127.0.0.1:8000 (서버 안에서만)
   ▼
  Gunicorn (gthread 3 워커 × 4 스레드)  ← systemd filehub.service
   │
   ▼
  Flask 앱 /opt/filehub/app/app.py
   ├─ 파일 본문   → /mnt/nfs/uploads/<팀>/UUID  (NFS → 파일서버, 팀별 XFS 쿼터)
   ├─ 목록·사용자 → MariaDB 192.168.56.103:3306 (filehub_app 계정)
   ├─ 감사 로그   → journald → Alloy → 관제 Loki (trace_id 포함)
   └─ 요청 트레이스 → Alloy(127.0.0.1:4317) → 관제 Tempo
```

| 구성 | 역할 |
|---|---|
| Nginx | 웹 서버 (바깥 손님을 맞는 창구) |
| Gunicorn | WAS (파이썬 앱을 실제로 돌리는 일꾼들) |
| Flask | 앱 코드 (업로드, 로그인 같은 업무 처리) |

| 웹서버 NIC | IP | 용도 |
|---|---|---|
| enp0s10 | 192.168.0.220 | 사용자망 (dmz 존 : http, https, ssh) |
| enp0s8 | 192.168.56.101 | 서버망 (work 존) |

| 디렉터리 | 내용 |
|---|---|
| `/opt/filehub/app` | 앱 소스 (이 저장소의 `app/`) |
| `/opt/filehub/.venv` | 파이썬 가상환경 |
| `/opt/filehub/.env` | DB 비밀번호 · SECRET_KEY (권한 600) |

**앞서 끝나 있어야 하는 것**
- [01. NFS 2부](01-nfs.md#2-웹서버-19216856101) : 웹서버 `/mnt/nfs` 마운트, filehub 계정 UID 2000
- [03. DB서버](03-db.md) : MariaDB, filehub DB, users · folders · files 테이블, `filehub_app@192.168.56.101` 계정과 세 테이블 권한, DB 방화벽 3306 을 56.101 에게 허용

> 명령은 전부 root 로 실행한다. `<꺾쇠>` 값은 각자 환경에 맞게 바꾼다.

---

## 1. 기본 준비

### 1) 패키지

📍 **WebServer**
```bash
# Nginx, 파이썬, 압축 해제·SELinux 도구 설치
dnf install -y nginx python3 python3-pip unzip policycoreutils-python-utils

# 파이썬 버전 확인 (3.12 이상)
python3 --version
```
> `policycoreutils-python-utils` = SELinux 라벨을 지정하는 `semanage` 명령이 들어 있는 패키지

### 2) filehub 계정과 NFS 확인

📍 **WebServer**
```bash
# 계정 확인 (uid=2000(filehub) gid=2000(filehub))
id filehub

# NFS 마운트 확인 (192.168.56.102:/nfs/share)
findmnt /mnt/nfs

# filehub 로 업로드 폴더에 쓰기 시험
sudo -u filehub touch /mnt/nfs/uploads/.w && sudo -u filehub rm /mnt/nfs/uploads/.w && echo "쓰기 OK"
```
> 없거나 실패하면 [01. NFS 2부](01-nfs.md#2-웹서버-19216856101)부터 한다.

### 3) DB 접속 확인 (웹서버에서)

📍 **WebServer**
```bash
# MariaDB 클라이언트만 설치
dnf install -y mariadb

# 앱 계정으로 테이블 목록 확인 (users, folders, files 가 보이면 OK)
mysql -h 192.168.56.103 -u filehub_app -p -e "SHOW TABLES FROM filehub;"
```

| 옵션 | 뜻 |
|---|---|
| `-h` | 접속할 DB 서버 주소 |
| `-u` | DB 계정 |
| `-p` | 비밀번호를 실행 중에 물어봄 (명령 줄에 적지 않음) |
| `-e` | 실행할 SQL |

- 실패하면 : DB 방화벽 3306 rich rule, `filehub_app@192.168.56.101` 계정, folders 테이블 권한(GRANT)을 DB 서버에서 확인.
- folders 권한이 없으면 앱이 폴더 기능을 끈 채로 조용히 돈다.

---

## 2. 앱 배치와 파이썬 환경

### 1) 소스 배치

📍 **WebServer**
```bash
# 앱 폴더 만들기
mkdir -p /opt/filehub

# 앱 폴더로 이동
cd /opt/filehub

# 소스 압축 풀기 (app/ 폴더가 생김)
unzip /root/filehub_app.zip

# 파일 확인 (app.py config.py db.py manage.py static templates ...)
ls /opt/filehub/app
```

- PC 에서 웹서버로 올리기 : `scp filehub_app.zip root@192.168.0.220:/root/`
- 또는 저장소를 클론해 `app/` 폴더를 `/opt/filehub/app` 으로 복사해도 된다.
- 배포본은 DB 비밀번호와 `SECRET_KEY` 가 비어 있으므로 **2-3) 의 `.env` 를 반드시 만들어야** 앱이 뜬다.

| 파일 | 내용 |
|---|---|
| `app.py` | 라우트 전부 (로그인, 업로드, 폴더, 관리자, `/health`) |
| `config.py` | DB 접속 정보, 업로드 경로, 업로드 최대 크기 (환경변수에서 읽음) |
| `db.py` | 요청마다 MariaDB 연결 1개 |
| `manage.py` | 관리 명령 (관리자 계정 만들기, 점검) |
| `templates/` | 화면 HTML |
| `static/` | CSS, JS, 아이콘 (Nginx 가 직접 내줌) |

### 2) 가상환경과 라이브러리

📍 **WebServer**
```bash
# 앱 전용 가상환경 만들기
python3 -m venv /opt/filehub/.venv

# 가상환경의 pip 업그레이드
/opt/filehub/.venv/bin/pip install --upgrade pip

# 필요한 라이브러리 설치
/opt/filehub/.venv/bin/pip install -r /opt/filehub/app/requirements.txt

# 라이브러리 목록 확인
cat /opt/filehub/app/requirements.txt
# Flask>=3.0,<4 / gunicorn>=23,<24 / PyMySQL>=1.1,<2
```
> **가상환경** = 이 앱 전용 파이썬 라이브러리 상자. 시스템 파이썬과 섞이지 않는다. `source activate` 없이 `.venv/bin/pip`, `.venv/bin/python` 처럼 경로로 바로 실행해도 된다.

### 3) 비밀 값 파일 (`.env`)

📍 **WebServer**
```bash
# 세션 서명 키 하나 만들기 (출력된 값을 복사)
python3 -c "import secrets; print(secrets.token_hex(32))"

# DB 접속 정보와 비밀 값 파일 작성
cat > /opt/filehub/.env <<'EOF'
DB_HOST=192.168.56.103
DB_PORT=3306
DB_USER=filehub_app
DB_PASS=<filehub_app 비밀번호>
DB_NAME=filehub
SECRET_KEY=<위에서 만든 값>
NFS_DIR=/mnt/nfs/uploads
MAX_UPLOAD_MB=2048
SESSION_COOKIE_SECURE=1
EOF

# root 만 읽도록 권한 600
chmod 600 /opt/filehub/.env
```

| 변수 | 뜻 |
|---|---|
| `DB_*` | 앱이 접속할 MariaDB 정보 |
| `SECRET_KEY` | 로그인 세션 쿠키 서명 키 |
| `NFS_DIR` | 업로드 저장 경로 |
| `MAX_UPLOAD_MB` | 업로드 최대 크기 (Nginx `client_max_body_size` 와 맞춤) |
| `SESSION_COOKIE_SECURE` | 1 이면 HTTPS 에서만 로그인 쿠키 전송 |
| `LOCK_THRESHOLD` | (선택) 연속 로그인 실패 몇 회에 잠글지, 기본 5 |

- `config.py` 는 이 값들을 환경변수에서 읽는다. systemd 가 `.env` 를 읽어 넣어 준다 (`EnvironmentFile=`).
- `SECRET_KEY` 가 비어 있으면 앱이 아예 시작을 거부한다 (워커마다 다른 키를 쓰면 로그인이 랜덤하게 풀리기 때문).
- `SESSION_COOKIE_SECURE=1` 은 4부 SSL 이후 필수.
- 비밀번호가 소스와 분리되므로 `.env` 방식을 권한다. `.env` 는 `.gitignore` 대상이다.

### 4) 소유자

📍 **WebServer**
```bash
# 앱 폴더 전체를 filehub 소유로
chown -R filehub:filehub /opt/filehub

# 상위 폴더 권한 755
chmod 755 /opt/filehub /opt/filehub/app

# 소유자·권한 확인
ls -la /opt/filehub
```
> Nginx(nginx 계정)가 `static` 폴더까지 들어갈 수 있도록 상위 폴더는 755.

### 5) 설치 점검과 첫 관리자 계정

📍 **WebServer**
```bash
# 앱 폴더로 이동
cd /opt/filehub/app

# 설치 점검
sudo -u filehub /opt/filehub/.venv/bin/python manage.py check
# [DB] 접속 OK, [DB] users.email 칼럼 OK, [nfs] 쓰기 OK, [secret] 설정됨 → 전체: 정상
```
> `files.storage` 칼럼 줄도 OK 여야 한다.

```bash
# 관리자 계정 만들기 (이메일과 비밀번호는 실행 중에 물어본다)
sudo -u filehub /opt/filehub/.venv/bin/python manage.py create-user admin "관리자" admin

# 계정 목록 확인
sudo -u filehub /opt/filehub/.venv/bin/python manage.py list-users

# 홈 디렉터리로 복귀
cd ~
```

`manage.py` 명령 전체 :

| 명령 | 뜻 |
|---|---|
| `check` | DB 접속 · 칼럼 · NFS 쓰기 · SECRET_KEY 점검 |
| `create-user <아이디> <이름> <admin\|staff>` | 계정 생성 (이메일·비밀번호는 실행 중 입력) |
| `list-users` | 계정 목록 |
| `passwd <아이디>` | 비밀번호 변경 |
| `unlock <아이디>` | 5회 실패로 잠긴 계정 풀기 |

> `sudo -u filehub` 로 실행하는 이유 : NFS 쓰기 점검을 실제 앱 계정(UID 2000)으로 해야 의미가 있다.

---

## 3. Gunicorn 을 systemd 서비스로 (WAS)

### 1) 서비스 파일

📍 **WebServer**
```bash
# filehub 앱 서비스 파일 작성
cat > /etc/systemd/system/filehub.service <<'EOF'
[Unit]
Description=filehub web app (Gunicorn)
After=network-online.target remote-fs.target
Wants=network-online.target
RequiresMountsFor=/mnt/nfs

[Service]
User=filehub
Group=filehub
WorkingDirectory=/opt/filehub/app
EnvironmentFile=/opt/filehub/.env
ExecStart=/opt/filehub/.venv/bin/gunicorn \
  --worker-class gthread --workers 3 --threads 4 \
  --bind 127.0.0.1:8000 --timeout 120 \
  --access-logfile - app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# 새 서비스 파일 다시 읽기
systemctl daemon-reload

# 앱 시작 및 부팅 시 자동 시작
systemctl enable --now filehub

# 실행 상태 확인
systemctl status filehub --no-pager
```

| 항목 | 뜻 |
|---|---|
| `After=... remote-fs.target` | 네트워크 · 원격 파일시스템 다음에 시작 |
| `RequiresMountsFor=/mnt/nfs` | NFS 가 붙기 전에는 앱을 띄우지 않는다. 안 그러면 로컬 디스크의 빈 `/mnt/nfs` 에 업로드가 쌓인다 |
| `User=filehub` | UID 2000 으로 실행 → NFS 에 2000 소유로 저장 |
| `EnvironmentFile=` | `.env` 의 값을 환경변수로 넣어 줌 |
| `--worker-class gthread` | 워커 하나가 여러 스레드로 요청을 처리 (업로드처럼 I/O 대기가 긴 요청에 유리) |
| `--workers 3 --threads 4` | 프로세스 3개 × 스레드 4개 = 동시 요청 12개 |
| `--bind 127.0.0.1:8000` | 서버 안에서만 접속 가능 (바깥은 Nginx 를 거쳐야 함) |
| `--timeout 120` | 큰 업로드가 넘어오는 동안 워커가 끊기지 않게 |
| `--access-logfile -` | 접근 로그를 표준출력 → journald 로 |
| `app:app` | app.py 파일 안의 `app` 객체를 실행 |
| `Restart=on-failure` | 비정상 종료 시 자동 재시작 |

> 지금 서버의 실제 서비스 파일과 다를 수 있다. `systemctl cat filehub` 으로 대조해서 맞출 것.

### 2) WAS 단독 확인 (Nginx 없이)

📍 **WebServer**
```bash
# 앱 상태 확인
curl -s http://127.0.0.1:8000/health
# {"db":true,"ok":true,"storage":{"nfs":true}}

# 앱 로그 확인
journalctl -u filehub -n 30 --no-pager
```
> `/health` 는 DB 접속과 NFS 쓰기를 같이 본다. 둘 중 하나라도 실패면 503.

---

## 4. HTTPS 인증서

### 1) 사설 CA 만들기 (웹서버에서 한 번만)

자체 서명 인증서는 접속하는 PC 마다 "안전하지 않음" 경고가 뜬다. **우리만의 인증기관(CA)을 하나 만들어** 웹 · 관제 인증서에 서명하고, PC 에 CA 인증서(`ca.crt`) 하나만 신뢰 등록하면 두 사이트 모두 경고 없이 열린다.

📍 **WebServer**
```bash
# CA 작업 폴더 만들고 이동
mkdir -p /root/filehub-ca && cd /root/filehub-ca

# CA 키와 CA 인증서 만들기 (10년)
openssl req -x509 -new -nodes -newkey rsa:4096 -days 3650 \
  -keyout ca.key -out ca.crt -subj "/CN=filehub Root CA"

# CA 키는 root 만 읽도록
chmod 600 ca.key
```

| 옵션 | 뜻 |
|---|---|
| `req -x509` | 서명 요청 대신 바로 자기 서명 인증서 생성 (CA 용) |
| `-new -newkey rsa:4096` | 새 RSA 4096비트 키 생성 |
| `-nodes` | 키를 암호로 잠그지 않음 (서비스가 재시작 때 비밀번호를 묻지 않게) |
| `-days 3650` | 유효 기간 10년 |
| `-subj "/CN=..."` | 인증서 이름 (질문 없이 지정) |

> `ca.key` 는 인증서를 찍어 내는 **도장**이다. 이 서버 밖으로 내보내지 않는다. 밖으로 나가는 건 `ca.crt` 뿐.

### 2) 웹서버 인증서 (CA 서명)

📍 **WebServer** (`/root/filehub-ca`)
```bash
# 웹서버 키와 서명 요청(CSR) 만들기
openssl req -new -nodes -newkey rsa:2048 \
  -keyout /etc/pki/tls/private/filehub.key -out web.csr -subj "/CN=192.168.0.220"

# 인증서 용도와 접속 주소(SAN) 적기
cat > web.ext <<'EOF'
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:192.168.0.220,IP:192.168.56.101
EOF

# CA 로 서명해 인증서 발급
openssl x509 -req -in web.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -extfile web.ext -out /etc/pki/tls/certs/filehub.crt

# 개인 키는 root 만 읽도록
chmod 600 /etc/pki/tls/private/filehub.key

# CA 서명이 맞는지 확인 (OK 가 나오면 정상)
openssl verify -CAfile ca.crt /etc/pki/tls/certs/filehub.crt

# 발급자 · 주소(SAN) · 유효 기간 확인
openssl x509 -in /etc/pki/tls/certs/filehub.crt -noout -issuer -ext subjectAltName -dates

# 홈 디렉터리로 복귀
cd ~
```

| 항목 | 뜻 |
|---|---|
| CSR | 인증서 서명 요청서 ("이 키로 이 이름의 인증서를 찍어 주세요") |
| `basicConstraints=CA:FALSE` | 이 인증서로는 다른 인증서를 서명할 수 없음 |
| `extendedKeyUsage=serverAuth` | 서버 인증용 |
| `subjectAltName=IP:...` | 이 인증서로 접속할 수 있는 주소 목록 |
| `-CAcreateserial` | 발급 일련번호 파일(`ca.srl`)을 자동 생성 |
| `-days 825` | 브라우저가 허용하는 서버 인증서 최대 유효 기간 |

- `issuer` 가 `CN = filehub Root CA` 이고 SAN 에 IP 두 개가 보이면 성공.
- **SAN 에 접속 주소 IP 가 있어야** 브라우저가 주소 불일치로 보지 않는다 (요즘 브라우저는 CN 을 보지 않음).
- 관제서버 Grafana 인증서도 같은 CA 로 여기서 발급한다 ([05. 관제서버 6)-A](05-monitoring.md#a-가동-및-기본-설정)).

### 3) PC 에 CA 인증서 신뢰 등록 (윈도우)

1. `ca.crt` 를 PC 로 받는다 (예 : PC 의 cmd 에서 `scp root@192.168.0.220:/root/filehub-ca/ca.crt .`)
2. `ca.crt` 더블클릭 → 인증서 설치 → 로컬 컴퓨터 → "모든 인증서를 다음 저장소에 저장" → **신뢰할 수 있는 루트 인증 기관** → 마침
3. 브라우저를 완전히 닫았다가 다시 열고 `https://192.168.0.220` 접속 → 경고 없이 열리면 성공

- 등록하지 않은 PC 는 예전처럼 경고 → 고급 → 계속 으로 들어가면 된다 (암호화는 같음).
- 공인 인증서(경고 없음 + 등록 불필요)는 도메인이 있어야 발급받을 수 있다.

---

## 5. Nginx (앞단 웹 서버)

### 1) 설정 파일

📍 **WebServer**
```bash
# filehub 사이트 설정 작성
cat > /etc/nginx/conf.d/filehub.conf <<'EOF'
log_format filehub_json escape=json '{'
  '"time":"$time_iso8601",'
  '"remote_addr":"$remote_addr",'
  '"method":"$request_method",'
  '"uri":"$request_uri",'
  '"status":$status,'
  '"bytes":$body_bytes_sent,'
  '"req_bytes":$request_length,'
  '"req_time":$request_time,'
  '"upstream_time":"$upstream_response_time",'
  '"user_agent":"$http_user_agent"'
'}';

server {
    listen 80;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/pki/tls/certs/filehub.crt;
    ssl_certificate_key /etc/pki/tls/private/filehub.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    access_log /var/log/nginx/filehub_access.log filehub_json;
    error_log  /var/log/nginx/filehub_error.log;

    client_max_body_size 2048m;

    location /static/ {
        alias /opt/filehub/app/static/;
        expires 1h;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 300;
        proxy_send_timeout 300;
    }
}
EOF

# 설정 문법 검사
nginx -t
```

> `conflicting server name "_" on 0.0.0.0:80, ignored` 경고는 `nginx.conf` 기본 server 블록과 이름이 겹쳐서 나는 것. conf.d 가 먼저 읽혀 http → https 전환은 정상 동작한다 (`syntax is ok` 면 통과).

| 설정 | 뜻 |
|---|---|
| 80 블록 | http 로 오면 https 로 돌려보냄 (301 영구 이동) |
| `server_name _` | 아무 호스트 이름이든 받음 (IP 접속) |
| `ssl_protocols TLSv1.2 TLSv1.3` | 오래된 TLS 1.0/1.1 차단 |
| `client_max_body_size` | 업로드 한도. 앱의 `MAX_UPLOAD_MB`(2048)와 맞춘다. Nginx 쪽이 작으면 앱까지 가기 전에 413 에러 |
| `location /static/` | CSS·JS 는 Nginx 가 직접 내줌 (Gunicorn 을 안 거침), 1시간 캐시 |
| `proxy_pass` | 나머지는 전부 Gunicorn 으로 넘김 |
| `X-Forwarded-*` | 실제 사용자 IP · https 여부를 앱에 알려 줌. 앱의 `ProxyFix` 가 이걸 읽어서 감사 로그에 진짜 IP 가 찍힌다 |
| `proxy_read/send_timeout 300` | 큰 업로드 저장 중 연결이 끊기지 않게 |
| `log_format ... escape=json` | 접근 로그를 JSON 한 줄로 → 관제(Alloy → Loki)가 칸별로 쪼개 보기 쉽게 |

> 지금 서버의 실제 `filehub.conf` 와 다를 수 있다 (특히 log_format 칸, 업로드 한도). `cat /etc/nginx/conf.d/filehub.conf` 로 대조할 것.

### 2) SELinux

📍 **WebServer**
```bash
# Nginx 가 127.0.0.1:8000 으로 넘길 수 있게
setsebool -P httpd_can_network_connect 1

# Nginx 가 /opt 아래 static 을 읽을 수 있게 라벨 지정
semanage fcontext -a -t httpd_sys_content_t "/opt/filehub/app/static(/.*)?"

# 지정한 라벨을 실제 파일에 적용
restorecon -Rv /opt/filehub/app/static

# 설정 확인 (on)
getsebool httpd_can_network_connect

# 라벨 확인 (httpd_sys_content_t)
ls -Zd /opt/filehub/app/static
```

| 명령 | 뜻 |
|---|---|
| `setsebool -P` | SELinux 스위치(boolean)를 영구(-P) 변경 |
| `semanage fcontext -a -t 타입 "경로"` | 이 경로 파일에 붙을 라벨 규칙을 추가 |
| `restorecon -Rv` | 규칙대로 실제 파일 라벨을 다시 붙임 |
| `ls -Z` | 파일의 SELinux 라벨 표시 |

- 안 하면 : **502 Bad Gateway** (프록시 차단) 또는 **CSS 가 안 나오는 화면** (static 차단).
- 차단 기록 확인 : `ausearch -m avc -ts recent`

### 3) 시작

📍 **WebServer**
```bash
# Nginx 시작 및 부팅 시 자동 시작
systemctl enable --now nginx

# HTTPS 로 앱 상태 확인 (-k : 인증서 검증 생략)
curl -k https://127.0.0.1/health

# http 로 오면 https 로 넘기는지 확인 (301 → https)
curl -I http://127.0.0.1/
```

---

## 6. 방화벽 (사용자망은 dmz, 서버망은 work)

📍 **WebServer**
```bash
# NIC 별 방화벽 존 확인
firewall-cmd --get-active-zones

# 사용자망 NIC 를 dmz 존에
firewall-cmd --permanent --zone=dmz --change-interface=enp0s10

# dmz 존에 http 허용
firewall-cmd --permanent --zone=dmz --add-service=http

# dmz 존에 https 허용
firewall-cmd --permanent --zone=dmz --add-service=https

# dmz 존에 ssh 허용
firewall-cmd --permanent --zone=dmz --add-service=ssh

# 서버망 NIC 를 work 존에
firewall-cmd --permanent --zone=work --change-interface=enp0s8

# 방화벽 설정 다시 읽기
firewall-cmd --reload

# dmz 존 설정 확인
firewall-cmd --zone=dmz --list-all

# work 존 설정 확인
firewall-cmd --zone=work --list-all
```

- **8000 은 열지 않는다.** Gunicorn 은 127.0.0.1 에만 붙어 있어서 바깥에서 못 온다.
- ssh 는 팀원이 웹서버를 거쳐 다른 서버로 들어가는 길이라 연다. (팀원 IP 로 좁히려면 `--add-service=ssh` 대신 rich rule 로 IP 를 지정)
- **존(zone)** = NIC 마다 다른 방화벽 정책 묶음. 외부 사용자망은 `dmz`, 내부 서버망은 `work`.

---

## 7. 화면에서 확인

브라우저에서 `https://192.168.0.220`

1. `ca.crt` 를 신뢰 등록한 PC 는 경고 없이 열림 (등록 안 한 PC 는 경고 → 고급 → 계속, [4-3)](#3-pc-에-ca-인증서-신뢰-등록-윈도우))
2. 2-5) 에서 만든 admin 으로 로그인
3. 공용 폴더에 작은 파일 업로드 → 목록에 보이는지
4. 다운로드 → 같은 파일인지

📍 **WebServer** — 서버에서 확인
```bash
# 방금 올린 파일 확인 (공용 폴더 = general 폴더, UUID 이름)
ls -lt /mnt/nfs/uploads/general | head -3

# DB 에 파일 정보가 들어갔는지 확인
mysql -h 192.168.56.103 -u filehub_app -p filehub \
  -e "SELECT stored_name, original_name, size_bytes FROM files ORDER BY id DESC LIMIT 3;"
```
> `stored_name` 이 `general/UUID` 처럼 팀 폴더 이름으로 시작하면 정상

```bash
# Nginx 접속 로그 확인 (JSON 한 줄씩)
tail -3 /var/log/nginx/filehub_access.log

# 업로드 감사 로그 확인 (UPLOAD SUCCESS)
journalctl -u filehub -n 20 | grep filehub.audit
```

---

## 8. 앱 동작 설정 (app.py 안의 값)

📍 **WebServer**
```bash
# 세션·팀 설정 값 찾기
grep -n "IDLE_TIMEOUT\|MAX_SESSION\|TEAMS =" /opt/filehub/app/app.py
```

| 값 | 뜻 |
|---|---|
| `IDLE_TIMEOUT = 30 * 60` | 아무것도 안 하면 30분 뒤 자동 로그아웃 (시연용 2분은 너무 짧아 2026-10-30 30분으로 되돌림) |
| `MAX_SESSION = 8 * 60 * 60` | 로그인 후 최대 8시간 |
| `TEAMS = {"team1": "1팀", ...}` | 회원가입 때 고르는 팀. 다른 팀 폴더는 접근 차단 |

- 폴더 용량 한도는 앱이 아니라 **DB 의 `folders.quota_mb`** 칸에 있다 (팀 폴더 500MB, 공용 2048MB). 앱은 파일을 다 받은 뒤 저장 전에 이 값을 보고, 넘으면 **413** 으로 거절한다 (큰 파일은 거절까지 시간이 걸림 — 트레이스의 `quota.check` 구간에서 보임).
- 앱 한도가 뚫려도 파일서버 XFS 프로젝트 쿼터([01. NFS 1-7)](01-nfs.md#7-팀-폴더-용량-한도-xfs-프로젝트-쿼터))가 한 번 더 막는다. 이때는 **507 "디스크 한도 가득 참"**.
- 업로드 최대 크기 : Nginx `client_max_body_size 2048m`, 앱 `MAX_UPLOAD_MB 2048` (둘을 맞춘다)

```bash
# 값을 바꾼 뒤에는 앱 재시작
systemctl restart filehub
```

---

## 9. OpenTelemetry 트레이스 → 관제 Tempo

> 시나리오 ② 에 필요. 요청 하나하나의 처리 시간을 관제 서버 Tempo 로 보낸다. 서비스 이름 `filehub-web`.

📍 **WebServer**
```bash
# OpenTelemetry 패키지 설치
/opt/filehub/.venv/bin/pip install opentelemetry-distro opentelemetry-exporter-otlp

# 설치된 라이브러리에 맞는 계측 모듈 자동 설치 (Flask, PyMySQL 등)
/opt/filehub/.venv/bin/opentelemetry-bootstrap -a install

# 서비스 덮어쓰기 폴더 만들기
mkdir -p /etc/systemd/system/filehub.service.d

# OpenTelemetry 로 앱을 실행하는 덮어쓰기 설정 작성
cat > /etc/systemd/system/filehub.service.d/otel.conf <<'EOF'
[Service]
Environment=OTEL_SERVICE_NAME=filehub-web
Environment=OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317
Environment=OTEL_EXPORTER_OTLP_INSECURE=true
Environment=OTEL_TRACES_EXPORTER=otlp
Environment=OTEL_METRICS_EXPORTER=none
Environment=OTEL_LOGS_EXPORTER=none
Environment=OTEL_BSP_SCHEDULE_DELAY=1000
ExecStart=
ExecStart=/opt/filehub/.venv/bin/opentelemetry-instrument /opt/filehub/.venv/bin/gunicorn \
  --worker-class gthread --workers 3 --threads 4 \
  --bind 127.0.0.1:8000 --timeout 120 \
  --access-logfile - app:app
EOF

# 바뀐 서비스 설정 다시 읽기
systemctl daemon-reload

# 앱 재시작
systemctl restart filehub
```

| 환경변수 | 뜻 |
|---|---|
| `OTEL_SERVICE_NAME` | Tempo 에 보일 서비스 이름 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 트레이스를 보낼 곳 (같은 서버의 Alloy) |
| `OTEL_EXPORTER_OTLP_INSECURE` | TLS 없이 전송 (서버 내부 통신) |
| `OTEL_TRACES_EXPORTER=otlp` | 트레이스만 OTLP 로 보냄 |
| `OTEL_METRICS/LOGS_EXPORTER=none` | 메트릭·로그는 Alloy 가 따로 수집하므로 끔 |
| `OTEL_BSP_SCHEDULE_DELAY=1000` | 트레이스를 1초마다 묶어 보냄 (기본 5초). 업로드 뒤 Grafana 패널에 보이기까지 약 10초 |

- `ExecStart=` (빈 줄) 은 원래 `ExecStart` 를 지운다는 뜻. 그다음 줄이 새 실행 명령.
- `opentelemetry-instrument` 가 gunicorn 을 감싸 실행하면서 Flask · PyMySQL 요청을 자동 계측한다.
- 트레이스는 같은 서버의 Alloy(127.0.0.1:4317)가 받아 관제 Tempo(192.168.56.104:4317)로 전달 ([05. 관제서버 5)-B](05-monitoring.md#b-webserver-설정-예시-etcalloyconfigalloy)). 관제 서버 방화벽 4317 은 work 존에 열려 있어야 한다.
- 확인 : Grafana → Explore → Tempo → Service name : `filehub-web`
- `app.py` 에는 업로드 단계별 구간 **`quota.check`(한도 검사) · `nfs.save`(NFS 저장) · `db.insert`(DB 기록)** 를 직접 넣어 두었다. Tempo 폭포 그림에서 어느 단계가 느린지 바로 보인다.
- 감사 로그 JSON 에 `trace_id` 가 들어가 있어, 감사 이벤트 한 줄에서 그 요청의 트레이스를 찾을 수 있다.
- OpenTelemetry 패키지가 없으면 앱은 가짜 tracer 로 대체되어 그냥 동작한다.

> 지금 서버의 otel.conf 와 다를 수 있다. `systemctl cat filehub` 으로 대조할 것.

---

## 10. 자주 막히는 곳

**(1) 브라우저에 502 Bad Gateway**  
→ Gunicorn 이 죽었거나 SELinux 차단.
```bash
# 앱 상태 확인
systemctl status filehub

# 앱이 직접 응답하는지 확인
curl http://127.0.0.1:8000/health

# SELinux 프록시 허용 확인
getsebool httpd_can_network_connect
```

**(2) 화면은 나오는데 CSS 가 깨짐**  
→ static 경로 · SELinux 라벨 ([5-2)](#2-selinux)), `/opt/filehub` 권한 755 ([2-4)](#4-소유자)).

**(3) filehub 서비스가 안 뜸 "SECRET_KEY 가 비어 있습니다"**  
→ `.env` 의 `SECRET_KEY`, 서비스 파일의 `EnvironmentFile` 경로.

**(4) filehub 서비스가 "Dependency failed" 로 안 뜸**  
→ `/mnt/nfs` 가 안 붙음. `findmnt /mnt/nfs`, 파일서버가 켜져 있는지 확인.

**(5) 로그인이 됐다 풀렸다 함**  
→ 워커마다 `SECRET_KEY` 가 다름 (`.env` 에 고정 안 함), 또는 https 가 아닌데 `SESSION_COOKIE_SECURE=1`.

**(6) 큰 파일 업로드에서 413**  
→ Nginx `client_max_body_size` 와 `.env` 의 `MAX_UPLOAD_MB` 를 맞췄는지. 폴더 한도 초과로 거절된 경우는 화면에 "폴더 용량 한도" 문구가 나온다 (정상). 파일서버 디스크 쿼터에 막히면 507 "디스크 한도 가득 참" ([01. NFS 5장 (7)](01-nfs.md#5-자주-막히는-곳)).

**(7) 업로드에서 "저장소에 쓰지 못했습니다"**  
→ NFS 쓰기 권한. 웹서버 · 파일서버 filehub UID 가 둘 다 2000 인지 확인.
```bash
# filehub 로 쓰기 시험
sudo -u filehub touch /mnt/nfs/uploads/x
```

**(8) 폴더 기능이 안 보이고 파일만 나열됨**  
→ `filehub_app` 에 folders 테이블 권한이 없음. DB 서버에서 GRANT 후 `manage.py check`, `systemctl restart filehub`.

**(9) 감사 로그에 사용자 IP 가 전부 127.0.0.1**  
→ Nginx 의 `X-Forwarded-For` 헤더 ([5-1)](#1-설정-파일)) 누락.

**(10) 업로드가 99% 에서 오래 멈춤**  
- 원인 : 브라우저 → Nginx 전송은 끝났고, 앱이 파일을 NFS 에 저장하는 중. 웹서버 ↔ 파일서버 구간이 느리면 생긴다.
- 확인 : Tempo 에서 그 업로드 트레이스의 `nfs.save` 구간 시간. 시나리오 ② 의 tc 제한이 남아 있지 않은지도 확인.
```bash
# 대역폭 제한(netem)이 남아 있는지 확인 (있으면 tc qdisc del dev enp0s8 root)
tc qdisc show dev enp0s8
```
