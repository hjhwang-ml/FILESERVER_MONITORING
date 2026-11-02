# filehub — JYIT 사내 공용 파일서버 구축 · 통합 관제

> 부서마다 흩어져 있던 파일을 **웹에서 로그인해 팀 폴더로 주고받는 사내 공유 드라이브**로 모으고,  
> 서버 4대의 **M·E·L·T(메트릭 · 이벤트 · 로그 · 트레이스)** 를 한곳에서 관제해 이상 징후를 Slack 으로 알리는 프로젝트입니다.

<p>
  <img src="https://img.shields.io/badge/Rocky%20Linux-10.1-10B981?logo=rockylinux&logoColor=white" />
  <img src="https://img.shields.io/badge/Nginx-HTTPS-009639?logo=nginx&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/Gunicorn-23-499848?logo=gunicorn&logoColor=white" />
  <img src="https://img.shields.io/badge/MariaDB-utf8mb4-003545?logo=mariadb&logoColor=white" />
  <img src="https://img.shields.io/badge/Grafana-13.2-F46800?logo=grafana&logoColor=white" />
  <img src="https://img.shields.io/badge/Prometheus-3.15-E6522C?logo=prometheus&logoColor=white" />
  <img src="https://img.shields.io/badge/OpenTelemetry-traces-425CC7?logo=opentelemetry&logoColor=white" />
</p>

더존비즈온 Cloud DX Academy 클라우드 엔지니어 부트캠프 · **1팀 (File Server & Monitoring)**

---

## 📚 구축 문서

모든 명령에 설명을 달아 두었습니다. 아래 [진행 순서](#진행-순서)대로 따라가면 처음부터 다시 만들 수 있습니다.

| # | 문서 | 서버 | 주요 내용 |
|---|---|---|---|
| 00 | [공통 준비](#공통-준비-4대-모두) (이 문서) | 4대 모두 | 호스트 이름, 고정 IP, 방화벽 존 |
| 01 | [파일서버 · NFS](docs/01-nfs.md) | FileServer · WebServer · DBServer | sdb → XFS → `/nfs`, 팀 폴더 XFS 쿼터, 웹 업로드 공유, DB 백업 공유 |
| 02 | [파일서버 · iSCSI](docs/02-iscsi.md) | FileServer → DBServer | sdc iSCSI 타깃, CHAP, DB 서버 LVM, 무중단 · 자동 증설 |
| 03 | [DB 서버](docs/03-db.md) | DBServer | MariaDB 설치, filehub 스키마, 용도별 계정 |
| 04 | [웹/WAS 서버](docs/04-web.md) | WebServer | Nginx · Gunicorn · Flask, 사설 CA HTTPS, SELinux, OpenTelemetry |
| 05 | [관제 서버](docs/05-monitoring.md) | Monitoring (+ Alloy 4대) | Prometheus · Loki · Tempo · Grafana · Alloy, Slack 경보, 대시보드, 트러블슈팅 14건 |
| 06 | [시연 가이드](docs/06-demo.md) | 전체 | 시연 전 준비, 경보 속도, 시나리오 ①~⑤ 명령, 시연 후 정리 |

---

## 목차

1. [주요 기능](#주요-기능)
2. [아키텍처](#아키텍처)
3. [서버 구성](#서버-구성)
4. [기술 스택](#기술-스택)
5. [저장소 설계 (NFS · iSCSI)](#저장소-설계-nfs--iscsi)
6. [웹 앱 (filehub)](#웹-앱-filehub)
7. [관제 (Grafana LGTM + Alloy)](#관제-grafana-lgtm--alloy)
8. [장애 시나리오와 대응](#장애-시나리오와-대응)
9. [공통 준비 (4대 모두)](#공통-준비-4대-모두)
10. [진행 순서](#진행-순서)
11. [디렉터리 구조](#디렉터리-구조)
12. [트러블슈팅 요약](#트러블슈팅-요약)
13. [보안 메모](#보안-메모)

---

## 주요 기능

**파일 공유 웹**
- 회원가입(팀 선택 · 관리자 승인) → 로그인 → 팀 폴더 / 공용 폴더에 업로드 · 다운로드 · 교체 · 삭제
- 팀 단위 접근 제어 — 다른 팀 폴더는 조회 · 다운로드 차단
- 하위 폴더 생성(DB 에서만 관리), 정렬(이름 · 최신 · 크기 · 올린 사람)
- **폴더 용량 한도 이중 방어** : 앱(DB `folders.quota_mb`) → 413 / 파일서버 XFS 프로젝트 쿼터 → 507
- **5회 연속 로그인 실패 시 계정 자동 잠금**, 30분 미사용 · 8시간 경과 시 자동 로그아웃
- CSRF 토큰, HttpOnly · Secure 쿠키, 업로드는 `.part` 임시 파일 → 원자적 rename
- 모든 주요 동작을 **JSON 감사 로그**(`filehub.audit`)로 남김 — `trace_id` 포함

**스토리지**
- NFS : 파일서버 sdb → `/nfs` → 웹 업로드 저장소 · DB 백업 사본
- iSCSI : 파일서버 sdc → DB 서버 LVM(`vg_db`) → MariaDB 데이터 · 덤프 원본
- **LVM 무중단 자동 증설** (85% 2회 연속 → +3G)

**관제**
- 서버 4대 Alloy 가 메트릭 · 로그 · 트레이스를 Push
- 통합 대시보드 `JYIT 통합 관제 M·E·L·T`
- Slack 경보 (디스크 80%, 로그인 실패 급증, 업로드 차단, 자동 증설 조치/불가)

---

## 아키텍처

```mermaid
flowchart LR
    U([직원 브라우저]) -- "HTTPS 443" --> W

    subgraph W["WebServer · 192.168.0.220 / 56.101"]
      N[Nginx] --> G[Gunicorn<br/>3 workers × 4 threads] --> F[Flask app]
    end

    subgraph FS["FileServer · 56.102"]
      SDB[(sdb → /nfs<br/>XFS + prjquota)]
      SDC[(sdc<br/>iSCSI target)]
    end

    subgraph DB["DBServer · 56.103"]
      M[(MariaDB<br/>/var/lib/mysql)]
      BK[(/dbbackup)]
    end

    subgraph MON["Monitoring · 56.104 / 0.222"]
      P[Prometheus] & L[Loki] & T[Tempo] --> GF[Grafana]
    end

    F -- "NFS 2049<br/>/mnt/nfs/uploads" --> SDB
    F -- "SQL 3306" --> M
    SDC -- "iSCSI 3260 + CHAP" --> DB
    BK -- "rsync (NFS)" --> SDB
    W & FS & DB -. "Alloy Push" .-> MON
    GF -- Webhook --> S([Slack])
```

- **요청 흐름** : 직원 → WebServer(HTTPS 443) → FileServer(NFS 2049) · DBServer(SQL 3306)
- **스토리지 흐름** : DBServer → FileServer(iSCSI 3260)
- **관제 흐름** : 서버 4대 → Monitoring (Alloy Push)

---

## 서버 구성

VirtualBox VM 4대, Rocky Linux 10.1

| 서버 | 호스트명 | 역할 | 서버망 (enp0s8, work 존) | 사용자망 (enp0s10) |
|---|---|---|---|---|
| 웹 | `WebServer` | Nginx → Gunicorn → Flask | 192.168.56.101 | 192.168.0.220 |
| 파일 | `FileServer` | NFS(sdb1 → /nfs), iSCSI 타깃(sdc) | 192.168.56.102 | — |
| DB | `DBServer` | MariaDB, iSCSI 디스크 위 LVM | 192.168.56.103 | — |
| 관제 | `Monitoring` | Prometheus · Loki · Tempo · Grafana · Alloy | 192.168.56.104 | 192.168.0.222 |

- 네트워크 : 서버망 192.168.56.x (enp0s8, 호스트 전용) · 사용자망 192.168.0.x (enp0s10, 브리지) · 인터넷 NAT (enp0s3)
- 서버 간 연동은 **호스트 전용망 192.168.56.x** 로 통일
- 방화벽은 끄지 않고 **rich rule 로 출발지 IP 까지 묶어서** 필요한 포트만 허용
- 프로토콜을 줄이면 열어야 할 포트와 관제할 대상도 줄어든다

| 포트 | 서비스 | 허용 대상 |
|---|---|---|
| 443 / 80 | Nginx | 사용자망 (dmz 존) |
| 2049 · 111 · 20048 | NFS · rpcbind · mountd | 56.101, 56.103 |
| 3260 | iSCSI | 56.103 |
| 3306 | MariaDB | 56.101(앱), 56.104(Grafana 조회) |
| 9090 · 3100 · 4317 | Prometheus · Loki · Tempo | work 존 (Alloy Push) |
| 3000 | Grafana (HTTPS) | 사용자망 (public 존) |
| 22 | SSH | 웹서버(56.101)를 거쳐서만 (점프 호스트) |

---

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| OS / 가상화 | Rocky Linux 10.1, VirtualBox |
| 웹 / WAS | Nginx (TLS 1.2/1.3, JSON 접근 로그), Gunicorn (gthread), Flask 3, PyMySQL |
| 인증서 | 사설 CA `filehub Root CA` → 웹 · Grafana 인증서 서명 (SAN 에 IP 지정) |
| 스토리지 | NFS, iSCSI (targetcli / iscsiadm, CHAP), LVM, XFS 프로젝트 쿼터 |
| DB | MariaDB (utf8mb4), mysqldump + rsync 백업 |
| 관제 | Grafana 13.2.4, Prometheus 3.15, Loki 3.7, Tempo 3.1, Grafana Alloy 1.20 |
| 트레이스 | OpenTelemetry (`opentelemetry-instrument`, 업로드 단계별 수동 span) |
| 알림 | Grafana Alerting → Slack Incoming Webhook (커스텀 템플릿 `jyit`) |

---

## 저장소 설계 (NFS · iSCSI)

| | NFS (폴더 공유) | iSCSI (디스크 공유) |
|---|---|---|
| 파일서버 디스크 | sdb → sdb1 → XFS → `/nfs` | sdc (포맷하지 않음) |
| 파일시스템 주인 | 파일서버 | **DB 서버** (LVM · XFS 를 직접 만듦) |
| 쓰는 곳 | 웹 업로드, DB 백업 사본 | MariaDB 데이터, 덤프 원본 |
| 확장 | 일반 파티션 → 증설 불가 | LVM → 무중단 증설 가능 |

```
FileServer /nfs
├─ share/  → WebServer:/mnt/nfs
│   ├─ uploads/{general,team1,team2,team4,team5,team6}/<UUID>   ← XFS 프로젝트 쿼터 (팀 500M, 공용 2G)
│   └─ .loadtest/
└─ backup/db/  → DBServer:/mnt/nfs   ← mysqldump 사본

FileServer sdc ─ iSCSI target (CHAP) ═══ 3260 ═══▶ DBServer
                                                    └ PV → VG vg_db (100G)
                                                         ├ lv_db      40G → /var/lib/mysql
                                                         ├ lv_backup  30G → /dbbackup
                                                         └ 여유 30G (자동 증설용)
```

- 서비스 계정 `filehub` **UID/GID 2000** 을 웹서버 · 파일서버에 동일하게 (NFS 는 숫자로 권한 판단)
- 디스크 이름(sdb/sdc)은 바뀔 수 있으므로 **UUID · by-id · by-path · IQN** 으로 지정
- 파일서버 `lvm.conf` `global_filter` 로 sdc 제외 → 두 서버가 같은 VG 를 동시에 활성화하는 사고 방지
- 앱 · MariaDB 에 `RequiresMountsFor` → 원격 디스크가 붙기 전에 서비스가 뜨지 않음
- 백업 : `mysqldump → /dbbackup (iSCSI) → rsync → NFS` 매일 03:00, 7일 보관. **원본과 사본이 서로 다른 디스크에**

자세히 → [01. NFS](docs/01-nfs.md) · [02. iSCSI](docs/02-iscsi.md)

---

## 웹 앱 (filehub)

```
브라우저 ─HTTPS─▶ Nginx(:443) ─▶ Gunicorn(127.0.0.1:8000) ─▶ Flask app.py
                                                            ├─ 파일 본문 → /mnt/nfs/uploads/<팀>/<UUID>
                                                            ├─ 메타데이터 → MariaDB (filehub_app 계정)
                                                            ├─ 감사 로그 → journald → Alloy → Loki
                                                            └─ 트레이스 → Alloy(127.0.0.1:4317) → Tempo
```

### 주요 라우트

| 경로 | 설명 |
|---|---|
| `GET /` · `POST /login` · `POST /logout` | 로그인 / 로그아웃 |
| `GET, POST /signup` | 회원가입 (팀 선택, 관리자 승인 대기) |
| `GET /files` | 파일 · 폴더 목록 |
| `POST /upload` | 업로드 (JS 가 파일을 하나씩 전송) |
| `GET /download/<id>` | 다운로드 |
| `POST /files/<id>/replace` · `/delete` | 파일 교체 · 소프트 삭제 |
| `POST /folders` · `/folders/<id>/delete` | 하위 폴더 생성 · 삭제 |
| `GET, POST /admin/users` | 관리자 — 사용자 승인 · 팀 변경 · 잠금 해제 |
| `GET /health` | DB 접속 + NFS 쓰기 점검 (로그인 불필요, 실패 시 503) |

### 업로드 처리와 트레이스 구간

```
quota.check  →  nfs.save  →  db.insert
(DB 한도 확인)   (.part 저장 → rename)   (files INSERT, 실패 시 파일 삭제)
```

| 결과 | 응답 | 감사 로그 |
|---|---|---|
| 폴더 한도(`quota_mb`) 초과 | 413 "폴더 용량 한도" | `UPLOAD FAIL` 용량 한도 초과 |
| 파일서버 XFS 쿼터 초과 (`EDQUOT`/`ENOSPC`) | 507 "디스크 한도 가득 참" | `UPLOAD FAIL` 디스크 쿼터 초과 |
| 정상 | 200 | `UPLOAD SUCCESS` |

### 관리 명령 (`manage.py`)

```bash
cd /opt/filehub/app
sudo -u filehub /opt/filehub/.venv/bin/python manage.py check                          # 설치 점검
sudo -u filehub /opt/filehub/.venv/bin/python manage.py create-user admin "관리자" admin  # 계정 생성
sudo -u filehub /opt/filehub/.venv/bin/python manage.py list-users                     # 계정 목록
sudo -u filehub /opt/filehub/.venv/bin/python manage.py passwd <아이디>                 # 비밀번호 변경
sudo -u filehub /opt/filehub/.venv/bin/python manage.py unlock <아이디>                 # 잠금 해제
```

자세히 → [03. DB서버](docs/03-db.md) · [04. 웹/WAS](docs/04-web.md)

---

## 관제 (Grafana LGTM + Alloy)

| 신호 | 수집 | 저장 | 내용 |
|---|---|---|---|
| **M**etrics | Alloy `prometheus.exporter.unix` (15s) | Prometheus | CPU · 메모리 · 디스크 · I/O |
| **E**vents | Flask 감사 로그 (JSON) | Loki | LOGIN · UPLOAD · ACCOUNT_LOCKED … |
| **L**ogs | messages · secure · Nginx JSON · mariadb.log | Loki | 서버별 `hostname` 라벨 |
| **T**races | OpenTelemetry → Alloy OTLP | Tempo | 서비스 `filehub-web`, 업로드 단계별 span |

| 경보 | 데이터 | 조건 |
|---|---|---|
| 디스크 사용률 80% 초과 | Prometheus | NFS(`/dev/sdb1`) · DB LVM(`vg_db-*`) · OS 디스크로 대상 제한 |
| iSCSI 자동 증설 조치 / 불가 | Loki | `storage-load` 로그의 `action=auto_extend` / `auto_extend_skipped` |
| 폴더 용량 초과 업로드 차단 | Loki | 사용자별 5분간 `UPLOAD FAIL` ≥ 1 |
| 웹 로그인 실패 급증 | Loki | 같은 IP 5분간 `LOGIN FAIL` ≥ 4 |

Slack 메시지는 커스텀 템플릿 `jyit` 으로 **발생/해제 · 대상 · 내용 · 발생 시각 · 대시보드 링크**만 정리해 보냅니다.

자세히 → [05. 관제서버](docs/05-monitoring.md)

---

## 장애 시나리오와 대응

DB 백업 공간 부족, 파일 업로드 지연, 팀 폴더 한도를 넘는 대용량 업로드, 웹 로그인 반복 실패 같은 이상 상황을 가정하고, 서버 4대의 M·E·L·T 데이터로 **조기 감지 → 원인 추적 → 정상 복구**하는 과정을 검증했습니다.

| # | 상황 | 감지 | 원인 분석 | 조치 |
|---|---|---|---|---|
| ① | DB 백업 공간(iSCSI `/dbbackup`) 86% 까지 채움 | Slack *디스크 사용률 80% 초과* | 저장소 사용률 패널, 파일서버 sdc 쓰기 상승 | 85% 2회 연속 → `lv_backup` **+3G 무중단 자동 증설** → Slack *자동 증설 조치* |
| ② | 웹 → 파일서버 NFS 구간을 `tc netem 2mbit` 로 제한, 업로드가 99% 에서 멈춤 | 경보 없음(사용자 신고), Tempo *느린 요청* 패널 | 트레이스 폭포에서 `nfs.save` 만 긺 → NFS 구간 병목 | 제한 해제 후 재업로드 정상 |
| ③ | 600MB 파일을 팀 폴더(한도 500MB)에 업로드 *(참여형)* | 앱이 413 으로 차단 → Slack *업로드 차단* | 감사 로그 `UPLOAD FAIL` 의 사용자 · 이메일 · IP | 이메일로 당사자 연락, 필요 시 앱 한도 + XFS 쿼터 함께 상향 |
| ④ | 팀 계정 비밀번호 반복 실패 *(참여형)* | Slack *웹 로그인 실패 급증* (IP별) | Loki `LOGIN FAIL` 계정 · IP | 5회 연속 실패 시 자동 잠금 → 본인 확인 후 `manage.py unlock` |
| ⑤ | *(선택)* NFS `/nfs` 86% 까지 채움 | Slack *디스크 사용률 80% 초과* | 파일서버 sdb 쓰기 상승 | 일반 파티션이라 증설 불가 → 파일 정리 (①과 대비) |

자세히 → [06. 시연 가이드](docs/06-demo.md)

---

## 공통 준비 (4대 모두)

가장 먼저 합니다. **호스트 이름이 관제의 서버 구분 값(`hostname` · `instance`)이 되므로 Alloy 를 설치하기 전에 끝내 둡니다.**

| 서버 | 호스트 이름 | 서버망 IP (enp0s8, work 존) | 사용자망 IP (enp0s10) |
|---|---|---|---|
| 웹 | WebServer | 192.168.56.101/24 | 192.168.0.220 |
| 파일 | FileServer | 192.168.56.102/24 | 없음 |
| DB | DBServer | 192.168.56.103/24 | 없음 |
| 관제 | Monitoring | 192.168.56.104/24 | 192.168.0.222 |

📍 **각 서버**
```bash
# 호스트 이름 바꾸기 (표의 값)
hostnamectl set-hostname <호스트 이름>

# 서버망(enp0s8)에 고정 IP 지정, 방화벽 존은 work
nmcli connection modify enp0s8 ipv4.method manual ipv4.addresses <서버망 IP> connection.zone work

# 바뀐 설정으로 enp0s8 다시 연결
nmcli connection up enp0s8

# 존 확인 (work 에 enp0s8 이 보이면 정상)
firewall-cmd --get-active-zones
```

| 명령 / 옵션 | 뜻 |
|---|---|
| `hostnamectl set-hostname` | 호스트 이름을 영구 변경. 프롬프트의 이름은 다시 접속(또는 `exec bash`)한 뒤부터 바뀜 |
| `nmcli connection modify enp0s8` | NetworkManager 의 enp0s8 연결 설정 변경 |
| `ipv4.method manual` | DHCP 대신 수동(고정) IP |
| `ipv4.addresses 192.168.56.10x/24` | 고정 IP 와 서브넷 |
| `connection.zone work` | 이 NIC 를 firewalld 의 work 존에 넣음 |
| `nmcli connection up` | 바뀐 설정으로 다시 연결 |

> 다른 서버가 항상 이 주소로 찾아오므로 반드시 고정 IP.  
> 사용자망(브리지)이 있는 서버(WebServer · Monitoring)는 default 경로가 둘이 되지 않게 정리합니다 → [05. 관제서버 1-1)](docs/05-monitoring.md#1-호스트-이름-및-네트워크-설정)

---

## 진행 순서

파트끼리 서로 필요한 것이 있어서, 문서 번호 순서가 아니라 아래 순서로 진행합니다. (명령은 모두 root)

| 단계 | 할 일 | 문서 위치 | 이유 |
|---|---|---|---|
| 1 | 호스트 이름 · 고정 IP | [공통 준비](#공통-준비-4대-모두) (4대 모두) | 모든 연결의 기준 |
| 2 | 파일서버 NFS, 웹서버 마운트 | [01. NFS 1장 · 2장](docs/01-nfs.md) | 웹 업로드 저장소 |
| 3 | iSCSI 타깃 → DB 서버 LVM · `/dbbackup` | [02. iSCSI 1장, 2장 1)~5)](docs/02-iscsi.md) | DB 디스크 준비 |
| 4 | MariaDB · 스키마 · 계정 · 방화벽 | [03. DB서버](docs/03-db.md) 전체 | DB 데이터 생성 |
| 5 | DB 데이터를 iSCSI 로 이동, 자동 증설 | [02. iSCSI 2장 6)~8), 3장](docs/02-iscsi.md#6-mariadb-데이터를-iscsi-디스크로-옮기기) | 4단계의 MariaDB 가 있어야 함 |
| 6 | DB 백업 마운트 · 백업 · 복원 연습 | [01. NFS 3장](docs/01-nfs.md#3-db-서버-19216856103--백업-저장소) | `/dbbackup` 과 filehub DB 가 있어야 함 |
| 7 | 웹/WAS | [04. 웹/WAS](docs/04-web.md) | NFS · DB 계정이 있어야 함 |
| 8 | 관제 (Alloy 는 4대 모두) | [05. 관제서버](docs/05-monitoring.md) | 수집할 대상이 있어야 함 |

**서버 기동 순서** : FileServer → DBServer → WebServer → Monitoring  
(파일서버가 먼저 떠야 iSCSI · NFS 에 의존하는 MariaDB · filehub 앱이 정상 기동)

### 문서 표기 방법

- 📍 **서버이름** : 그 아래 명령을 실행할 서버
- 코드 블록 안 `# 회색 줄` : 바로 아래 명령이 하는 일
- 인용(>) · 목록 : 성공 기준, 이유, 주의할 점
- `<꺾쇠>` : 각자 환경에 맞게 바꿀 값 (꺾쇠까지 지우고 입력)

---

## 디렉터리 구조

```
.
├── README.md
├── app/                      # Flask 웹 앱 → 웹서버 /opt/filehub/app
│   ├── app.py                # 라우트 전체 (로그인, 업로드, 폴더, 관리자, /health)
│   ├── config.py             # 환경변수(.env)에서 설정 읽기
│   ├── db.py                 # 요청마다 MariaDB 연결 1개
│   ├── manage.py             # 관리 CLI (계정 생성 · 잠금 해제 · 점검)
│   ├── requirements.txt      # Flask, gunicorn, PyMySQL
│   ├── templates/            # login · signup · files · admin_users · error ...
│   └── static/               # css · js · img (Nginx 가 직접 서빙)
├── scripts/
│   ├── lv_autoextend.sh      # iSCSI LV 자동 증설 (DBServer, systemd timer 15초)
│   └── storage_load.sh       # 시연용 디스크 · I/O 부하 생성
└── docs/
    ├── 01-nfs.md
    ├── 02-iscsi.md
    ├── 03-db.md
    ├── 04-web.md
    ├── 05-monitoring.md
    └── 06-demo.md
```

---

## 트러블슈팅 요약

| 증상 | 원인 | 해결 |
|---|---|---|
| Grafana 데이터소스 *Missing signature* | SELinux 가 플러그인 소켓(`/run/grafana`) 생성 차단 | `grafana-selinux` 제거, 관제 서버만 permissive |
| `nc` 가 *No route to host* | enp0s8 은 work 존인데 포트를 public 에만 개방 | work 존에 9090 · 3100 · 4317 개방 |
| Tempo 설정 미반영 | `enable --now` 는 실행 중 서비스를 재시작하지 않음 | 설정 변경 후 `systemctl restart` |
| 디스크 경보 15개 동시 Firing | 임계치 0, 감시 대상 미제한 | 임계치 80, `device` 정규식으로 제한 |
| Slack 에 *DatasourceNoData* | 사건이 없으면 Loki 가 빈 결과 반환 | No data 처리 → Normal |
| 로그인 경보가 조용히 0건 | OTel 적용으로 감사 로그 앞에 `[trace_id=…]` 추가 | 줄 안의 `{...}` JSON 을 꺼내는 regexp 로 변경 |
| Nginx 로그 미수집 | 사이트 전용 로그 `filehub_access.log`(JSON) 사용 | Alloy 경로 · 쿼리를 `json` 방식으로 |
| 업로드 "저장소에 쓰지 못했습니다" | 웹 · 파일서버 filehub UID 불일치 | 양쪽 UID 2000 확인 |
| 재부팅 후 iSCSI 타깃 소실 | `targetcli saveconfig` 누락 / target.service 비활성 | 재생성 후 saveconfig, `enable --now target` |
| 502 Bad Gateway | Gunicorn 중단 또는 SELinux | `httpd_can_network_connect` on |

파트별 상세 → 각 문서의 "자주 막히는 곳", [05. 관제서버 3장 트러블슈팅 기록](docs/05-monitoring.md#3-트러블슈팅-기록)

---

## 보안 메모

- 비밀 값(DB 비밀번호, `SECRET_KEY`, CHAP 비밀번호, Slack Webhook URL, 팀 계정 비밀번호)은 **저장소와 문서에 남기지 않습니다.** 앱은 `/opt/filehub/.env`(권한 600)에서 읽고, `.env` 는 `.gitignore` 대상
- 공인 도메인이 없어 사설 CA 를 사용 — PC 에 `ca.crt` 하나만 신뢰 등록하면 웹 · Grafana 모두 경고 없이 접속. `ca.key` 는 웹서버 밖으로 내보내지 않음
- DB 계정은 용도별로 분리하고 접속 IP 를 고정 (`filehub_app@56.101`, 조회 전용 `filehub_grafana@56.104` 는 `password_hash` 칸 제외)
- 파일서버 · DB · 관제 SSH 는 웹서버(56.101)를 거쳐서만 접속, DB 원격 root 제거
- 관제 서버의 SELinux permissive 는 실습 환경 절충안 — 운영이라면 Grafana 용 정책을 별도로 작성
- 시연용 경보 속도(평가 10s, Group wait 5s)는 발표 후 운영값(평가 1m, Pending 1m)으로 복구

---

<sub>Cloud DX Academy 8기 · 1팀 · 2026</sub>
