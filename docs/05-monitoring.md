# 05. 관제서버 (Prometheus · Loki · Tempo · Grafana · Alloy)

> Monitoring · Rocky Linux 10.1 · 내부(Host-Only) 192.168.56.104, 외부(브리지) 192.168.0.222  
> 접속 : Grafana `https://192.168.0.222:3000`, 터미널은 WebServer(192.168.0.220) 접속 후 `ssh root@Monitoring`

[← 04. 웹/WAS](04-web.md) · [README](../README.md) · [06. 시연 가이드 →](06-demo.md)

---

## 목차

- [관제 구성 요약](#관제-구성-요약)
- [1. 환경 구성 및 설정](#1-환경-구성-및-설정)
  - [1) 네트워크 (브리지 경로 정리)](#1-호스트-이름-및-네트워크-설정)
  - [2) 관제 패키지 설치](#2-관제-패키지-설치)
  - [3) 저장소 3종 설정 및 서비스 등록](#3-저장소-3종-설정-및-서비스-등록)
  - [4) 방화벽](#4-방화벽-설정)
  - [5) Alloy 설치 및 서버별 설정](#5-alloy-설치-및-서버별-설정)
  - [6) Grafana 설정](#6-grafana-설정)
- [2. 관제 대시보드 및 경보 구성](#2-관제-대시보드-및-경보-구성)
- [3. 트러블슈팅 기록](#3-트러블슈팅-기록)
- [4. 검증 결과 및 운영 수칙](#4-검증-결과-및-운영-수칙)

---

## 관제 구성 요약

| 구분 | 내용 |
|---|---|
| 수집 | 각 서버의 Alloy 가 15초마다 OS 메트릭은 Prometheus(9090)로, 시스템·보안 로그는 Loki(3100)로 보냄 (Push). WebServer 는 Nginx 접속·에러 로그와 Flask 감사 이벤트(로그인·업로드 등)도 보냄 |
| 트레이스 | Flask(OpenTelemetry) → WebServer Alloy(127.0.0.1:4317) → Tempo(4317) |
| 저장 | Prometheus : `/var/lib/prometheus`, Loki : `/var/lib/loki`, Tempo : `/var/lib/tempo` |
| 조회 | Grafana(3000)가 같은 서버의 세 저장소를 localhost 로 조회 |
| 경보 | 디스크 사용률 80% 초과, 웹 로그인 실패(같은 IP 5분 4회 이상), 폴더 용량 초과 업로드 차단, iSCSI 자동 증설 조치 · 자동 증설 불가 → Slack |
| 설치 방식 | dnf 패키지와 바이너리를 systemd 서비스로 등록 |

> 전제 : 서버 4대(WebServer, FileServer, DBServer, Monitoring)가 호스트 전용망 192.168.56.x 로 서로 연동되고, 관제 서버는 NAT(enp0s3)로 인터넷에 나갈 수 있다고 가정.

---

## 1. 환경 구성 및 설정

### 1) 호스트 이름 및 네트워크 설정

호스트 이름(Monitoring)과 고정 IP(192.168.56.104) 설정은 [README 공통 준비](../README.md#공통-준비-4대-모두)에서 한다. 여기서는 **브리지 경로만 정리**한다.

📍 **Monitoring**
```bash
# 연결 이름과 장치(어댑터) 이름 대응 확인
nmcli -f NAME,DEVICE con show

# 브리지(enp0s10, 연결 이름 Profile 2)의 게이트웨이를 지우고 default 경로로 쓰지 않게 설정
nmcli con mod "Profile 2" ipv4.gateway "" ipv4.never-default yes

# 바뀐 설정으로 Profile 2 연결 다시 활성화
nmcli con up "Profile 2"

# 라우팅 표 확인 (default 가 enp0s3 한 줄만 남아야 함)
ip route
```
```
default via 10.0.2.2 dev enp0s3 proto dhcp src 10.0.2.15 metric 100
10.0.2.0/24 dev enp0s3 proto kernel scope link src 10.0.2.15 metric 100
192.168.0.0/24 dev enp0s10 proto kernel scope link src 192.168.0.222 metric 105
192.168.56.0/24 dev enp0s8 proto kernel scope link src 192.168.56.104 metric 101
```

- default 가 둘이면 나간 길과 돌아오는 길이 달라져(**비대칭 라우팅**) 응답이 끊길 수 있다.
- `ipv4.never-default yes` = 이 연결은 IP 를 받아도 기본 경로(default route)로는 쓰지 않음.
- 성공 기준 : `ip route` 에 `default via 10.0.2.2 dev enp0s3` 한 줄만 남음

```bash
# 내부망 통신 확인 (WebServer)
ping -c 2 192.168.56.101

# 인터넷 연결 확인 (dnf 설치에 필요)
ping -c 2 8.8.8.8
```

### 2) 관제 패키지 설치

#### A. Grafana 공식 저장소 등록 및 Grafana · Alloy · Loki · Tempo 설치

📍 **Monitoring**
```bash
# Grafana Labs 공식 RPM 저장소 등록
cat > /etc/yum.repos.d/grafana.repo <<'EOF'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF
```
> Rocky Linux 10.1 기본 AppStream 에는 구버전(Grafana 10.2.6)만 있어서 공식 저장소가 필요

| 항목 | 뜻 |
|---|---|
| `baseurl` | 패키지를 받아 올 주소 |
| `gpgcheck=1` / `repo_gpgcheck=1` | 패키지와 저장소 메타데이터 서명 검증 |
| `gpgkey` | 서명 확인에 쓸 공개키 |

```bash
# 설치할 패키지 확인 (처음 실행하면 GPG 키 질문이 나오는데 y 입력)
dnf list grafana alloy loki tempo
```
```
Importing GPG key 0x10458545:
 Userid     : "Grafana Labs <engineering@grafana.com>"
 Fingerprint: B53A E77B ADB6 30A6 8304 6005 963F A277 1045 8545
 From       : https://rpm.grafana.com/gpg.key
Is this ok [y/N]: y
Installed Packages
grafana.x86_64      10.2.6-20.el10     @AppStream
Available Packages
alloy.x86_64        1.20.1-1           grafana
grafana.x86_64      13.2.4-1           grafana
loki.x86_64         3.7.8-1            grafana
tempo.x86_64        3.1.0-1            grafana
```
- GPG 키의 Userid 가 Grafana Labs 인지 확인한 뒤 y 입력
- Installed 에 AppStream 의 Grafana 10.2.6 이 이미 깔려 있었음 → 이 상태면 구버전이 남으므로 아래 "AppStream 버전이 먼저 설치된 경우" 절차로 정리

```bash
# Grafana · Alloy · Loki · Tempo 설치
dnf install -y grafana alloy loki tempo
```

| 패키지 | 역할 | 버전 |
|---|---|---|
| grafana | 시각화 · 경보 웹 대시보드 | 13.2.4 |
| alloy | Node Exporter, Promtail, OTel Collector 를 대체하는 통합 에이전트 | 1.20.1 |
| loki | 로그 저장소 | 3.7.8 |
| tempo | 분산 추적(Trace) 저장소 | 3.1.0 |

**AppStream 버전(10.2.6)이 먼저 설치된 경우** : 지우고 공식 저장소만 켜서 재설치

```bash
# 실행 중인 Grafana 중지
systemctl stop grafana-server

# AppStream 으로 설치된 Grafana 관련 패키지 모두 삭제
dnf remove -y "grafana*"

# 남은 Grafana 프로그램·데이터 폴더 삭제 (대시보드·설정도 함께 지워짐)
rm -rf /usr/share/grafana /var/lib/grafana

# dnf 저장소 캐시 비우기
dnf clean all

# 공식 grafana 저장소만 켜서 다시 설치
dnf install -y --disablerepo="*" --enablerepo="grafana" grafana alloy loki tempo

# 설치된 Grafana 의 버전과 배포처 확인
rpm -qi grafana | grep -E "Version|Vendor|Packager"
```
- 성공 기준 : Version 13.2.4, Vendor Grafana Labs
- `/var/lib/grafana` 를 지우면 대시보드와 설정도 사라지므로 **최초 설치 때만** 사용
- `--disablerepo="*" --enablerepo="grafana"` = 이번 설치만 grafana 저장소에서 받음

#### B. Grafana 13 플러그인과 SELinux

Grafana 13.2.4 는 Prometheus · Loki · Tempo 데이터소스를 `/var/lib/grafana/plugins/gpx_*` 별도 프로그램으로 실행하고 `/run/grafana` 소켓으로 통신하는데, Rocky Linux 10 의 SELinux 가 이 소켓 생성을 막아 데이터소스가 **"Missing signature"** 로 비활성화되는 문제가 발생한다.

📍 **Monitoring**
```bash
# 원인 확인
journalctl -u grafana-server --no-pager | grep -i "bind: permission denied"
```

Monitoring 서버만 SELinux permissive 영구 적용 :
```bash
# SELinux 를 바로 permissive 로 전환 (재부팅 전까지만 유지)
setenforce 0

# 충돌을 일으키는 grafana-selinux 정책 패키지 삭제
dnf remove -y grafana-selinux

# 재부팅 후에도 permissive 가 유지되도록 설정 파일 수정
sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config

# 설정 파일 반영 확인 (SELINUX=permissive)
grep "^SELINUX=" /etc/selinux/config

# 플러그인 소켓을 만들 폴더 생성
mkdir -p /run/grafana

# 소켓 폴더와 Grafana 데이터 폴더 소유자를 grafana 계정으로 변경
chown -R grafana:grafana /run/grafana /var/lib/grafana

# 바뀐 설정으로 Grafana 재시작
systemctl restart grafana-server
```

| 모드 | 동작 |
|---|---|
| enforcing | 규칙에 어긋나면 차단 + 기록 |
| permissive | 차단하지 않고 기록만 |
| disabled | SELinux 꺼짐 |

- permissive 는 보안이 약해짐 (실습 환경이라 허용한 절충).
- 운영 환경이라면 차단 기록을 바탕으로 Grafana 용 SELinux 정책을 따로 만드는 것이 맞다.

#### C. Prometheus 바이너리 다운로드

최신 버전을 GitHub 릴리스 주소에서 자동으로 읽어 다운로드한다. (작업 당시 3.15.0)

📍 **Monitoring**
```bash
# 다운로드 작업 폴더(/tmp)로 이동
cd /tmp

# GitHub 최신 릴리스 주소에서 버전 번호만 읽어 VER 변수에 저장
VER=$(curl -sIL -o /dev/null -w '%{url_effective}' https://github.com/prometheus/prometheus/releases/latest | sed 's#.*/tag/v##')

# 버전 확인 (3.15.0 처럼 숫자만 나와야 함)
echo "VER=$VER"

# Prometheus 압축 파일 다운로드
curl -LO https://github.com/prometheus/prometheus/releases/download/v${VER}/prometheus-${VER}.linux-amd64.tar.gz

# 압축 해제
tar xzf prometheus-${VER}.linux-amd64.tar.gz

# 압축 푼 파일 목록 확인
ls prometheus-${VER}.linux-amd64
# LICENSE  NOTICE  prometheus  prometheus.yml  promtool
```

| 부분 | 뜻 |
|---|---|
| `curl -sIL` | 조용히(-s), 헤더만(-I), 리다이렉트 따라감(-L) |
| `-w '%{url_effective}'` | 최종 도착 주소 출력 (`.../tag/v3.15.0`) |
| `sed 's#.*/tag/v##'` | 주소 앞부분을 지우고 버전 숫자만 남김 |
| `curl -LO` | 원래 파일 이름으로 저장 |
| `tar xzf` | gzip 압축(z) 해제(x), 파일(f) 지정 |

> 실행 옵션(`--web.enable-remote-write-receiver`)을 직접 정하기 위해서 RPM 대신 바이너리를 사용

### 3) 저장소 3종 설정 및 서비스 등록

#### A. Prometheus (메트릭, 9090)

📍 **Monitoring**
```bash
# 로그인할 수 없는 Prometheus 전용 계정 생성
useradd --no-create-home --shell /bin/false prometheus

# 설정 폴더와 데이터 저장 폴더 생성
mkdir -p /etc/prometheus /var/lib/prometheus

# 압축 푼 폴더로 이동
cd /tmp/prometheus-${VER}.linux-amd64

# 실행 파일(prometheus, promtool)을 /usr/local/bin 에 복사
cp prometheus promtool /usr/local/bin/

# 기본 설정 파일 복사
cp prometheus.yml /etc/prometheus/

# 설정·데이터 폴더 소유자를 prometheus 계정으로 변경
chown -R prometheus:prometheus /etc/prometheus /var/lib/prometheus

# 실행 파일 소유자를 prometheus 계정으로 변경
chown prometheus:prometheus /usr/local/bin/prometheus /usr/local/bin/promtool

# 복사한 실행 파일의 SELinux 컨텍스트 복구
restorecon -Rv /usr/local/bin/prometheus /usr/local/bin/promtool

# 다운로드 폴더 작업이 끝났으므로 홈 디렉터리로 복귀
cd
```
> `restorecon` : /tmp 에서 복사한 파일은 /tmp 의 라벨을 달고 있어서, 라벨을 복구하지 않으면 서비스가 뜨지 않는다.

```bash
# Prometheus 서비스 파일 작성 (systemd)
cat > /etc/systemd/system/prometheus.service <<'EOF'
[Unit]
Description=Prometheus Time Series Database
Wants=network-online.target
After=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus/ \
  --web.listen-address=0.0.0.0:9090 \
  --web.enable-remote-write-receiver
Restart=always

[Install]
WantedBy=multi-user.target
EOF
```

| 옵션 | 뜻 |
|---|---|
| `--config.file` | 설정 파일 위치 |
| `--storage.tsdb.path` | 시계열 데이터 저장 폴더 |
| `--web.listen-address` | 접속 받을 주소:포트 |
| `--web.enable-remote-write-receiver` | **핵심 옵션.** 각 서버 Alloy 가 메트릭을 직접 밀어 넣을(Push) 수 있게 함. 기본 Prometheus 는 가져오는(Pull) 방식만 씀 |

> ⚠️ 서비스 파일 안 옵션 줄 끝에 `# 설명` 을 붙이면 systemd 가 그대로 인자로 넘겨 Prometheus 가 `unknown long flag` 오류로 시작하지 않는다.

```bash
# systemd 가 새 서비스 파일을 다시 읽도록 함
systemctl daemon-reload

# 9090 포트를 다른 프로그램이 쓰고 있지 않은지 확인 (아무것도 안 나오면 정상)
ss -tlnp | grep ':9090 '
```
> systemd 가 9090 을 잡고 있으면 Cockpit 웹 콘솔(cockpit.socket) → `systemctl disable --now cockpit.socket` 후 진행 (WebServer 에서는 실제로 cockpit.socket 이 9090 을 쓰고 있었음, 관제에는 영향 없음)

```bash
# Prometheus 시작 및 부팅 시 자동 시작
systemctl enable --now prometheus
# Created symlink '/etc/systemd/system/multi-user.target.wants/prometheus.service' → ...
#   ↑ 부팅 시 자동 시작으로 등록됐다는 뜻

# 준비 상태 확인 (Prometheus Server is Ready. 가 나오면 정상)
curl -s localhost:9090/-/ready
```

| `ss` 옵션 | 뜻 |
|---|---|
| `-t` / `-u` | TCP / UDP |
| `-l` | 대기(LISTEN) 중인 것만 |
| `-n` | 이름 대신 숫자 포트 |
| `-p` | 어떤 프로세스인지 |

#### B. Loki (로그, 3100)

📍 **Monitoring**
```bash
# Loki 설정 폴더와 로그 저장 폴더 생성
mkdir -p /etc/loki /var/lib/loki

# Loki 설정 파일 작성 (로컬 디스크 저장, TSDB 스키마 v13)
cat > /etc/loki/config.yml <<'EOF'
auth_enabled: false
server:
  http_listen_port: 3100
  grpc_listen_port: 9096
common:
  instance_addr: 127.0.0.1
  path_prefix: /var/lib/loki
  storage:
    filesystem:
      chunks_directory: /var/lib/loki/chunks
      rules_directory: /var/lib/loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
EOF

# Loki 시작 및 부팅 시 자동 시작
systemctl enable --now loki

# 3100 포트 LISTEN 확인
ss -tulnp | grep 3100
# tcp LISTEN 0 4096 *:3100 *:* users:(("loki",pid=1412,fd=7))
```

| 설정 | 뜻 |
|---|---|
| `auth_enabled: false` | 단일 사용자(테넌트) 모드 |
| `http_listen_port: 3100` | Push 수신 · 조회 포트 |
| `grpc_listen_port: 9096` | 내부 컴포넌트 통신 |
| `storage.filesystem` | 로그 덩어리(chunk)를 로컬 디스크에 저장 |
| `replication_factor: 1` | 복제 없음 (서버 1대) |
| `kvstore: inmemory` | 클러스터 상태를 메모리에만 (단일 서버) |
| `store: tsdb` / `schema: v13` | 현재 권장 인덱스 방식 |
| `period: 24h` | 하루 단위로 인덱스 파일 분리 |

#### C. Tempo (트레이스, 4317 수신 / 3200 조회)

📍 **Monitoring**
```bash
# Tempo 설정 폴더와 트레이스 저장 폴더 생성
mkdir -p /etc/tempo /var/lib/tempo

# Tempo 설정 파일 작성 (4317 수신, 3200 조회)
cat > /etc/tempo/config.yml <<'EOF'
server:
  http_listen_port: 3200
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: "0.0.0.0:4317"
        http:
          endpoint: "0.0.0.0:4318"
storage:
  trace:
    backend: local
    wal:
      path: /var/lib/tempo/wal
    local:
      path: /var/lib/tempo/blocks
EOF

# Tempo 시작 및 부팅 시 자동 시작
systemctl enable --now tempo

# 설정 반영을 위해 재시작
systemctl restart tempo

# 3200 · 4317 포트 LISTEN 확인
ss -tulnp | grep -E '3200|4317'
# tcp LISTEN 0 4096 *:4317 *:* users:(("tempo",pid=1356,fd=12))
# tcp LISTEN 0 4096 *:3200 *:* users:(("tempo",pid=1356,fd=4))
```

| 설정 | 뜻 |
|---|---|
| `http_listen_port: 3200` | Grafana 가 조회하는 포트 |
| `distributor.receivers.otlp` | OpenTelemetry 프로토콜로 트레이스 수신 (gRPC 4317, HTTP 4318) |
| `backend: local` | 로컬 디스크에 저장 |
| `wal` | 받은 트레이스를 블록으로 묶기 전 임시 기록 (Write-Ahead Log) |

- `enable --now` 는 이미 실행 중인 서비스를 재시작하지 않으므로, **설정을 바꾼 뒤에는 반드시 restart.**
- 성공 기준 : 세 서비스 모두 `active (running)`, 포트 LISTEN

### 4) 방화벽 설정

서버 간 통신이 들어오는 enp0s8 은 **work zone**, 브리지·NAT 는 **public zone** 인데, public 에만 포트를 열면 다른 서버의 Alloy 가 연결하지 못한다. 방화벽은 끄지 않고 필요한 포트만 개방한다.

| 포트 | 서비스 | 누가 접속하는가 | zone |
|---|---|---|---|
| 3000 | Grafana | 관리자 브라우저 (브리지 192.168.0.222) | public |
| 9090 | Prometheus | 각 서버 Alloy (Push) | work |
| 3100 | Loki | 각 서버 Alloy (Push) | work |
| 4317 | Tempo OTLP gRPC | WebServer Alloy (Push) | work |
| 3200 | Tempo 조회 | Grafana (localhost) | 외부 포트 불필요 |

📍 **Monitoring**
```bash
# work zone 에 수집 포트 9090 · 3100 · 4317 영구 개방
for p in 9090 3100 4317; do firewall-cmd --permanent --zone=work --add-port=$p/tcp; done

# public zone 에 Grafana 3000 영구 개방
firewall-cmd --permanent --zone=public --add-port=3000/tcp

# 방화벽 설정 다시 읽기
firewall-cmd --reload

# work zone 열린 포트 확인 (9090/tcp 3100/tcp 4317/tcp)
firewall-cmd --zone=work --list-ports

# public zone 열린 포트 확인 (3000/tcp)
firewall-cmd --zone=public --list-ports
```

📍 **WebServer** — 연결 확인
```bash
# Connected to 192.168.56.104:9090 이 나오면 성공 (3100, 4317 도 같은 방법)
nc -zv 192.168.56.104 9090
```
> `nc -z` = 데이터 없이 연결만 시험, `-v` = 결과 출력

### 5) Alloy 설치 및 서버별 설정

- Alloy 는 **서버 4대 모두** 설치해야 한다.
- 서버마다 `hostname` 라벨과 수집하는 로그 경로만 다르고, Push 대상은 192.168.56.104 로 고정한다. (관제 서버 자신은 127.0.0.1)
- `hostname` 라벨은 **대소문자까지 구분**한다. 호스트 이름(WebServer, FileServer, DBServer, Monitoring)과 똑같이 적어야 경보 · 대시보드 쿼리가 맞는다. (적은 값은 Explore → Loki 의 hostname 목록에서 확인)

#### A. 설치 (WebServer, FileServer, DBServer)

📍 **각 서버**
```bash
# 관제 서버와 같은 grafana.repo 등록 후 설치
dnf install -y alloy
```

| 서버 | 수집 로그 (job) | 추가 설정 |
|---|---|---|
| WebServer | `/var/log/messages` (syslog), `/var/log/secure` (security), `/var/log/nginx/filehub_access.log` (nginx), `/var/log/nginx/filehub_error.log` (nginx_error) | OpenTelemetry 수신(127.0.0.1:4317) → Tempo 전달, `tls { insecure = true }` 필수 |
| FileServer | messages (syslog), secure (security) | 없음 |
| DBServer | messages, secure, `/var/log/mariadb/mariadb.log` (database) | 없음 |
| Monitoring | messages, secure | Push 주소를 127.0.0.1 로 (Self-Monitoring) |

#### B. WebServer 설정 예시 (`/etc/alloy/config.alloy`)

📍 **WebServer**
```bash
# OS 메트릭 → Prometheus, 로그 → Loki, Flask 트레이스 → Tempo
cat > /etc/alloy/config.alloy <<'EOF'
prometheus.exporter.unix "self" {}

prometheus.scrape "linux_metrics" {
  targets         = prometheus.exporter.unix.self.targets
  forward_to      = [prometheus.remote_write.prom_remote.receiver]
  scrape_interval = "15s"
}

prometheus.remote_write "prom_remote" {
  endpoint { url = "http://192.168.56.104:9090/api/v1/write" }
}

local.file_match "sys_logs" {
  path_targets = [
    {"__path__" = "/var/log/messages",                 "job" = "syslog",      "hostname" = "WebServer"},
    {"__path__" = "/var/log/secure",                   "job" = "security",    "hostname" = "WebServer"},
    {"__path__" = "/var/log/nginx/filehub_access.log", "job" = "nginx",       "hostname" = "WebServer"},
    {"__path__" = "/var/log/nginx/filehub_error.log",  "job" = "nginx_error", "hostname" = "WebServer"},
  ]
}

loki.source.file "log_scrape" {
  targets    = local.file_match.sys_logs.targets
  forward_to = [loki.write.loki_remote.receiver]
}

loki.write "loki_remote" {
  endpoint { url = "http://192.168.56.104:3100/loki/api/v1/push" }
}

otelcol.receiver.otlp "default" {
  grpc { endpoint = "127.0.0.1:4317" }
  http { endpoint = "127.0.0.1:4318" }
  output { traces = [otelcol.exporter.otlp.tempo_remote.input] }
}

otelcol.exporter.otlp "tempo_remote" {
  client {
    endpoint = "192.168.56.104:4317"
    tls { insecure = true }
  }
}
EOF
```

Alloy 설정은 **블록(컴포넌트)을 파이프처럼 연결**하는 방식이다.

| 블록 | 하는 일 |
|---|---|
| `prometheus.exporter.unix "self"` | 이 서버의 CPU · 메모리 · 디스크 · 네트워크 지표를 만듦 (Node Exporter 역할) |
| `prometheus.scrape` | 위 지표를 15초마다 읽어 `forward_to` 로 넘김 |
| `prometheus.remote_write` | 관제 Prometheus 의 `/api/v1/write` 로 Push |
| `local.file_match` | 수집할 로그 파일 목록과 라벨(`job`, `hostname`) 지정. `__path__` 는 파일 경로 |
| `loki.source.file` | 위 파일들을 tail 하며 새 줄을 읽음 |
| `loki.write` | 관제 Loki 의 `/loki/api/v1/push` 로 Push |
| `otelcol.receiver.otlp` | Flask 가 보내는 트레이스를 127.0.0.1:4317 에서 받음 |
| `otelcol.exporter.otlp` | 받은 트레이스를 관제 Tempo 로 전달. TLS 미사용이므로 `insecure = true` 필수 |

- FileServer · DBServer 는 `otelcol` 블록과 nginx 두 줄을 빼고 hostname 만 바꿔 사용 (DBServer 는 mariadb.log 한 줄 추가 : `{"__path__" = "/var/log/mariadb/mariadb.log", "job" = "database", "hostname" = "DBServer"}`)
- Nginx 기본 `access.log` 는 비어 있고, 사이트 전용 로그 `filehub_access.log` 를 JSON 형식으로 남긴다.

#### Monitoring (Self-Monitoring) 설정

관제 서버의 디스크나 메모리가 고갈되면 전체 관제가 멈추므로 **관제 서버 자신의 메트릭과 로그도 수집**한다. 저장소가 같은 서버에 있어서 Push 주소는 127.0.0.1 이다.

📍 **Monitoring**
```bash
# 관제 서버 자신의 OS 메트릭 → 로컬 Prometheus, 시스템·보안 로그 → 로컬 Loki
cat > /etc/alloy/config.alloy <<'EOF'
// OS 메트릭 -> 같은 서버 Prometheus(9090)
prometheus.exporter.unix "self" {}
prometheus.scrape "linux_metrics" {
  targets         = prometheus.exporter.unix.self.targets
  forward_to      = [prometheus.remote_write.local_prom.receiver]
  scrape_interval = "15s"
}
prometheus.remote_write "local_prom" {
  endpoint { url = "http://127.0.0.1:9090/api/v1/write" }
}

// 시스템·보안 로그 -> 같은 서버 Loki(3100)
local.file_match "sys_logs" {
  path_targets = [
    {"__path__" = "/var/log/messages", "job" = "syslog",   "hostname" = "Monitoring"},
    {"__path__" = "/var/log/secure",   "job" = "security", "hostname" = "Monitoring"},
  ]
}
loki.source.file "log_scrape" {
  targets    = local.file_match.sys_logs.targets
  forward_to = [loki.write.local_loki.receiver]
}
loki.write "local_loki" {
  endpoint { url = "http://127.0.0.1:3100/loki/api/v1/push" }
}
EOF
```
> 다른 서버와 달리 192.168.56.104 가 아니라 127.0.0.1 로 보내고, 블록 이름도 `local_prom` · `local_loki` 로 구분

#### C. root 권한 적용 및 시작 (4대 공통)

`/var/log/messages`, `secure` 는 root 만 읽을 수 있어서 실행 계정을 root 로 변경해야 한다.

📍 **각 서버**
```bash
# Alloy 서비스 설정을 덮어쓸 폴더 생성
mkdir -p /etc/systemd/system/alloy.service.d

# 실행 계정을 root 로 바꾸는 override 파일 작성
cat > /etc/systemd/system/alloy.service.d/override.conf <<'EOF'
[Service]
User=root
Group=root
EOF

# Alloy 설정 파일 문법 검사 (문법 OK 가 나오면 정상)
alloy fmt /etc/alloy/config.alloy > /dev/null && echo "문법 OK"

# 바뀐 서비스 설정 다시 읽기
systemctl daemon-reload

# Alloy 시작 및 부팅 시 자동 시작
systemctl enable --now alloy

# 설정 반영을 위해 재시작
systemctl restart alloy

# 실행 상태 확인
systemctl status alloy --no-pager
```
```
● alloy.service - Vendor-agnostic OpenTelemetry Collector distribution with programmable pipelines
     Loaded: loaded (/usr/lib/systemd/system/alloy.service; enabled; preset: disabled)
    Drop-In: /etc/systemd/system/alloy.service.d
             └─override.conf
     Active: active (running) since Tue 2026-10-20 18:54:00 KST; 1min 11s ago
```
- 성공 기준 : `active (running)`, Drop-In 에 `override.conf` 표시
- 실패하면 `journalctl -u alloy -n 30 --no-pager` 로 확인
- `alloy fmt` = 설정 파일을 정렬해 출력하는 명령. 문법이 틀리면 오류가 나므로 검사용으로 쓴다.

### 6) Grafana 설정

#### A. 가동 및 기본 설정

📍 **Monitoring**
```bash
# Grafana 시작 및 부팅 시 자동 시작
systemctl enable --now grafana-server

# Slack 알림 링크 주소(root_url)를 관제 서버 주소로 지정
sed -i 's|^;root_url = .*|root_url = https://192.168.0.222:3000/|' /etc/grafana/grafana.ini

# root_url 반영 확인
grep '^root_url' /etc/grafana/grafana.ini

# 설정 반영을 위해 재시작
systemctl restart grafana-server
```
- `root_url` 을 지정하지 않으면 Slack 알림 링크가 `localhost:3000` 으로 만들어져 다른 PC 에서 열리지 않는다.
- `grafana.ini` 에서 `;` 로 시작하는 줄은 주석(기본값). sed 로 `;` 를 지우며 값을 바꾼다.

**HTTPS 적용** : 웹서버의 사설 CA([04. 웹/WAS 4장](04-web.md#4-https-인증서))로 Grafana 인증서를 발급해 붙인다.

📍 **WebServer** (`/root/filehub-ca`)
```bash
# Grafana 키와 서명 요청 만들기
openssl req -new -nodes -newkey rsa:2048 -keyout mon.key -out mon.csr -subj "/CN=192.168.0.222"

# 인증서 용도와 접속 주소(SAN) 적기
cat > mon.ext <<'EOF'
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:192.168.0.222,IP:192.168.56.104
EOF

# CA 로 서명해 인증서 발급
openssl x509 -req -in mon.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile mon.ext -out mon.crt

# 관제서버로 보내기
scp mon.crt mon.key root@192.168.56.104:/etc/grafana/
```

📍 **Monitoring**
```bash
# grafana 계정이 읽을 수 있게
chown root:grafana /etc/grafana/mon.crt /etc/grafana/mon.key && chmod 640 /etc/grafana/mon.key

# grafana.ini 의 [server] 구역에서 아래 세 값 지정 (앞의 ; 지우기)
vi /etc/grafana/grafana.ini
```
```ini
[server]
protocol = https
cert_file = /etc/grafana/mon.crt
cert_key = /etc/grafana/mon.key
```
```bash
# 설정 반영을 위해 재시작
systemctl restart grafana-server
```

📍 **WebServer** (`/root/filehub-ca`)
```bash
# HTTPS 응답 확인 (CA 로 검증돼 HTTP/1.1 302 가 나오면 정상)
curl --cacert ca.crt -sI https://192.168.0.222:3000/ | head -1
```
- `root_url` 도 https 여야 Slack 알림의 대시보드 링크가 열린다 (위 sed 명령).
- 웹서버 인증서와 같은 CA 라서 PC 에 `ca.crt` 하나만 등록하면 웹 · Grafana 둘 다 경고 없이 열린다.
- 지금 서버는 `grafana.ini` 77~78줄에 cert_file · cert_key 가 있다. 예전 자체 서명 인증서는 `/etc/grafana/.ssl` 에 보관.

**첫 로그인** : 브라우저에서 `https://192.168.0.222:3000` 접속 → 초기 계정 admin / admin 입력 → 새 비밀번호 설정 화면에서 변경
- 새 비밀번호는 팀 내부에서만 공유
- 접속이 안 되면 Monitoring 의 public zone 에 3000 포트가 열려 있는지 먼저 확인 ([4) 방화벽](#4-방화벽-설정))

#### B. 데이터소스 연결

1. 왼쪽 메뉴 **Connections → Data sources → Add new data source** 를 누른다.
2. 목록에서 Prometheus 를 고르고, Connection 의 Prometheus server URL 에 `http://localhost:9090` 을 입력한다.
3. 맨 아래 **Save & test** 를 눌러 초록색 성공 메시지가 나오는지 확인한다.
4. Loki, Tempo 도 같은 방법으로 아래 URL 을 넣고 Save & test 를 누른다.

| 데이터소스 | URL |
|---|---|
| Prometheus | `http://localhost:9090` |
| Loki | `http://localhost:3100` |
| Tempo | `http://localhost:3200` |
| MySQL (filehub DB) | `192.168.56.103:3306` · Database `filehub` · User `filehub_grafana` |

- Grafana 와 세 저장소가 같은 서버에 있으므로 IP 대신 localhost 로 연결 (외부 포트를 열 필요 없음)
- Tempo 는 수신 포트(4317)가 아니라 **조회 포트(3200)** 로 연결
- MySQL 만 다른 서버(DBServer)라 IP 로 연결. 계정은 [03. DB서버](03-db.md#db-계정과-권한-용도별-분리--최소-권한)에서 만든 조회 전용 `filehub_grafana`, DB 방화벽 3306 이 관제(56.104)에 열려 있어야 함
- Loki 데이터소스가 provisioning 파일(`/etc/grafana/provisioning/datasources/`)로 만들어진 경우 UI 에서 저장하면 403 오류 → 파일을 고친 뒤 grafana-server 재시작

📍 **Monitoring** — 데이터소스 연결 전에 각 저장소가 응답하는지 확인
```bash
# Prometheus Server is Ready. 가 나오면 정상
curl -s localhost:9090/-/ready

# Loki 준비 상태 확인 (ready)
curl -s localhost:3100/ready

# Tempo 준비 상태 확인 (ready)
curl -s localhost:3200/ready
```

데이터소스를 연결한 뒤에는 데이터가 실제로 들어오는지 바로 확인한다.

1. **로그 확인** : Explore → Loki 선택 → Label filters 에서 `hostname = Monitoring` → Run query 를 눌러 `/var/log/messages` 와 `/var/log/secure` 로그가 실시간으로 찍히는지 확인. 다른 서버는 Alloy 를 배포한 뒤 hostname 만 바꿔 같은 방법으로 본다.
2. **메트릭 확인** : Explore → Prometheus 에서 `up` 을 실행해 instance 가 4대(WebServer, FileServer, DBServer, Monitoring) 모두 1 로 나오는지 확인.
3. **기본 대시보드** : Dashboards → New → Import → Import via grafana.com 칸에 **1860**(Node Exporter Full) 입력 → Load → Prometheus 데이터소스 선택 → Import 를 누르면 서버별 CPU · Memory · Disk · Network 계기판이 바로 보인다.

> 1860 대시보드 Nodename 목록에 서버가 빠져 있으면 [트러블슈팅 (13)](#13-1860-대시보드-nodename-목록에서-일부-서버가-빠짐) 참고

#### C. Slack Incoming Webhook URL 받기

Grafana 가 Slack 채널로 경보를 보내려면 그 채널 전용 주소(Webhook URL)가 필요하다. Slack 웹사이트에서 앱을 하나 만들고 Incoming Webhooks 기능을 켜면 받을 수 있다.

1. Slack 워크스페이스에 경보를 받을 채널을 만든다. (예 : `#jyit-alert`)
2. 브라우저에서 https://api.slack.com/apps 에 접속해 같은 워크스페이스 계정으로 로그인한다.
3. **Create New App → From scratch** 를 고르고, App Name(예 : JYIT 관제)과 워크스페이스를 정한 뒤 Create App 을 누른다.
4. 왼쪽 메뉴 **Features → Incoming Webhooks** 로 들어가 Activate Incoming Webhooks 를 On 으로 바꾼다.
5. 페이지 아래 **Add New Webhook (to Workspace)** 를 누르고, 1번에서 만든 채널을 고른 뒤 허용(Allow)을 누른다.
6. Webhook URL 목록에 `https://hooks.slack.com/services/T.../B.../...` 형태의 주소가 생기면 Copy 로 복사한다.

> ⚠️ Webhook URL 을 아는 사람은 누구나 그 채널에 메시지를 보낼 수 있으므로 **비밀번호처럼 다룬다** (문서·캡처·깃에 남기면 안 됨). 주소가 노출되면 같은 화면에서 해당 Webhook 을 삭제(Revoke)하고 새로 발급.

📍 **Monitoring**
```bash
# 관제 서버에서 Slack 으로 나가는지 먼저 확인 (실제 URL 을 작은따옴표로 감싸서 입력, 성공하면 ok)
curl -X POST -H 'Content-type: application/json' --data '{"text":"관제 서버 연결 테스트"}' 'https://hooks.slack.com/services/...'
```

| 응답 | 뜻 |
|---|---|
| `ok` | 정상, 채널에 메시지가 도착함 |
| `no_service` / `invalid_token` | URL 을 잘못 붙여 넣었거나 이미 삭제된 Webhook |
| 아무 응답 없이 멈춤 | 관제 서버가 인터넷에 나가지 못함 (`ping -c 2 8.8.8.8`, `ip route` 확인) |

#### D. Grafana Contact point 만들기 (Slack 연결)

1. 왼쪽 메뉴 **Alerting → Contact points → + Add contact point** 를 누른다.
2. Name 에 `slack-dz`, Integration 에 Slack 을 고르고 Webhook URL 칸에 C 에서 복사한 주소를 붙여 넣는다.
3. **Test → Send test notification** 을 눌러 Slack 채널에 테스트 메시지가 오는지 확인한다.
4. **Save contact point** 를 눌러 저장한다.
5. **Alerting → Notification policies** 에서 Default policy 오른쪽 **⋯ More → Edit** 를 누른다.
6. Default contact point 를 `slack-dz` 로 바꾸고 **Update default policy** 를 누른다.

- **Notification policy** 는 "어떤 경보를 어느 Contact point 로 보낼지" 정하는 규칙. Default policy 에 걸어 두면 모든 경보가 Slack 으로 간다.
- 경보가 울렸는데 Slack 이 조용하면 이 Default contact point 가 `slack-dz` 인지 먼저 확인.

#### E. 알림 템플릿 `jyit` 만들기 (Slack 메시지 모양)

Grafana 기본 Slack 메시지는 영어 라벨이 길게 나열되어 한눈에 읽기 어렵다. 템플릿을 만들면 발생·해제, 대상, 내용, 발생 시각, 대시보드 링크만 정리해서 보낼 수 있다.

1. **Alerting → Contact points → Notification Templates** 탭 → **+ Add notification template group** 을 누른다.
2. Name 에 `jyit` 을 입력하고 아래 내용을 Template 칸에 붙여 넣은 뒤 Save 한다.
3. Contact point `slack-dz` → Edit → **Optional Slack settings** 를 펼친다.
4. Title 에 `{{ template "jyit.title" . }}`, Text Body 에 `{{ template "jyit.text" . }}` 를 넣고 Save contact point 를 누른다.
5. 경보 규칙의 Summary 는 "내용", Description 은 "확인 방법" 줄로 들어가므로 규칙마다 적어 둔다.

```gotemplate
{{ define "jyit.title" -}}
{{ if gt (len .Alerts.Firing) 0 }}[경보 {{ len .Alerts.Firing }}건]{{ else }}[해제]{{ end }} {{ .CommonLabels.alertname }}
{{- end }}

{{ define "jyit.target" -}}
{{- if .Labels.mountpoint }}{{ .Labels.instance }} 서버 {{ .Labels.mountpoint }}
{{- else if .Labels.ip }}접속 IP {{ .Labels.ip }}
{{- else if .Labels.user }}사용자 {{ .Labels.user }}
{{- else if .Labels.instance }}{{ .Labels.instance }} 서버
{{- else }}-{{ end }}
{{- end }}

{{ define "jyit.text" -}}
{{ range .Alerts.Firing -}}
:red_circle: *발생* | {{ .Labels.alertname }}
• 대상: {{ template "jyit.target" . }}
• 내용: {{ if .Annotations.summary }}{{ .Annotations.summary }}{{ else }}-{{ end }}
{{ if .Annotations.description }}• 확인 방법: {{ .Annotations.description }}
{{ end }}• 발생 시각: {{ .StartsAt.Local.Format "2006-01-02 15:04:05" }}
• 대시보드: https://192.168.0.222:3000/d/jyit-melt
{{ end -}}
{{ range .Alerts.Resolved -}}
:large_green_circle: *해제* | {{ .Labels.alertname }}
• 대상: {{ template "jyit.target" . }}
• 기간: {{ .StartsAt.Local.Format "15:04:05" }} ~ {{ .EndsAt.Local.Format "15:04:05" }}
{{ end -}}
{{- end }}
```

| 템플릿 | 하는 일 |
|---|---|
| `jyit.title` | 울리는 경보가 있으면 `[경보 N건]`, 모두 풀리면 `[해제]` + 경보 이름 |
| `jyit.target` | 경보 라벨을 보고 대상을 고름 (디스크 경보는 서버+마운트, 로그인 경보는 접속 IP, 업로드 경보는 사용자) |
| `jyit.text` | 발생 건마다 빨간 원, 해제 건마다 초록 원과 발생~해제 시각을 표시 |

- `{{- ` / ` -}}` = 앞/뒤 공백·줄바꿈을 지움 (메시지에 빈 줄이 생기지 않게)
- `.StartsAt.Local` : 서버 시간대(KST)로 표시. `2006-01-02 15:04:05` 는 Go 언어의 날짜 형식 기준값

#### F. 경보 규칙 만드는 순서 (공통)

디스크 · 웹 로그인 · 업로드 차단 · 자동 증설 경보 모두 같은 화면에서 만든다. 규칙별 쿼리와 값은 [2장 2) 경보 규칙](#2-경보-규칙)에 정리했다.

1. **Alerting → Alert rules → + New alert rule** 을 누른다.
2. **Enter alert rule name** : 규칙 이름을 적는다. (예 : 디스크 사용률 80% 초과) 이 이름이 Slack 메시지 제목에 들어간다.
3. **Define query and alert condition** : 데이터소스(prometheus 또는 loki)를 고르고 Code 모드로 바꿔 쿼리를 붙여 넣는다.
4. **Expressions** : Reduce(B)는 Function Last, Threshold(C)는 입력 B 에 Is above 값을 넣고 Set as alert condition 을 누른다.
5. **Preview** 를 눌러 지금 상태가 Normal 인지, 대상이 몇 개인지 확인한다. (대상이 너무 많으면 쿼리 범위를 다시 좁힘)
6. **Add folder and labels** : 폴더 `DZ-company` 를 고른다.
7. **Set evaluation behavior** : 평가 그룹(evaluation group)을 고르거나 새로 만들어 평가 간격(시연용 10s)을 정하고, Pending period 를 정한다.
8. **Configure no data and error handling** : Alert state if no data 를 **Normal** 로 바꾼다. (Loki 쿼리는 사건이 없으면 빈 결과가 나오기 때문)
9. **Configure notifications** : Contact point 로 `slack-dz` 를 고른다.
10. **Configure notification message** : Summary 와 Description 을 적는다.
11. 오른쪽 위 **Save rule and exit** 를 누른다. (Save 만 누르고 나가면 바뀐 값이 반영되지 않은 적이 있음)

| 용어 | 뜻 |
|---|---|
| Reduce (Last) | 시계열을 마지막 값 하나로 줄임 |
| Threshold | 그 값이 기준을 넘는지 판단 |
| Evaluation interval | 규칙을 몇 초마다 검사할지 |
| Pending period | 조건이 이 시간만큼 계속돼야 Firing. 시연용으로 디스크 0s, 웹 로그인 None(둘 다 바로 알림). 운영이라면 디스크는 1m 이상 권장(잠깐 튀는 값 무시) |

- 임계치를 낮춰 시험한 뒤에는 반드시 원래 값으로 되돌리고 Save rule and exit 까지 눌러 저장
- 시연용 알림 속도 조정(평가 10s, Group wait 5s 등)은 [06. 시연 가이드 2장](06-demo.md#2-시연용-설정-경보-속도)에 모았다.

---

## 2. 관제 대시보드 및 경보 구성

### 1) 통합 관제 대시보드 (JYIT 통합 관제 M·E·L·T)

파일 `jyit-melt-dashboard.json`(uid `jyit-melt`)을 **Dashboards → New → Import** 로 가져온다.

| 줄 | 패널 | 보는 것 |
|---|---|---|
| M 서버 | 수집 상태, OS 디스크, CPU · 메모리, 디스크 쓰기 | 서버 4대 Alloy 생존, 자원 사용률 |
| M 저장소 | 저장소 연결 상태, 사용률 · 남은 용량, 파일서버 sdb/sdc I/O, 저장소 작업 로그 | NFS(/nfs)와 iSCSI(/var/lib/mysql, /dbbackup)를 한 화면에 |
| E | 현재 경보, 웹 로그인 실패(IP별), 웹 오류 응답, 감사 이벤트 그래프 · 목록 | 누가, 언제, 무엇을 했는지 (파일 크기 KB · MB · GB 자동 표시, trace_id 포함) |
| L | 웹 요청 수, 응답 시간, Nginx 접속 로그, 에러 로그, 웹 앱 로그 | 500 오류의 원인(Traceback) |
| T | 최근 트레이스 (Flask), 느린 요청(2초 이상) | filehub-web 요청별 처리 경로와 시간, 업로드 단계(quota.check · nfs.save · db.insert)별 시간 |

- iSCSI 디스크(sdc)는 FileServer 에 있지만 사용률은 이 디스크를 LVM 으로 쓰는 **DBServer 에서** 읽는다.
- 대시보드 수정은 UI 에서 하고 Export 로 백업 (JSON Import/Overwrite 는 팀원 수정 사항을 덮어씀)

### 2) 경보 규칙

#### A. 디스크 사용률 80% 초과 (시나리오 ①)

```promql
# Query A (Prometheus) : 감시 대상을 NFS, DB LVM, OS 디스크로 제한
100 - (node_filesystem_avail_bytes{device=~"/dev/sdb1|/dev/sda2|/dev/mapper/vg_db-.*"}
     / node_filesystem_size_bytes{device=~"/dev/sdb1|/dev/sda2|/dev/mapper/vg_db-.*"} * 100)
```

| 항목 | 값 |
|---|---|
| 조건 | Reduce Last → Threshold Is above 80 |
| 평가 간격 / Pending | 10s / 0s (시연용) |
| Summary | `{{ $labels.instance }} {{ $labels.mountpoint }} 디스크 사용률 80% 초과` |

- `avail / size * 100` = 남은 비율 → 100 에서 빼면 사용률(%)
- `device=~"a|b|c"` = 정규식으로 장치 이름이 셋 중 하나인 것만
- 대상을 제한하지 않으면 /DATA, /BACKUP 등 15개 파티션이 한꺼번에 경보 대상이 된다.

#### B. iSCSI 자동 증설 조치 · 자동 증설 불가 (시나리오 ①)

```logql
# Query (Loki) : 1분 안에 자동 증설이 일어난 마운트
sum by (instance, mountpoint) (count_over_time({job="syslog", hostname=~"(?i)dbserver"}
  |= "storage-load" |= "action=auto_extend" != "action_failed"
  | regexp `mount=(?P<mountpoint>\S+)`
  | label_format instance="DBServer" [1m]))

# Query (Loki) : 1분 안에 자동 증설을 건너뛴 마운트 (상한 도달 · VG 여유 부족 → 수동 조치)
sum by (instance, mountpoint) (count_over_time({job="syslog", hostname=~"(?i)dbserver"}
  |= "storage-load" |= "event=auto_extend_skipped"
  | regexp `mount=(?P<mountpoint>\S+)`
  | label_format instance="DBServer" [1m]))
```

| LogQL 부분 | 뜻 |
|---|---|
| `{job="syslog", hostname=~"(?i)dbserver"}` | 라벨로 로그 흐름 선택. `(?i)` = 대소문자 무시 |
| `\|= "문자열"` / `!= "문자열"` | 그 문자열을 포함하는 / 포함하지 않는 줄만 |
| `\| regexp` | 정규식 이름 그룹(`?P<mountpoint>`)을 라벨로 뽑아냄 |
| `\| label_format` | 라벨 값을 새로 지정 |
| `count_over_time([1m])` | 1분 동안의 줄 수 |
| `sum by (...)` | 지정한 라벨별로 합계 |

- 조건 : Reduce Last → Threshold Is above 0, Pending None, No data 처리 Normal (사건이 없으면 빈 결과)
- Summary 예 : 자동 증설 조치 = `{{ $labels.instance }} {{ $labels.mountpoint }} 3G 자동 증설 완료` / 자동 증설 불가 = `{{ $labels.mountpoint }} 자동 증설 불가 - 수동 조치 필요`
- 라벨 이름을 `mountpoint` · `instance` 로 맞춰 두면 알림 템플릿 `jyit.target` 이 "DBServer 서버 /dbbackup" 으로 표시한다.

#### C. 폴더 용량 초과 업로드 차단 (시나리오 ③)

```logql
# Query (Loki) : 사용자별 5분간 업로드 차단(UPLOAD FAIL) 횟수
sum by (user) (count_over_time({job="syslog", hostname="WebServer"} |= "filehub.audit"
  | regexp `(?P<audit>\{.*\})` | line_format "{{.audit}}" | json
  | action="UPLOAD" | result="FAIL" [5m]))
```

| 항목 | 값 |
|---|---|
| 조건 | Reduce Last → Threshold Is above 0 (1회 이상) |
| Pending period | None (즉시) |
| No data 처리 | Normal (차단이 없으면 빈 결과) |
| Summary | `{{ $labels.user }} 폴더 용량 초과 업로드 차단 (최근 5분 {{ $values.B }}회)` |

- `regexp (?P<audit>\{.*\})` → `line_format "{{.audit}}"` → `json` : 줄에서 `{...}` JSON 부분만 꺼내 그 칸들을 라벨로 만든다.
- 앱은 팀 폴더 한도(DB `folders.quota_mb`, 팀 500MB)를 넘는 업로드를 저장하지 않고 413 으로 거절하며 감사 로그에 `UPLOAD FAIL` 을 남긴다 ([04. 웹/WAS 8장](04-web.md#8-앱-동작-설정-apppy-안의-값)).
- 경보에 이메일까지 띄우려면 `sum by (user, email)` 로 바꾼다 (감사 로그 JSON 에 `email` 칸이 있음).
- 한도 초과만 세려면 `| detail=~"용량 한도.*"` 필터를 더한다.
- 감사 로그 형식이 바뀌어 0건이 되면 규칙이 조용히 멈추므로, 리허설 때 한 번 차단시켜 Slack 이 오는지 본다.

#### D. 웹 로그인 실패 급증 (시나리오 ④)

```logql
# Query (Loki) : 같은 IP 에서 5분간 로그인 실패 횟수
sum by (ip) (count_over_time({job="syslog", hostname="WebServer"} |= "filehub.audit"
  | regexp `trace_id=(?P<trace_id>[0-9a-f]{32})`
  | regexp `(?P<audit>\{.*\})` | line_format "{{.audit}}" | json
  | action="LOGIN" | result="FAIL" [5m]))
```

| 항목 | 값 |
|---|---|
| 조건 | Reduce Last → Threshold Is above 3 (4회 이상) |
| Pending period | None (즉시) |
| No data 처리 | Normal (실패가 없으면 빈 결과가 나와 DatasourceNoData 경보가 오는 것 방지) |
| Summary | `{{ $labels.ip }}에서 웹 로그인 5분간 {{ $values.B }}회 실패` |

감사 이벤트 로그 형식 (OpenTelemetry 적용 후) :
```
WebServer opentelemetry-instrument[pid]: ... INFO [filehub.audit] [app.py:36] [trace_id=<32자리> ...] - {"action": "LOGIN", "result": "FAIL", "user": "...", "ip": "...", "detail": "..."}
```
- 처음 쿼리는 `filehub.audit` 바로 뒤의 JSON 만 읽어서, 형식이 바뀐 뒤 경보가 오류 없이 0건이 됨
- 지금 쿼리는 줄 어디에 있든 `{...}` JSON 을 꺼내므로 예전 · 새 형식을 모두 읽음

#### E. 보류 경보 (쿼리만 준비)

```logql
# (참고) 대용량 업로드 성공 합계 : 사용자별 10분 업로드 합계(바이트)
sum by (user) (sum_over_time({job="syslog", hostname="WebServer"} |= "filehub.audit"
  | regexp `(?P<audit>\{.*\})` | line_format "{{.audit}}" | json
  | action="UPLOAD" | result="SUCCESS"
  | regexp `(?P<bytes>[0-9]+) bytes` | unwrap bytes [10m]))

# 계정 잠금 : 5분 안에 ACCOUNT_LOCKED 이벤트 발생
sum by (user) (count_over_time({job="syslog", hostname="WebServer"} |= "filehub.audit"
  | regexp `(?P<audit>\{.*\})` | line_format "{{.audit}}" | json
  | action="ACCOUNT_LOCKED" [5m]))
```
> `unwrap bytes` = 라벨 값을 숫자로 꺼내 `sum_over_time` 으로 합산

### 3) 트레이스 확인 (Tempo)

```traceql
# Explore → Tempo → TraceQL (주기적인 /metrics 요청 제외)
{ resource.service.name = "filehub-web" && span.http.route != "/metrics" }

# 느린 요청(2초 이상)만 (대시보드 T줄 패널, 시나리오 ②)
{ resource.service.name = "filehub-web" && duration > 2s }
```
- 트레이스를 열면 업로드 단계 `quota.check` · `nfs.save` · `db.insert` 가 폭포 그림으로 나온다. **가장 긴 막대가 병목.**
- 감사 이벤트의 trace_id → 트레이스 링크(Loki derived field)는 Loki 데이터소스가 provisioning 파일이라 UI 저장 불가(403). `/etc/grafana/provisioning/datasources/` 의 Loki 파일 `jsonData` 에 `derivedFields` 추가 후 grafana-server 재시작 필요 (미완료)

---

## 3. 트러블슈팅 기록

| 증상 | 원인 | 해결 |
|---|---|---|
| Grafana 데이터소스가 Missing signature 로 비활성화 | SELinux 가 플러그인 소켓(/run/grafana) 생성을 차단 | grafana-selinux 제거, SELinux permissive |
| tar 가 not in gzip format | VER=3.x.x 자리표시를 그대로 입력해 "Not Found" 파일을 받음 | releases/latest 에서 버전을 자동으로 읽음 |
| bash: syntax error near unexpected token newline | `<Webhook URL>` 의 꺾쇠를 bash 가 리디렉션으로 읽음 | 꺾쇠를 지우고 URL 을 작은따옴표로 감쌈 |
| nc 가 No route to host | enp0s8 은 work zone 인데 9090 이 public 에만 열림 | work zone 에 9090 · 3100 · 4317 개방 |
| nc 10.10.10.14 9090 이 TIMEOUT | 10.10.10.x 내부망을 쓰지 않음 (enp0s9 제거) | 연동 주소를 192.168.56.104 로 통일 |
| Tempo 설정이 반영되지 않음 (Active: 9h ago) | enable --now 는 실행 중인 서비스를 재시작하지 않음 | 설정 변경 후 systemctl restart |
| 노션에서 복사한 명령이 실패 | 마크다운 변환으로 `--`, `__path__`, `v1` 이 바뀜 | 코드 블록으로 복사, 붙여 넣은 뒤 확인 |
| 디스크 경보 미리보기에서 15개 모두 Firing | 임계치 0, 감시 대상 미제한 | 임계치 80, device 정규식으로 대상 제한 |
| Nginx 웹 로그가 안 잡힘 | 사이트가 전용 로그 filehub_access.log(JSON)를 사용 | Alloy 경로와 대시보드 쿼리를 json 방식으로 변경 |
| Slack 에 DatasourceNoData 경보 | 실패가 없으면 Loki 가 빈 결과를 돌려줌 | No data 처리를 Normal 로 |
| 웹 로그인 경보가 조용히 멈춤 | OTel 적용으로 감사 로그 형식이 바뀜 | 두 형식 모두 읽는 쿼리로 수정 |
| 20:30 에 웹 요청이 한꺼번에 몰려 보임 | Alloy 첫 실행 때 기존 로그를 한 번에 읽음 | 1회성, 실제 시각은 로그 안의 time 값 |
| 1860 대시보드 Nodename 목록에 서버 누락 | Alloy 재시도 간격 증가, Grafana 변수 캐시 | systemctl restart alloy 후 Ctrl+F5 |
| Loki 로그에 context canceled / EOF 반복 | Grafana 가 이전 쿼리를 취소할 때 남는 내부 기록 | 영향 없음, 무시 |
| Tempo 가 기동 실패 | Tempo 3.1.0 설정에 ingester 구역을 넣음 | ingester 설정을 빼고 원복 |
| Slack 알림의 대시보드 링크가 안 열림 | Grafana 를 HTTPS 로 바꿨는데 root_url · 템플릿 링크가 http | 둘 다 https://192.168.0.222:3000 으로 |

아래는 문제마다 증상 · 원인 · 확인 방법 · 해결을 자세히 적은 기록이다. 처음 설치하던 단계의 문제부터 운영 중에 찾은 문제 순서.

### (1) Grafana 데이터소스가 Missing signature 로 비활성화

- **증상** : Grafana 에 접속하면 Prometheus · Loki · Tempo 데이터소스가 "Unsigned plugins / Missing signature" 경고와 함께 목록에서 사라짐
- **원인** : Grafana 13.2.4 는 데이터소스를 본체에 넣지 않고 `/var/lib/grafana/plugins/gpx_*` 별도 프로그램으로 실행하며 `/run/grafana` 소켓으로 통신함. Rocky Linux 10 의 SELinux 가 이 소켓 생성(bind)을 막아 플러그인이 바로 죽었고, Grafana 화면에는 엉뚱하게 서명 오류로 표시됨
- **해결** : grafana-selinux 정책 패키지를 지우고 SELinux 를 permissive 로 영구 설정한 뒤 재시작 ([1장 2)-B](#b-grafana-13-플러그인과-selinux) 참고)

```bash
# Grafana 로그에서 진짜 원인 찾기 (bind: permission denied 가 보이면 SELinux 차단)
journalctl -u grafana-server --no-pager | grep -i "bind: permission denied"

# SELinux 를 잠시 끄고 재시작해 데이터소스가 돌아오는지로 원인 확정
setenforce 0; systemctl restart grafana-server

# 현재 SELinux 모드 확인 (Permissive 가 나와야 함)
getenforce
```
- 처음에는 서명 검증 옵션(`allow_loading_unsigned_plugins`)이나 AppStream 구버전 문제로 보고 grafana.ini 수정과 재설치를 했지만 해결되지 않음
- **화면의 오류 문구보다 서비스 로그(journalctl)를 먼저 보는 것이 빠름**

### (2) Prometheus 압축 해제 시 tar: not in gzip format

- **증상** : `tar xzf prometheus-....tar.gz` 실행 시 gzip 형식이 아니라는 오류
- **원인** : 버전 자리에 `VER=3.x.x` 자리표시를 그대로 넣어 GitHub 가 9바이트짜리 "Not Found" 문서를 내려줌. 파일 이름만 tar.gz 이고 내용은 글자였음
- **해결** : releases/latest 주소에서 최신 버전 번호를 자동으로 읽어 VER 에 넣고, echo 로 숫자만 나오는지 확인한 뒤 다시 다운로드

```bash
# 받은 파일 크기 확인 (수 바이트면 실패한 다운로드)
ls -l /tmp/prometheus-*.tar.gz

# 파일 종류 확인 (gzip compressed data 가 아니라 ASCII text 면 실패)
file /tmp/prometheus-*.tar.gz

# 버전 변수 확인 (3.15.0 처럼 숫자만 나와야 함)
echo "VER=$VER"
```

### (3) bash: syntax error near unexpected token newline

- **증상** : Slack Webhook 시험 curl 명령을 실행하자마자 문법 오류
- **원인** : 명령 예시의 `<Webhook URL>` 자리표시를 그대로 붙여 넣어, bash 가 `<` `>` 를 입력·출력 리디렉션 기호로 읽음
- **해결** : 자리표시와 꺾쇠를 모두 지우고 실제 URL 을 작은따옴표로 감싸서 입력

```bash
# 올바른 형식 (URL 전체를 작은따옴표로 감쌈)
curl -X POST -H 'Content-type: application/json' --data '{"text":"test"}' 'https://hooks.slack.com/services/...'
```
> 문서의 `<...>` 는 "여기에 실제 값을 넣으라"는 표시이므로 복사 후 반드시 바꿔서 실행

### (4) WebServer 에서 nc 가 No route to host

- **증상** : WebServer 에서 `ping 192.168.56.104` 는 되는데 `nc -zv 192.168.56.104 9090` 은 No route to host
- **원인** : 관제 서버의 enp0s8(192.168.56.104)은 work zone 인데 9090 포트를 public zone 에만 열었음. firewalld 가 패킷을 거부하면서 ICMP "administratively prohibited" 응답을 보내고, nc 가 이것을 No route to host 라는 문구로 표시함. **이름과 달리 라우팅 문제가 아니라 방화벽 문제**
- **해결** : work zone 에 9090 · 3100 · 4317 을 열고 다시 읽기

```bash
# 📍 Monitoring — 어느 어댑터가 어느 zone 인지 확인 (enp0s8 → work)
firewall-cmd --get-active-zones

# work zone 에 열린 포트 확인
firewall-cmd --zone=work --list-ports

# work zone 에 수집 포트 개방
for p in 9090 3100 4317; do firewall-cmd --permanent --zone=work --add-port=$p/tcp; done

# 방화벽 설정 다시 읽기
firewall-cmd --reload

# 📍 WebServer — 다시 확인 (Connected 가 나오면 해결)
nc -zv 192.168.56.104 9090
```
> ping 은 되는데 특정 포트만 안 되면 라우팅보다 방화벽 zone 을 먼저 확인

### (5) `nc -zv 10.10.10.14 9090` 이 TIMEOUT

- **증상** : 팀 문서 초안의 10.10.10.14 주소로 연결하면 응답 없이 시간 초과
- **원인** : 10.10.10.x 내부망은 쓰지 않기로 하고 어댑터(enp0s9)를 비활성화(이후 제거)해서 그 주소로는 아무도 응답하지 않음
- **해결** : 서버 간 연동 주소(Alloy Push 대상, nc 확인 등)를 모두 호스트 전용망 192.168.56.104 로 통일

```bash
# 관제 서버에 실제로 붙어 있는 주소 확인
ip -br addr
```

### (6) Tempo 설정을 바꿨는데 반영되지 않음

- **증상** : `/etc/tempo/config.yml` 을 고치고 `systemctl enable --now tempo` 를 실행했지만 이전 설정대로 동작, status 에 `Active: active (running) since ... 9h ago`
- **원인** : `enable --now` 는 꺼져 있는 서비스만 새로 시작하고, 이미 실행 중인 서비스는 재시작하지 않음. 9시간 전부터 기본 설정으로 떠 있던 프로세스가 그대로 남아 있었음
- **해결** : 설정 파일을 바꾼 뒤에는 `systemctl restart` 로 재시작

```bash
# 언제부터 실행 중인지 확인 (since 시각이 설정 변경 전이면 미반영)
systemctl status tempo --no-pager | grep Active

# 설정 반영을 위해 재시작
systemctl restart tempo
```
> Prometheus · Loki · Alloy · Grafana 모두 같은 원리. **설정 변경 → restart → status 확인** 순서를 지킴

### (7) 노션에서 복사한 명령 · 설정이 실패

- **증상** : `systemctl enable-now`, `/api/vl/write`, `_path` 처럼 원래와 다른 글자가 들어가 명령 오류나 데이터 미수집
- **원인** : 명령을 코드 블록 없이 노션에 붙여 넣어 마크다운 변환이 일어남. `--` 가 `-` 로, `__path__` 의 밑줄이 굵은 글씨 표시로 사라지고, `v1` 이 `vl` 로 바뀜
- **해결** : 명령과 설정은 항상 코드 블록으로 복사하고, 붙여 넣은 뒤 `--`, `__path__`, `v1` 부분을 눈으로 확인

```bash
# Alloy 설정 문법 검사 (문법 OK 가 나와야 함)
alloy fmt /etc/alloy/config.alloy > /dev/null && echo "문법 OK"

# 잘못 바뀐 글자가 남아 있는지 검색 (아무것도 안 나와야 정상)
grep -nE 'vl/write|"_path"' /etc/alloy/config.alloy
```

### (8) 디스크 경보 미리보기에서 15개가 모두 Firing

- **증상** : 경보 규칙 Preview 에서 모든 서버의 파티션 15개가 한꺼번에 Firing
- **원인** : 처음 임계치가 0 이었고, 쿼리에 감시 대상을 제한하지 않아 /DATA, /BACKUP 같은 다른 파티션까지 모두 포함됨
- **해결** : 임계치를 80 으로, device 정규식으로 대상을 NFS(`/dev/sdb1`) · DB LVM(`vg_db`) · OS 디스크(`/dev/sda2`)로 제한
- Explore → Prometheus 에서 `count by (instance, device, mountpoint) (node_filesystem_size_bytes)` 로 실제 라벨 값을 먼저 확인한 뒤 정규식을 작성
- `mountpoint` 라벨은 디스크가 마운트된 위치(/nfs)라서 하위 폴더(/nfs/share)로는 조회되지 않음

### (9) Nginx 웹 로그가 Loki 에 안 잡힘

- **증상** : Alloy 에 `/var/log/nginx/access.log` 를 추가했지만 Loki 에 웹 접속 기록이 보이지 않음
- **원인** : filehub 사이트는 Nginx 기본 로그(access.log, 비어 있음)가 아니라 전용 로그 `filehub_access.log` 에 JSON 형식으로 기록함
- **해결** : WebServer Alloy 의 수집 경로를 `filehub_access.log` · `filehub_error.log` 로 바꾸고, 대시보드 쿼리를 `| json` 방식으로 수정

```bash
# 📍 WebServer — 실제 로그 파일과 크기 확인 (filehub_access.log 가 커지고 있어야 함)
ls -l /var/log/nginx/

# Nginx 설정에서 로그 경로 찾기
grep -rn "access_log\|error_log" /etc/nginx/

# 로그 형식 확인 (JSON 한 줄씩)
tail -3 /var/log/nginx/filehub_access.log
```

### (10) Slack 에 DatasourceNoData 경보

- **증상** : 웹 로그인 실패가 없는데도 Slack 에 DatasourceNoData 경보가 옴
- **원인** : 실패가 한 건도 없으면 Loki 는 0 이 아니라 **빈 결과**를 돌려주고, Grafana 기본 설정은 빈 결과를 이상 상태로 봄
- **해결** : 경보 규칙의 Configure no data and error handling → Alert state if no data 를 Normal 로 변경 후 Save rule and exit
- Loki 기반 경보(로그인 실패, 업로드, 계정 잠금)는 모두 같은 설정이 필요

### (11) 웹 로그인 경보가 오류 없이 조용히 멈춤

- **증상** : 10/23 이후 로그인 실패를 여러 번 해도 경보와 대시보드 그래프가 0
- **원인** : 팀원이 Flask 에 OpenTelemetry 를 적용하면서 감사 로그 형식이 바뀜. 프로세스 이름이 gunicorn → opentelemetry-instrument 로, JSON 앞에 `[trace_id=...]` 가 붙음. 처음 쿼리는 "filehub.audit 바로 뒤의 JSON" 만 읽어서 새 형식을 하나도 찾지 못함
- **해결** : 줄 어디에 있든 `{...}` JSON 을 꺼내는 regexp 로 바꿔 예전 · 새 형식을 모두 읽도록 수정 ([2장 2)-D](#2-경보-규칙) 참고)

```bash
# 📍 WebServer — 실제 감사 로그 형식 확인
grep "filehub.audit" /var/log/messages | tail -3
```
> 쿼리는 문법 오류가 없으면 0 을 돌려줄 뿐 경고하지 않음. **웹 코드가 바뀌면 시험 실패 1번으로 경보를 다시 확인**

### (12) 20:30 에 웹 요청과 500 오류가 한꺼번에 몰려 보임

- **증상** : 대시보드에 20:28~20:30 사이 웹 요청 190건, 500 오류 19건이 몰려 있음
- **원인** : Alloy 를 새 설정으로 재시작하면서 그날 쌓인 Nginx 로그(09:18~20:28)를 한 번에 읽었고, Loki 에는 읽은 시각으로 기록됨
- **해결** : 1회성 현상. 실제 발생 시각은 로그 줄 안의 `"time"` 값으로 확인
- 새 서버나 새 로그 파일을 수집 대상에 추가할 때도 같은 일이 한 번 생기므로 **발표 직전에는 수집 대상을 바꾸지 않음**

### (13) 1860 대시보드 Nodename 목록에서 일부 서버가 빠짐

- **증상** : 포트를 연 직후에도 Node Exporter Full(1860) 대시보드의 Nodename 목록에 Monitoring 만 보이거나 FileServer 가 없음
- **원인** : Alloy 가 연결 실패를 겪으며 재시도 간격을 늘려 둔 상태(exponential backoff)였고, Grafana 도 변수 목록을 캐시함. FileServer 에는 config.alloy 가 아직 배포되지 않았음
- **해결** : 해당 서버에서 Alloy 를 재시작하고 브라우저에서 Ctrl+F5 로 새로고침

```bash
# 📍 누락된 서버 — Alloy 재시작
systemctl restart alloy

# 📍 Monitoring — Prometheus 에 실제로 들어오는 서버 확인 (4대가 보여야 함)
curl -s 'localhost:9090/api/v1/query?query=up' | grep -o '"instance":"[^"]*"'
```

### (14) Loki 로그에 context canceled / EOF 반복

- **증상** : `journalctl -u loki` 에 127.0.0.1:9096 관련 context canceled, EOF 메시지가 계속 찍힘
- **원인** : Grafana 에서 화면을 바꾸거나 쿼리를 연달아 실행하면 이전 쿼리가 취소되는데, 단일 모드로 도는 Loki 내부 스케줄러(9096)가 그때 남기는 기록
- **해결** : 수집 · 조회에 영향이 없으므로 그대로 둠
- 로그 수집이 실제로 멈췄는지는 Explore → Loki 에서 최근 5분 로그가 보이는지로 판단

---

## 4. 검증 결과 및 운영 수칙

| 항목 | 확인 방법 | 결과 |
|---|---|---|
| 서버 4대 메트릭 | Explore → Prometheus 에서 `up` | instance 4대 표시 |
| 서버 로그 | Explore → Loki 에서 hostname 별 조회 | messages · secure · nginx 로그 표시 |
| 트레이스 | Explore → Tempo 에서 filehub-web 조회 | 요청별 트레이스 표시 |
| 디스크 경보 | 임계치를 낮춰 시험 | Slack 경보 수신 |
| 웹 로그인 경보 | 없는 아이디로 4번 실패 | Slack 경보 수신, 대시보드 표시 |

### VM 종료 및 백업 수칙

- 4대 모두 `shutdown -h now` 로 정상 종료 (켜 둔 채 호스트 PC 를 끄면 Prometheus · Loki · MariaDB 데이터 손상 위험)
- 전원이 꺼진 뒤 4대 모두 VirtualBox 스냅샷 촬영, 발표 전날 NFS · iSCSI 50% 상태로 한 번 더
- 재부팅 시 C 드라이브가 초기화되는 PC 라면 `.vdi` 위치를 확인하거나 OVA 로 내보내기
