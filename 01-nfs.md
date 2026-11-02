# 01. 파일서버 · NFS

> 파일서버 sdb → XFS → `/nfs` → 웹서버 업로드 저장소 / DB 서버 백업 저장소  
> 기준 : Rocky Linux 10, 2026-10-30 실제 구성

[← README](../README.md) · [02. iSCSI →](02-iscsi.md)

---

## 목차

- [0. 이 문서로 만드는 것](#0-이-문서로-만드는-것)
- [1. 파일서버 (192.168.56.102)](#1-파일서버-19216856102)
- [2. 웹서버 (192.168.56.101)](#2-웹서버-19216856101)
- [3. DB 서버 (192.168.56.103) — 백업 저장소](#3-db-서버-19216856103--백업-저장소)
- [4. 전체 점검](#4-전체-점검)
- [5. 자주 막히는 곳](#5-자주-막히는-곳)

---

## 0. 이 문서로 만드는 것

**NFS = 디렉터리 공유.** 파일서버가 자기 디스크(sdb)를 XFS로 포맷해 `/nfs` 에 붙이고, 그 안의 폴더를 다른 서버에 빌려준다. 실제 파일은 전부 파일서버 sdb 에 저장된다.

```
[파일서버 192.168.56.102]
  sdb (100G) → sdb1 → XFS → /nfs
    /nfs/share    → 웹서버(56.101)에 공유
      └ uploads/             ★ 사용자가 올린 실제 파일 (팀 폴더 / UUID 이름)
          general/ team1/ team2/ team4/ team5/ team6/
                             팀마다 XFS 프로젝트 쿼터 (팀 500M, 공용 2G)
      └ .loadtest/           부하 시험용
    /nfs/backup   → DB 서버(56.103)에 공유
      └ db/                  ★ MySQL 덤프 사본

[웹서버 192.168.56.101]
  /mnt/nfs  ←  192.168.56.102:/nfs/share
  앱은 /mnt/nfs/uploads/<팀>/ 에 쓴다 (config.py 의 NFS_DIR, DB stored_name = 팀/UUID)

[DB 서버 192.168.56.103]
  /mnt/nfs  ←  192.168.56.102:/nfs/backup/db
  db_backup.sh 가 /dbbackup 의 덤프를 여기로 rsync
```

> 웹서버와 DB 서버 둘 다 `/mnt/nfs` 라는 이름을 쓰지만 **가리키는 곳이 다르다.**

| 서버 | IP | NIC / 방화벽 존 |
|---|---|---|
| 웹서버 | 192.168.56.101 | 호스트 전용 NIC enp0s8, work 존 |
| 파일서버 | 192.168.56.102 | 호스트 전용 NIC enp0s8, work 존 |
| DB 서버 | 192.168.56.103 | 호스트 전용 NIC enp0s8, work 존 |

**공통 서비스 계정 : `filehub` UID 2000 / GID 2000**

- NFS 는 사용자 이름이 아니라 **UID 숫자**로 권한을 판단한다.
- 웹서버와 파일서버의 `filehub` 가 같은 2000 이어야 웹 앱이 쓴 파일을 파일서버에서도 filehub 소유로 인식한다.
- 명령은 전부 root 로 실행한다. 각 단계 제목의 **서버 이름을 꼭 확인**할 것.

---

## 1. 파일서버 (192.168.56.102)

### 1) 패키지 설치

📍 **FileServer**
```bash
# NFS 서버 패키지 설치
dnf install -y nfs-utils
```

### 2) NFS 에 쓸 디스크 확인 ★ 가장 중요

📍 **FileServer**
```bash
# 붙어 있는 디스크 목록 확인
lsblk
```

- 파일서버에는 100G 디스크가 2개 있다.
  - **sdb → NFS 용** (이 문서에서 포맷)
  - **sdc → iSCSI 용** (절대 포맷하지 않는다. DB 서버가 LVM 을 올려 쓴다)
- VirtualBox 에서 디스크 순서가 바뀌면 이름이 바뀔 수 있다. 아래 명령으로 크기·시리얼을 보고, 비어 있는(파티션·마운트 없는) 디스크가 맞는지 확인한 뒤 진행한다. **이미 쓰고 있는 디스크를 포맷하면 데이터가 날아간다.**

```bash
# 디스크 시리얼(by-id) 목록 확인
ls -l /dev/disk/by-id/ | grep -v part

# sdb 가 비어 있는지 확인 (파티션·파일시스템·마운트 없음)
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS /dev/sdb
```

### 3) 파티션 만들기 → XFS 포맷

📍 **FileServer**
```bash
# sdb 전체를 파티션 하나(sdb1)로 만들기
parted -s /dev/sdb mklabel gpt mkpart primary xfs 0% 100%

# sdb 아래에 sdb1 이 생겼는지 확인
lsblk /dev/sdb

# sdb1 을 XFS 로 포맷
mkfs.xfs /dev/sdb1
```

| 명령 / 옵션 | 뜻 |
|---|---|
| `parted -s` | 질문 없이(script 모드) 실행 |
| `mklabel gpt` | 디스크에 GPT 파티션 표를 새로 만듦 |
| `mkpart primary xfs 0% 100%` | 처음부터 끝까지 파티션 하나 |
| `mkfs.xfs` | 파티션에 XFS 파일시스템을 만듦 (= 포맷) |

> **포맷** = 디스크에 파일시스템(장부와 선반)을 까는 것.  
> **XFS** 는 Rocky 기본 파일시스템. 늘릴 수는 있지만 **줄일 수는 없다.**

### 4) `/nfs` 에 마운트 (재부팅해도 자동으로)

📍 **FileServer**
```bash
# 마운트할 폴더 만들기
mkdir -p /nfs

# sdb1 의 UUID 확인 (UUID="xxxx-..." 값을 복사)
blkid /dev/sdb1

# 부팅 때 자동 마운트되도록 fstab 에 등록
echo 'UUID=<위에서 복사한 UUID> /nfs xfs defaults,prjquota 0 0' >> /etc/fstab

# 바뀐 fstab 을 systemd 에 반영
systemctl daemon-reload

# fstab 대로 마운트
mount -a

# 마운트 확인 (/dev/sdb1 xfs 100G /nfs 이면 성공)
df -Th /nfs
```

- `/nfs` 는 그냥 빈 폴더 이름이다. 마운트하는 순간 그 폴더가 sdb1 의 입구가 된다.
- fstab 에 `sdb1` 대신 **UUID** 를 쓰는 이유 : 디스크 이름은 재부팅 때 바뀔 수 있다.
- `prjquota` = 이 파일시스템에서 폴더(프로젝트) 단위 용량 한도를 쓰겠다는 옵션. [1-7) 팀 폴더 한도](#7-팀-폴더-용량-한도-xfs-프로젝트-쿼터)에 필요하다.
- 쿼터 옵션은 `mount -o remount` 로는 켜지지 않는다. 이미 `/nfs` 를 쓰던 서버는 1-7) 의 "이미 쓰던 서버" 순서로 다시 마운트한다.

| fstab 칸 | 값 | 뜻 |
|---|---|---|
| 1 | `UUID=...` | 어떤 장치를 |
| 2 | `/nfs` | 어디에 붙일지 |
| 3 | `xfs` | 파일시스템 종류 |
| 4 | `defaults,prjquota` | 마운트 옵션 |
| 5 | `0` | dump 백업 안 함 |
| 6 | `0` | 부팅 때 fsck 검사 안 함 (XFS 는 자체 복구) |

### 5) filehub 계정 (UID 2000)

📍 **FileServer**
```bash
# filehub 그룹 만들기 (GID 2000)
groupadd -g 2000 filehub

# 로그인 불가 filehub 계정 만들기 (UID 2000)
useradd -u 2000 -g 2000 -M -s /sbin/nologin filehub

# 계정 확인 (uid=2000(filehub) gid=2000(filehub))
id filehub
```

| 옵션 | 뜻 |
|---|---|
| `-u 2000` / `-g 2000` | UID / GID 를 2000 으로 고정 |
| `-M` | 홈 디렉터리를 만들지 않음 |
| `-s /sbin/nologin` | 로그인 불가 (서비스 전용 계정) |

### 6) 공유할 폴더 만들기와 소유자

📍 **FileServer**
```bash
# 웹 업로드용 팀 폴더와 부하 시험 폴더 만들기
mkdir -p /nfs/share/uploads/{general,team1,team2,team4,team5,team6} /nfs/share/.loadtest

# 공유 최상위 폴더 소유자를 nobody 로
chown nobody:nobody /nfs/share

# 공유 최상위 폴더 권한
chmod 775 /nfs/share

# 업로드·부하 시험 폴더는 filehub 소유로
chown -R filehub:filehub /nfs/share/uploads /nfs/share/.loadtest

# DB 백업용 폴더 만들기
mkdir -p /nfs/backup/db

# 백업 폴더 전체를 nobody 소유로
chown -R nobody:nobody /nfs/backup

# 폴더와 소유자 확인
ls -la /nfs /nfs/share /nfs/backup
```

- **왜 nobody 인가** : `root_squash` 때문에 다른 서버의 root 는 여기서 nobody 로 바뀐다. DB 서버의 root 가 백업을 쓰려면 `/nfs/backup` 이 nobody 소유여야 한다.
- 웹 앱은 filehub(2000)로 쓰므로 `uploads` 는 filehub 소유.
- 팀 폴더 이름(general, team1 …)은 **DB `folders.dept` 값과 같아야** 한다. 앱이 이 이름의 폴더 안에 UUID 이름으로 저장한다.
- 웹에서 만든 하위 폴더는 디스크에 디렉터리를 만들지 않고 DB 에서만 나눈다. 디스크 폴더는 팀 단위 6개뿐.
- `{general,team1,...}` 는 bash 중괄호 확장 — 한 번에 여러 폴더를 만든다.

### 7) 팀 폴더 용량 한도 (XFS 프로젝트 쿼터)

앱(DB `folders.quota_mb`)이 먼저 **413** 으로 막고, 앱 한도가 뚫려도 파일서버 디스크가 한 번 더 막는 **이중 구조**. 팀 폴더 500M, 공용 2G.

📍 **FileServer**
```bash
# 쿼터가 켜져 있는지 확인 (Project quota 의 Accounting · Enforcement 가 ON)
xfs_quota -x -c state /nfs
```
> OFF 면 1-4) fstab 에 `prjquota` 가 빠졌거나 remount 로만 적용한 것

```bash
# 팀 폴더 → 프로젝트 번호 대응표
cat > /etc/projects <<'EOF'
11:/nfs/share/uploads/general
12:/nfs/share/uploads/team1
13:/nfs/share/uploads/team2
14:/nfs/share/uploads/team4
15:/nfs/share/uploads/team5
16:/nfs/share/uploads/team6
EOF

# 프로젝트 번호 → 이름 대응표
cat > /etc/projid <<'EOF'
fh_general:11
fh_team1:12
fh_team2:13
fh_team4:14
fh_team5:15
fh_team6:16
EOF
```
> 번호는 서로 겹치지만 않으면 된다. 지금 서버의 번호는 `cat /etc/projid` 로 대조할 것.

```bash
# 각 팀 폴더를 프로젝트로 지정 (그 아래 새 파일에도 같은 번호가 붙음)
for p in general team1 team2 team4 team5 team6; do xfs_quota -x -c "project -s fh_$p" /nfs; done

# 공용 폴더 2G 한도
xfs_quota -x -c 'limit -p bhard=2g fh_general' /nfs

# 팀 폴더 500M 한도
for p in team1 team2 team4 team5 team6; do xfs_quota -x -c "limit -p bhard=500m fh_$p" /nfs; done

# 한도와 사용량 확인 (Used · Hard 칸)
xfs_quota -x -c 'report -h -p' /nfs
```

| 명령 / 옵션 | 뜻 |
|---|---|
| `xfs_quota -x` | 관리자(expert) 모드 |
| `-c '...'` | 실행할 쿼터 명령 |
| `project -s fh_team1` | 해당 폴더에 프로젝트 번호를 표시(setup) |
| `limit -p bhard=500m` | 프로젝트(-p) 블록 hard 한도 500MB |
| `report -h -p` | 프로젝트별 사용량을 사람이 읽기 쉬운 단위로 |

- `bhard` = 넘을 수 없는 한도. 앱 한도(DB `quota_mb` : 팀 500, 공용 2048)와 **같은 값**으로 맞춘다.
- 한도를 넘는 쓰기는 디스크가 거부한다. 앱은 이를 `ENOSPC`(Errno 28) / `EDQUOT` 로 받아 **507 "디스크 한도 가득 참"** 으로 응답한다.

#### 이미 `/nfs` 를 쓰던 서버에 쿼터를 켤 때

fstab 에 `prjquota` 를 넣은 뒤, 웹 앱을 먼저 멈춘다.

```bash
# 📍 WebServer — 웹서버 앱 중지
systemctl stop filehub

# 📍 FileServer — NFS 공유를 멈추고 /nfs 다시 마운트
systemctl stop nfs-server && umount /nfs && mount /nfs && systemctl start nfs-server

# 📍 WebServer — 웹서버 앱 다시 시작
systemctl start filehub
```
> `umount` 가 "target is busy" 면 `/nfs` 를 쓰는 셸·프로세스가 남은 것 (`fuser -vm /nfs` 로 확인)

### 8) 공유 설정 (`/etc/exports`)

📍 **FileServer**
```bash
# 어느 폴더를 어느 서버에 빌려줄지 설정
cat > /etc/exports <<'EOF'
/nfs/share   192.168.56.101(rw,sync,root_squash)
/nfs/backup  192.168.56.103(rw,sync,root_squash)
EOF
```

한 줄 읽는 법 : `[빌려줄 폴더]  [이 IP의 서버에게]([옵션])`

| 옵션 | 뜻 |
|---|---|
| `rw` | 읽기·쓰기 허용 (`ro` 면 읽기만) |
| `sync` | 디스크에 실제로 저장한 뒤에 "완료"라고 답함 |
| `root_squash` | 상대 서버 root 를 root 로 인정하지 않고 nobody 로 바꿈 |

### 9) NFS 서비스 시작과 적용

📍 **FileServer**
```bash
# NFS 서버 시작 및 부팅 시 자동 시작
systemctl enable --now nfs-server

# /etc/exports 다시 읽어 공유 적용
exportfs -ra

# 공유 상태 확인
exportfs -v
```

| 옵션 | 뜻 |
|---|---|
| `exportfs -r` | exports 파일을 다시 읽어 동기화 |
| `exportfs -a` | 모든 항목에 적용 |
| `exportfs -v` | 현재 공유 목록과 옵션 상세 표시 |

> `exportfs -v` 에 두 줄이 보이면 성공. `wdelay, hide, no_subtree_check, sec=sys, secure, no_all_squash` 같은 옵션은 NFS 가 자동으로 채운 기본값이다.

### 10) 방화벽 (work 존, 필요한 서버 IP만)

📍 **FileServer**
```bash
# NIC 별 방화벽 존 확인
firewall-cmd --get-active-zones

# 호스트 전용 NIC 를 work 존에 (이미 되어 있으면 건너뜀)
firewall-cmd --permanent --zone=work --change-interface=enp0s8

# 웹서버(56.101), DB 서버(56.103)에게만 NFS 허용
for ip in 192.168.56.101 192.168.56.103; do
  for s in nfs mountd rpc-bind; do
    firewall-cmd --permanent --zone=work \
      --add-rich-rule="rule family=\"ipv4\" source address=\"$ip\" service name=\"$s\" accept"
  done
done

# 방화벽 설정 다시 읽기
firewall-cmd --reload

# 허용 규칙 확인
firewall-cmd --zone=work --list-rich-rules
```

| 서비스 | 포트 | 역할 |
|---|---|---|
| `nfs` | 2049 | 실제 파일 읽기·쓰기 |
| `rpc-bind` | 111 | RPC 서비스 위치 안내 |
| `mountd` | 20048 | 마운트 요청 처리 (`showmount` 도 사용) |

- 포트만 열지 않고 **출발지 IP 까지 묶는다.** `/etc/exports` 와 방화벽 두 겹으로 막는 구조.
- `--permanent` 는 설정 파일에만 기록 → `--reload` 해야 실제 적용된다.

---

## 2. 웹서버 (192.168.56.101)

### 1) 패키지와 filehub 계정

📍 **WebServer**
```bash
# NFS 클라이언트 패키지 설치
dnf install -y nfs-utils

# filehub 계정이 없을 때만 만들기 (웹 앱 구축 때 이미 만들었으면 건너뜀, 반드시 UID 2000)
id filehub || { groupadd -g 2000 filehub; useradd -u 2000 -g 2000 -m -s /bin/bash filehub; }

# 계정 확인 (uid=2000)
id filehub
```
> `A || B` = A 가 실패했을 때만 B 를 실행. 계정이 이미 있으면 아무것도 하지 않는다.

### 2) 파일서버에서 무엇을 빌려주는지 보기

📍 **WebServer**
```bash
# 파일서버의 공유 목록 확인
showmount -e 192.168.56.102
```
> `/nfs/share 192.168.56.101` 이 보이면 파일서버 설정과 방화벽이 맞다.

### 3) `/mnt/nfs` 에 마운트 (재부팅해도 자동으로)

📍 **WebServer**
```bash
# 마운트할 폴더 만들기
mkdir -p /mnt/nfs

# 파일서버 /nfs/share 를 fstab 에 등록
echo '192.168.56.102:/nfs/share /mnt/nfs nfs defaults,_netdev,x-systemd.automount,nofail 0 0' >> /etc/fstab

# 바뀐 fstab 을 systemd 에 반영
systemctl daemon-reload

# 원격 파일시스템 마운트 다시 실행
systemctl restart remote-fs.target

# 마운트 확인 (SOURCE 가 192.168.56.102:/nfs/share 이면 성공)
findmnt /mnt/nfs
```

fstab 한 줄 읽는 법 :

| 칸 | 뜻 |
|---|---|
| `192.168.56.102:/nfs/share` | 어디서 : 파일서버의 /nfs/share |
| `/mnt/nfs` | 어디에 : 웹서버의 이 폴더 |
| `nfs` | 방식 |
| `_netdev` | 네트워크가 올라온 뒤에 마운트 |
| `x-systemd.automount` | 처음 접근할 때 자동으로 붙임 |
| `nofail` | 파일서버가 꺼져 있어도 웹서버 부팅은 계속 |
| `0 0` | dump 안 함 / 부팅 때 디스크 검사 안 함 |

- 로컬 디스크 줄은 `UUID=...` 로 시작하고, NFS 줄은 `IP:경로` 로 시작한다.
- 이 줄 때문에 `/mnt/nfs` 아래의 읽기·쓰기는 커널이 파일서버로 보낸다. **앱은 파일서버를 모르고** 그냥 `/mnt/nfs/uploads` 폴더에 쓸 뿐이다.
- `remote-fs.target` = 원격(네트워크) 파일시스템 마운트를 묶은 systemd 단위.

### 4) 쓰기 확인

📍 **WebServer**
```bash
# filehub 로 시험 파일 만들기
sudo -u filehub touch /mnt/nfs/uploads/hello_from_web

# 소유자 숫자가 2000 인지 확인
ls -ln /mnt/nfs/uploads/hello_from_web
```
> 파일서버에서도 `ls -ln /nfs/share/uploads/hello_from_web` 으로 확인한다. 같은 파일이 2000 소유로 보이면 두 서버가 같은 폴더를 보고 있는 것. (`ls -n` = 이름 대신 UID/GID 숫자로 표시)

```bash
# 시험 파일 지우기
sudo -u filehub rm /mnt/nfs/uploads/hello_from_web

# root 로 쓰면 거부되는 것 확인 (root_squash 동작)
touch /mnt/nfs/.loadtest/root_test 2>&1 || echo "root 는 쓰기 거부됨(정상)"
```

### 5) 앱 서비스가 NFS 를 기다리게 하기

📍 **WebServer**
```bash
# filehub.service 의 [Unit] 에 RequiresMountsFor=/mnt/nfs 줄이 있는지 확인
systemctl cat filehub | grep RequiresMountsFor

# 앱의 업로드 경로 확인 (기본값 /mnt/nfs/uploads)
grep -n NFS_DIR /opt/filehub/app/config.py
```
> NFS 가 붙기 전에 앱이 뜨면, 웹서버 **로컬 디스크의 빈 `/mnt/nfs`** 에 파일을 써 버린다. 이 줄이 그 사고를 막는다.

---

## 3. DB 서버 (192.168.56.103) — 백업 저장소

### 1) 패키지

📍 **DBServer**
```bash
# NFS 클라이언트 패키지 설치
dnf install -y nfs-utils

# 파일서버 공유 목록 확인 (/nfs/backup 192.168.56.103 이 보여야 함)
showmount -e 192.168.56.102
```

### 2) `/mnt/nfs` 에 마운트 (공유 폴더의 하위 `db` 만 붙인다)

📍 **DBServer**
```bash
# 마운트할 폴더 만들기
mkdir -p /mnt/nfs

# 파일서버 /nfs/backup/db 를 fstab 에 등록
echo '192.168.56.102:/nfs/backup/db /mnt/nfs nfs _netdev,x-systemd.automount,nofail 0 0' >> /etc/fstab

# 바뀐 fstab 을 systemd 에 반영
systemctl daemon-reload

# 원격 파일시스템 마운트 다시 실행
systemctl restart remote-fs.target

# 마운트 확인 (SOURCE 가 192.168.56.102:/nfs/backup/db)
findmnt /mnt/nfs
```

### 3) 쓰기 확인

📍 **DBServer**
```bash
# 시험 파일을 만들고 소유자를 본 뒤 지우기
touch /mnt/nfs/test && ls -l /mnt/nfs/test && rm /mnt/nfs/test
```
> 소유자가 **nobody** 로 나오면 정상이다. DB 서버의 root 가 썼지만 root_squash 때문에 파일서버에서는 nobody 로 기록된다.

### 4) 백업 스크립트와 cron (매일 03:00)

> [02. iSCSI](02-iscsi.md)(`/dbbackup`)와 [03. DB서버](03-db.md)를 마친 뒤 진행 (README 설치 순서 6단계)

📍 **DBServer**
```bash
# 덤프 → /dbbackup 저장 → NFS 로 복사하는 백업 스크립트 작성
cat > /usr/local/bin/db_backup.sh <<'EOF'
#!/bin/bash
set -e
F=/dbbackup/filehub_$(date +%F_%H%M).sql.gz
mysqldump --single-transaction --routines filehub | gzip > "$F"
find /dbbackup -name 'filehub_*.sql.gz' -mtime +7 -delete
rsync -a --no-owner --no-group --delete --include='filehub_*.sql.gz' --exclude='*' /dbbackup/ /mnt/nfs/
EOF

# 실행 권한 주기
chmod +x /usr/local/bin/db_backup.sh

# 한 번 직접 실행해 보기
/usr/local/bin/db_backup.sh

# 양쪽에 같은 filehub_날짜.sql.gz 가 있는지 확인
ls -lh /dbbackup /mnt/nfs

# 매일 03:00 자동 실행 등록
echo '0 3 * * * root /usr/local/bin/db_backup.sh >> /var/log/db_backup.log 2>&1' > /etc/cron.d/db_backup
```

스크립트 한 줄씩 :

| 줄 | 뜻 |
|---|---|
| `set -e` | 중간에 하나라도 실패하면 즉시 멈춤 (덤프 실패한 채 rsync 하지 않게) |
| `date +%F_%H%M` | `2026-10-30_0300` 형식 파일 이름 |
| `mysqldump --single-transaction` | InnoDB 를 잠그지 않고 일관된 시점으로 덤프 |
| `--routines` | 저장 프로시저·함수까지 포함 |
| `\| gzip >` | 압축해서 저장 |
| `find ... -mtime +7 -delete` | 7일 지난 덤프 삭제 |
| `rsync -a` | 권한·시간 등을 유지하며 복사 |
| `--no-owner --no-group` | 소유자 변경 시도 안 함 (root_squash 로 어차피 nobody) |
| `--include='filehub_*.sql.gz' --exclude='*'` | 덤프 파일만 복사 (부하 시험 파일 `demo_fill_*.dat` 이 섞이지 않게) |
| `--delete` | `/dbbackup` 에서 지운 덤프를 NFS 쪽에서도 지움 |

cron 한 줄 : `분 시 일 월 요일 실행계정 명령` → `0 3 * * *` = 매일 03시 00분

- 흐름 : `mysqldump → /dbbackup (iSCSI, sdc) → rsync → /mnt/nfs (NFS, sdb)`. **원본 DB 와 덤프 사본이 서로 다른 디스크에 남는다.**
- `/dbbackup`(lv_backup), `/var/lib/mysql`(lv_db) 은 [02. iSCSI](02-iscsi.md) 에서 만든다.

### 5) 복원 연습 (원래 DB 는 건드리지 않음)

📍 **DBServer**
```bash
# 복원용 빈 DB 만들기
mysql -e "CREATE DATABASE filehub_restore"

# NFS 의 덤프를 복원용 DB 에 풀기
gunzip -c /mnt/nfs/<덤프파일>.sql.gz | mysql filehub_restore

# 복원 결과 확인
mysql filehub_restore -e "SHOW TABLES; SELECT COUNT(*) FROM files;"

# 복원용 DB 지우기
mysql -e "DROP DATABASE filehub_restore"

# 실제 사고 때는 날아간 테이블만 옮긴다
mysqldump filehub_restore <테이블> | mysql filehub
```

- `gunzip -c` = 압축을 풀어 화면(표준출력)으로 내보냄 → 파이프로 mysql 에 바로 넣는다.
- 덤프 전체를 filehub 에 바로 돌리면 **멀쩡한 테이블까지 덤프 시점으로 되감긴다.**
- 복원 시점은 마지막 덤프(새벽 3시)까지다.

---

## 4. 전체 점검

### A. 파일서버

📍 **FileServer**
```bash
# /dev/sdb1 xfs /nfs 인지
df -Th /nfs

# /nfs/share → 56.101, /nfs/backup → 56.103 공유 확인
exportfs -v

# NFS 허용 규칙 확인
firewall-cmd --zone=work --list-rich-rules
```
> 56.101 · 56.103 말고 다른 IP(예: 예전 관제 56.104)가 남아 있으면 지운다 :  
> `firewall-cmd --permanent --zone=work --remove-rich-rule='<위 목록의 줄 그대로>'` 후 `--reload`

```bash
# 업로드·백업 사용량
du -sh /nfs/share/uploads /nfs/backup/db

# 팀 폴더 한도·사용량 (fh_general 2G, fh_team* 500M)
xfs_quota -x -c 'report -h -p' /nfs
```

### B. 웹서버

📍 **WebServer**
```bash
# 192.168.56.102:/nfs/share 가 붙었는지
findmnt /mnt/nfs

# NFS 버전, 서버 주소
nfsstat -m

# 파일서버 /nfs/share/uploads 와 같은 내용인지
ls -l /mnt/nfs/uploads
```

### C. DB 서버

📍 **DBServer**
```bash
# 192.168.56.102:/nfs/backup/db 가 붙었는지
findmnt /mnt/nfs

# 매일 filehub_날짜_0300.sql.gz 가 쌓이는지
ls -lh /mnt/nfs
```

---

## 5. 자주 막히는 곳

**(1) `showmount` 가 멈추거나 "No route to host"**  
→ 파일서버 방화벽. [1-10)](#10-방화벽-work-존-필요한-서버-ip만) 의 rich rule 에 이 서버 IP 가 있는지 확인.
```bash
# 📍 FileServer
firewall-cmd --zone=work --list-rich-rules
```

**(2) mount 할 때 "access denied by server"**  
→ 파일서버 `/etc/exports` 에 이 서버 IP 가 없거나 `exportfs -ra` 를 안 함.

**(3) 웹 앱이 업로드에서 "저장소에 쓰지 못했습니다"**  
→ UID 불일치. 웹서버와 파일서버 둘 다 `id filehub` 가 2000 인지, `ls -ln /nfs/share/uploads` 의 소유자 숫자가 2000 인지 확인.

**(4) root 로 쓰면 Permission denied**  
→ 정상 (root_squash). 웹 쪽은 `sudo -u filehub` 로, 백업 폴더는 nobody 소유로.

**(5) 재부팅 후 `/mnt/nfs` 가 비어 있음**  
→ `findmnt /mnt/nfs` 로 붙었는지 확인. 안 붙었으면 다시 마운트하고 로그를 본다.
```bash
# 원격 파일시스템 마운트 다시 실행
systemctl restart remote-fs.target

# 이번 부팅의 NFS 관련 로그 확인
journalctl -b | grep -i nfs
```

**(6) 파일서버를 끈 채 웹서버를 켜면?**  
→ `nofail` 덕분에 웹서버는 부팅된다. filehub 앱은 `RequiresMountsFor` 때문에 NFS 가 붙을 때까지 뜨지 않는다. **파일서버를 먼저 켜는 게 순서.**

**(7) 업로드에서 507 "디스크 한도 가득 참"**  
→ 팀 폴더가 XFS 프로젝트 쿼터(1-7)를 다 씀. 디스크가 정상적으로 막은 것.

| 코드 | 막은 곳 |
|---|---|
| 413 "폴더 용량 한도" | 앱 (DB `quota_mb`) |
| 507 "디스크 한도 가득 참" | 파일서버 XFS 디스크 쿼터 |

```bash
# 📍 FileServer — 팀별 사용량 확인
xfs_quota -x -c 'report -h -p' /nfs
```
