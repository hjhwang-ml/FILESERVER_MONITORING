# 06. 시연 가이드

> 시연 전 준비, 시연용 설정, 시나리오별 명령, 시연 후 정리

[← 05. 관제서버](05-monitoring.md) · [README](../README.md)

---

## 목차

- [1. 시연 전 준비](#1-시연-전-준비)
- [2. 시연용 설정 (경보 속도)](#2-시연용-설정-경보-속도)
- [3. 시나리오별 시연](#3-시나리오별-시연)
  - [① iSCSI 백업 공간 부족 → LVM 자동 증설](#1-iscsi-백업-공간-부족--lvm-자동-증설-lv_backup)
  - [② 파일 업로드 지연 → 트레이스로 병목 추적](#2-파일-업로드-지연-99-에서-멈춤--트레이스로-병목-추적)
  - [③ 대용량 업로드 → 한도 초과 차단](#3-대용량-업로드--한도-초과-차단--이메일로-당사자-연락-참여형)
  - [④ 웹 로그인 실패 반복 → 계정 잠금](#4-웹-로그인-실패-반복--계정-잠금-참여형)
  - [⑤ (선택) NFS 저장소 용량 → 정리](#5-선택-발표-외-nfs-저장소-용량--정리로-조치)
- [4. 시연 후 정리](#4-시연-후-정리)
- [부록. storage_load.sh 명령](#부록-storage_loadsh-명령)

---

## 1. 시연 전 준비

- 발표 전날 4대 모두 `shutdown -h now` 로 정상 종료한 뒤 VirtualBox 스냅샷 촬영 ([05. 관제서버 4장 운영 수칙](05-monitoring.md#vm-종료-및-백업-수칙))
- 시작 전 NFS · iSCSI 를 **50% 까지** 채운 상태로 맞춰 둠
- 아래 [2. 시연용 경보 속도](#2-시연용-설정-경보-속도)가 적용돼 있는지 확인
- Grafana 대시보드(JYIT 통합 관제 M·E·L·T), Slack 경보 채널, 웹 로그인 화면(`https://192.168.0.220`)을 미리 열어 둠
- 웹 자동 로그아웃(`IDLE_TIMEOUT`)은 30분. 리허설 뒤 시간이 지났으면 시연 직전에 한 번 다시 로그인해 둠 ([04. 웹/WAS 8장](04-web.md#8-앱-동작-설정-apppy-안의-값))
- 자동 증설 타이머 : 리허설에서 멈춰 두었다면 본 시연 전에 다시 켬 ([3-1)-A](#a-테스트-시작-전-확인))
- 리허설 때마다 lv_backup 이 3G 씩 늘고 XFS 는 줄일 수 없음 → `lvs vg_db; vgs` 로 lv_backup 크기(자동 상한 45G)와 VFree(5G 미만이면 증설 안 함) 확인, 모자라면 스냅샷으로 되돌림
- 웹서버에 tc 대역폭 제한이 남아 있지 않은지 확인 (`tc qdisc show dev enp0s8` 에 netem 이 없어야 함)
- 시나리오 ② 용 테스트 파일 준비 (윈도우 cmd : `fsutil file createnew test10MB.bin 10485760` — 10MB 빈 파일 생성)
- 시나리오 ③ · ④ (참여형) : 팀 계정 team2 · team4 · team5 · team6 로 로그인되는지, 잠긴 계정이 없는지 확인 (비밀번호는 발표 슬라이드 계정 안내에만 두고 문서에는 적지 않음)
- 시나리오 ③ : 공용 폴더에 600MB 더미 파일 압축본(`대용량_시연_600MB.zip`)을 올려 둠. 팀 폴더(한도 500MB)는 비어 있는 상태로
- 발표 전날 보안 정리 : 파일서버 · DB · 관제 SSH 는 웹서버(56.101)에서만, DB 원격 root(`root@'%'`) 삭제([03. DB서버 3장](03-db.md#db-계정과-권한-용도별-분리--최소-권한)), 파일서버 NFS rich rule 은 56.101 · 56.103 만([01. NFS 4장](01-nfs.md#a-파일서버))

📍 **FileServer · DBServer · Monitoring 각각**
```bash
# work 존의 ssh 서비스 빼기 (누구나 ssh 가능 → 차단)
firewall-cmd --permanent --zone=work --remove-service=ssh

# 웹서버(56.101)에서 오는 ssh 만 허용
firewall-cmd --permanent --zone=work --add-rich-rule='rule family="ipv4" source address="192.168.56.101" service name="ssh" accept'

# 방화벽 다시 읽기
firewall-cmd --reload
```
- ⚠️ 지금 접속한 SSH 창은 닫지 말고, 새 창에서 웹서버를 거쳐 다시 들어가지는지 확인한 뒤 닫는다 (잘못 막으면 VirtualBox 콘솔로 들어가 되돌림).
- 관제서버는 브리지(public 존)에도 ssh 가 열려 있으면 `--zone=public --remove-service=ssh` 도 함께. Grafana 3000 은 그대로 둔다.
- 결과적으로 **웹서버가 점프 호스트(배스천)** 역할을 한다.

---

## 2. 시연용 설정 (경보 속도)

기본값 그대로면 디스크를 채운 뒤 Slack 경보가 오기까지 최대 1분 45초, 해제 알림은 최대 5분이 걸려 시연에 맞게 아래처럼 줄였다.

| 항목 | 기본값 → 시연값 | 위치 |
|---|---|---|
| 평가 간격 | 1m → **10s** | 규칙 Edit → Set evaluation behavior → 평가 그룹 옆 Edit (10s 가 Grafana 기본 최솟값, 같은 그룹 규칙 모두 적용) |
| Group wait | 30s → **5s** | Notification policies → Default policy → More → Edit → Timing options |
| Group interval | 5m → **30s** | 〃 |
| Repeat interval | 4h 유지 | 〃 |

| 용어 | 뜻 |
|---|---|
| Group wait | 첫 경보가 생긴 뒤 같은 그룹 경보를 모아 보내기 전 기다리는 시간 |
| Group interval | 그룹에 새 경보가 추가됐을 때(또는 해제됐을 때) 다시 보내기까지 간격 |
| Repeat interval | 같은 경보가 계속 울릴 때 재알림 간격 |

- 결과 : 채운 뒤 Slack 경보까지 **30초 안팎**, 해제 알림도 1분 안쪽
- 수집 간격(Alloy `scrape_interval` 15s)은 서버 설정 변경과 재시작이 필요해 그대로 둠
- 운영 환경이라면 너무 민감하므로 평가 1m, Pending 1m, Timing 기본값으로 되돌림

---

## 3. 시나리오별 시연

모든 시나리오는 **상황 → 감지 → 원인 분석 → 조치** 순서로 진행한다. 시작 전 NFS · iSCSI 를 50% 까지 채운 상태로 맞춰 둔다.

| # | 상황 | 감지 | 조치 | 관련 서버 |
|---|---|---|---|---|
| ① | DB 백업 공간 `/dbbackup` 을 86% 까지 채움 | Slack 디스크 사용률 80% 초과 → Slack 자동 증설 조치 | 85% 2회 연속 → lv_backup 3G 자동 증설 | DBServer · FileServer |
| ② | 웹서버 → 파일서버 NFS 구간 대역폭을 tc 로 제한, 업로드가 99% 에서 멈춤 | 경보 없음(사용자 신고), Tempo 느린 요청(2초 이상) 패널 | 트레이스에서 nfs.save 병목 확인 → 제한 해제 후 재업로드 정상 | WebServer · FileServer |
| ③ | 팀 계정으로 600MB 파일을 팀 폴더(한도 500MB)에 업로드 | 앱이 업로드 차단 → Slack 폴더 용량 초과 업로드 차단, 감사 로그 UPLOAD FAIL | 로그에 남은 이메일로 당사자 연락, 필요하면 폴더 한도 상향 | WebServer · DBServer |
| ④ | 팀 계정으로 비밀번호를 여러 번 틀림 | Slack 웹 로그인 실패 급증, Loki 로그인 실패 로그(계정 · IP) | 5회 연속 실패 시 자동 잠금 → 관리자 본인 확인 후 해제 | WebServer |
| ⑤ | (선택, 발표 외) 웹서버에서 `/nfs` 를 86% 까지 채움 | Slack 디스크 사용률 80% 초과 (FileServer /nfs) | NFS 는 일반 파티션이라 증설 불가 → 테스트 파일 정리 | WebServer · FileServer |

### 1) iSCSI 백업 공간 부족 → LVM 자동 증설 (lv_backup)

- **상황** : DBServer 의 `/dbbackup`(iSCSI 위 lv_backup)을 `storage_load.sh` 로 86% 까지 채움 (`storage_load.sh fill 86 2 /dbbackup`)
- **감지** : Slack `[경보 1건] 디스크 사용률 80% 초과` (대상 DBServer 서버 /dbbackup)
- **원인 분석** : 대시보드 저장소 사용률 빨강, 파일서버 sdc 쓰기 상승, 저장소 작업 로그에서 작업 시각 확인
- **조치** : 사용률 85% 이상이 2번 연속(15초 간격)이면 자동 증설 타이머가 서비스를 멈추지 않고 lv_backup 을 3G 늘림 (XFS 도 함께) → 저장소 작업 로그에 `action=auto_extend` → Slack "자동 증설 조치" ([02. iSCSI 3장](02-iscsi.md#3-무중단-증설과-자동-증설-db-서버) 참고)

#### A. 테스트 시작 전 확인

📍 **DBServer**
```bash
# 자동 증설 타이머 켜기 (이미 active 면 그대로, 리허설에서 멈췄다면 여기서 다시 켬)
systemctl start lv-autoextend.timer

# 타이머가 등록돼 15초마다 도는지 확인
systemctl list-timers lv-autoextend.timer
```
> 성공 기준 : NEXT 칸에 다음 실행 시각이 보임

```bash
# /dbbackup 현재 사용률 확인
/usr/local/bin/storage_load.sh status /dbbackup
```
> 시작 전 50% 안팎이면 정상

#### B. 부하 시작

📍 **DBServer**
```bash
# 86% 까지 2G 씩 채우기 (백그라운드 실행, 진행 기록은 /root/sl.log)
nohup /usr/local/bin/storage_load.sh fill 86 2 /dbbackup > /root/sl.log 2>&1 &

# 진행 상황 보기
tail -f /root/sl.log
```

| 부분 | 뜻 |
|---|---|
| `fill 86 2 /dbbackup` | /dbbackup 을 86% 가 될 때까지 2G 조각 파일로 채움 |
| `nohup ... &` | 백그라운드로 돌리고, 터미널을 닫아도 계속 실행 |
| `> /root/sl.log 2>&1` | 출력과 오류를 모두 로그 파일로 |
| `tail -f` | 파일에 새 줄이 생길 때마다 계속 보여 줌 (Ctrl+C 로 빠져나와도 부하는 계속됨) |

- 목표는 **86 으로 고정**한다. fill 은 반복마다 용량을 다시 재므로, 목표를 90 처럼 높이면 증설로 커진 크기 기준으로 또 채워 상한(45G)까지 연달아 증설될 수 있다.
- 흐름 : 80% 넘음 → Slack 디스크 경보 → 85% 2회 연속 → lv_backup +3G → 사용률 하락 → Slack "자동 증설 조치" → 디스크 경보 해제

### 2) 파일 업로드 지연 (99% 에서 멈춤) → 트레이스로 병목 추적

- **상황** : 사용자가 "업로드가 99% 에서 멈춰요" 라고 신고. 웹서버 → 파일서버(56.102) 구간 대역폭을 tc 로 제한해 재현
- **감지** : 에러 경보 없이 업로드만 지연 (사용자 신고). CPU · 메모리 · 디스크 정상, 에러 로그 없음 → Tempo **느린 요청(2초 이상)** 패널에 업로드(POST) 요청 등장
- **원인 분석** : 트레이스 폭포 그림에서 `quota.check` · `db.insert` 는 짧고 **`nfs.save` 만 김** → 웹서버 ↔ 파일서버 NFS 구간 병목. 99% = 브라우저 → Nginx 전송은 끝났고 앱이 NFS 에 저장하는 중이라 응답을 못 준 상태
- **조치** : tc 제한 해제 → 같은 파일을 다시 올려 정상 속도 확인. 실제 운영이라면 스토리지 트래픽 전용망을 따로 두거나 대역폭을 늘림

#### A. 대역폭 제한 걸기

📍 **WebServer**
```bash
# 출구 큐를 4칸으로 나누기 (평소 트래픽은 1~3칸, 4칸은 비워 둠)
tc qdisc add dev enp0s8 root handle 1: prio bands 4

# 4칸에만 2mbit 제한
tc qdisc add dev enp0s8 parent 1:4 handle 40: netem rate 2mbit

# 파일서버(56.102)로 가는 패킷만 4칸으로 보내기
tc filter add dev enp0s8 parent 1: protocol ip prio 1 u32 match ip dst 192.168.56.102/32 flowid 1:4

# 걸렸는지 확인 (netem ... rate 2Mbit 줄이 보이면 정상)
tc qdisc show dev enp0s8
```

| 명령 | 뜻 |
|---|---|
| `tc` | traffic control — 리눅스 커널의 네트워크 송신 큐 제어 |
| `qdisc add ... root handle 1: prio bands 4` | 나가는 패킷 큐를 우선순위 4칸으로 나눔 (기본 분류는 1~3칸에만 들어감) |
| `parent 1:4 ... netem rate 2mbit` | 4번 칸에 netem(네트워크 에뮬레이터)을 붙여 2Mbit/s 로 제한 |
| `filter ... u32 match ip dst 192.168.56.102/32 flowid 1:4` | 목적지가 파일서버인 패킷만 4번 칸으로 분류 |

- 56.102 로 가는 것만 느려진다. DB(56.103) · 관제(56.104) 통신과 사용자망(enp0s10)은 그대로.
- 웹서버를 거쳐 파일서버에 SSH 로 들어간 창도 느려지므로 시연 중 파일서버 작업은 피한다.
- 리허설 기준 실제 속도 약 1MB/s : 10MB 파일 약 10초, 100MB 파일 약 1분 40초.

#### B. 업로드 → 트레이스 확인

1. 브라우저에서 10MB 테스트 파일 업로드 → 진행률이 99% 에서 멈춤
2. Grafana 대시보드 T줄 **느린 요청(2초 이상)** 패널에 POST 요청이 나타남 (표시까지 약 10초)
3. 트레이스를 열어 `nfs.save` 막대가 가장 긴 것을 보여 줌

#### C. 해제 ★ 꼭 할 것

📍 **WebServer**
```bash
# 제한 해제 (root qdisc 를 지우면 아래 칸·필터도 함께 사라짐)
tc qdisc del dev enp0s8 root

# 해제 확인 (prio · netem 줄이 없어야 함)
tc qdisc show dev enp0s8
```
> tc 설정은 재부팅하면 사라지지만, 켜 둔 채 다른 시연을 하면 모든 업로드가 느려진다.

### 3) 대용량 업로드 → 한도 초과 차단 → 이메일로 당사자 연락 (참여형)

- **상황** : 참여자가 팀 계정(team2 · 4 · 5 · 6)으로 로그인해 공용 폴더의 600MB 더미 파일을 받아 압축을 풀고, 자기 팀 폴더(한도 500MB)에 업로드
- **감지** : 앱이 파일을 다 받은 뒤 저장 전에 폴더 한도를 확인해 NFS 에 저장하지 않고 차단 (화면에 "폴더 용량 한도" 문구, 413) → Slack `[경보] 폴더 용량 초과 업로드 차단` (대상 사용자, 최근 5분 차단 횟수)
- **원인 분석** : 대시보드 E줄 감사 이벤트 목록의 UPLOAD FAIL 줄에서 사용자 · 이메일 · IP · 파일 · 크기 · 차단 사유 확인. 필요하면 trace_id 로 트레이스를 열어 업로드 처리 시간(quota.check) 참고
- **조치** : 로그에 남은 이메일로 당사자에게 연락해 불필요한 파일 정리 안내, 업무상 꼭 필요하면 관리자가 폴더 한도 상향

**진행 순서**
1. 슬라이드 계정 안내를 띄우고 `https://192.168.0.220` 접속 · 팀 계정 로그인 안내 (보안 경고는 고급 → 계속)
2. 공용 폴더의 더미 파일을 받아 압축을 풀고 자기 팀 폴더에 업로드하도록 유도
3. 업로드 실패 메시지 확인 (600MB > 팀 한도 500MB). 큰 파일이라 다 받은 뒤 거절되어 메시지까지 시간이 걸림
4. Slack 경보 화면 → 대시보드 E줄 감사 이벤트의 UPLOAD FAIL 줄 확인
5. 조치 설명 : 이메일로 연락, 필요하면 아래 A 로 한도 상향

#### A. (필요할 때) 팀 폴더 한도 상향 : 앱 한도와 파일서버 디스크 쿼터를 함께 올린다

📍 **DBServer**
```bash
# 앱 한도 : 2팀 폴더를 1024MB 로 (비밀번호는 실행 중에 물어봄)
mysql -u root -p filehub -e "UPDATE folders SET quota_mb=1024 WHERE dept='team2' AND parent_id IS NULL"
```
> 앱은 업로드 때마다 DB 값을 읽으므로 재시작 없이 바로 적용

📍 **FileServer**
```bash
# 디스크 쿼터 : 2팀 프로젝트 쿼터도 같은 크기로
xfs_quota -x -c 'limit -p bhard=1g fh_team2' /nfs
```
- 앱 한도만 올리면 파일서버 쿼터(500M)에 막혀 **507 "디스크 한도 가득 참"** 이 난다 ([01. NFS 1-7)](01-nfs.md#7-팀-폴더-용량-한도-xfs-프로젝트-쿼터))
- 시연 뒤에는 [4장 정리](#4-시연-후-정리)에서 500 · 500m 로 되돌린다

### 4) 웹 로그인 실패 반복 → 계정 잠금 (참여형)

- **상황** : 참여자가 팀 계정으로 비밀번호를 일부러 여러 번 틀림 (이미 로그인돼 있으면 로그아웃 후)
- **감지** : 같은 IP 에서 5분간 4회 이상 실패 → Slack `[경보] 웹 로그인 실패 급증` (대상 접속 IP)
- **원인 분석** : Loki(대시보드 E줄 웹 로그인 실패 · 감사 이벤트)에서 LOGIN FAIL 줄의 계정과 IP 확인
- **조치** : 5회 연속 실패 시 앱이 계정을 자동으로 잠그고 `ACCOUNT_LOCKED` 를 남김 → 관리자가 본인 확인 후 해제

Grafana → Explore → Loki : 로그인 실패와 잠금 기록만 보기
```logql
{job="syslog", hostname="WebServer"} |= "filehub.audit"
  | regexp `(?P<audit>\{.*\})` | line_format "{{.audit}}" | json
  | action=~"LOGIN|ACCOUNT_LOCKED" | result!="SUCCESS"
```
> 경보는 접속 IP 별로 묶인다. 여러 팀이 동시에 틀리면 IP 마다 따로 온다.

📍 **WebServer** — 본인 확인 뒤 잠금 해제 (예 : team2)
```bash
cd /opt/filehub/app
sudo -u filehub /opt/filehub/.venv/bin/python manage.py unlock team2
cd ~
```
> 관리자 화면(`/admin/users`)에서 해제해도 된다 ([04. 웹/WAS 2-5)](04-web.md#5-설치-점검과-첫-관리자-계정))

### 5) (선택, 발표 외) NFS 저장소 용량 → 정리로 조치

- **상황** : 웹서버에서 NFS(`/nfs`)를 86% 까지 채움
- **감지** : Slack `[경보 1건] 디스크 사용률 80% 초과` (대상 FileServer 서버 /nfs)
- **원인 분석** : 대시보드 저장소 사용률 · 파일서버 sdb 쓰기 상승, 저장소 작업 로그 (WebServer 가 남긴 storage-load 로그)
- **조치** : NFS 는 LVM 이 아닌 일반 파티션이라 늘릴 수 없음 → 테스트 파일 정리로 조치 (① iSCSI 자동 증설과 대비)

📍 **WebServer**
```bash
# 86% 까지 2G 씩 채우기 (filehub 계정으로, 웹서버의 NFS 마운트에)
nohup sudo -u filehub /usr/local/bin/storage_load.sh fill 86 2 /mnt/nfs/.loadtest > /root/nfs_sl.log 2>&1 &

# 조치 : 테스트 파일 정리
sudo -u filehub /usr/local/bin/storage_load.sh clean /mnt/nfs/.loadtest
```
- 파일서버에서 `/nfs` 에 직접 돌리면 로컬 디스크라 거부된다. **반드시 웹서버의 `/mnt/nfs` 경로로.**
- root 로 돌리면 root_squash 때문에 쓰기 권한이 없다 → `sudo -u filehub`.
- 100G 를 86% 까지 채우는 데 시간이 걸리므로 시작 전 사용률을 75% 안팎으로 맞춰 둔다.
- 로그의 hostname 은 WebServer 로 찍힌다. 대시보드 저장소 작업 로그 패널이 DBServer 만 보게 돼 있으면 WebServer 도 넣어야 보인다.

---

## 4. 시연 후 정리

**① 시연에서 채운 `/dbbackup` 은 반드시 정리** (새벽 3시 백업 전에)

📍 **DBServer**
```bash
# 시연 끝나고 디스크 정리
/usr/local/bin/storage_load.sh clean /dbbackup

# 사용률이 돌아왔는지 확인
/usr/local/bin/storage_load.sh status /dbbackup
```

**② 를 했다면 tc 제한이 풀렸는지 확인**

📍 **WebServer**
```bash
tc qdisc show dev enp0s8
```

**③ 에서 한도를 올렸다면 원래 값으로 되돌림** (앱 500MB, 파일서버 쿼터 500m)

```bash
# 📍 DBServer — 앱 한도 되돌리기
mysql -u root -p filehub -e "UPDATE folders SET quota_mb=500 WHERE dept LIKE 'team%' AND parent_id IS NULL"

# 📍 FileServer — 파일서버 쿼터 되돌리기
for p in team1 team2 team4 team5 team6; do xfs_quota -x -c "limit -p bhard=500m fh_$p" /nfs; done
```

**④ 에서 잠긴 팀 계정 모두 해제**

📍 **WebServer**
```bash
cd /opt/filehub/app
for u in team2 team4 team5 team6; do sudo -u filehub /opt/filehub/.venv/bin/python manage.py unlock $u; done
cd ~
```

**⑤ 를 했다면**

📍 **WebServer**
```bash
sudo -u filehub /usr/local/bin/storage_load.sh clean /mnt/nfs/.loadtest
```

**그 밖에**
- 자동 증설로 커진 lv_backup 은 줄일 수 없음 → 다시 시연할 계획이면 VirtualBox 스냅샷으로 되돌림
- 경보 임계치를 낮춰 시험했다면 원래 값(80%)으로 되돌리고 Save rule and exit 까지 눌러 저장
- 발표가 끝나면 경보 속도를 운영값(평가 1m, Pending 1m, Notification Timing 기본값)으로 되돌림

---

## 부록. `storage_load.sh` 명령

시연용 부하 생성 스크립트 ([`scripts/storage_load.sh`](../scripts/storage_load.sh)). 모든 동작을 syslog 태그 `storage-load` 로 남겨 Loki → 대시보드 "저장소 작업 로그" 패널에 보인다.

설치 : `install -m 755 storage_load.sh /usr/local/bin/storage_load.sh && dnf install -y fio`

| 명령 | 뜻 |
|---|---|
| `fill <목표%> <조각GB> <경로...>` | `demo_fill_N.dat` 파일을 조각 크기씩 써서 목표 사용률에서 정확히 멈춤. 80% 를 넘는 순간 `event=threshold` 기록. 목표는 95% 이하만 허용 |
| `io <초> <경로>` | fio 로 순차 쓰기 · 랜덤 쓰기 · 읽기/쓰기 혼합 I/O 부하를 지정 시간 동안 (여유 공간 10G 이상 필요) |
| `stop` | 실행 중인 fill · io 부하 중지 |
| `clean <경로...>` | 테스트 파일(`demo_fill_*.dat`, `io_*.dat`) 삭제 후 전후 사용률 기록 (조치) |
| `extend <LV경로> <크기>` | `lvextend -r` 로 LV 수동 증설 후 기록 (조치, root) |
| `status [경로...]` | 실행 중인 부하와 경로별 저장소 종류 · 사용률 · 남은 용량 |

- 대상은 NFS 또는 iSCSI 만 허용 — 로컬 디스크 경로는 "로컬 디스크는 대상이 아님" 으로 거부 (OS 디스크를 실수로 채우지 않게)
- `dd ... oflag=direct` 로 페이지 캐시를 거치지 않고 디스크에 바로 써서 사용률이 즉시 반영됨
