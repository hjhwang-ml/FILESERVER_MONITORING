# 02. 파일서버 · iSCSI

> 파일서버 sdc → iSCSI → DB 서버 LVM → MariaDB 데이터 · 백업 · 자동 증설  
> 기준 : Rocky Linux 10, 2026-10-26 실제 구성

[← 01. NFS](01-nfs.md) · [README](../README.md) · [03. DB서버 →](03-db.md)

---

## 목차

- [0. 이 문서로 만드는 것](#0-이-문서로-만드는-것)
- [1. 파일서버 (192.168.56.102) — 타깃](#1-파일서버-19216856102--타깃)
- [2. DB 서버 (192.168.56.103) — 이니시에이터 + LVM](#2-db-서버-19216856103--이니시에이터--lvm)
- [3. 무중단 증설과 자동 증설 (DB 서버)](#3-무중단-증설과-자동-증설-db-서버)
- [4. 전체 점검](#4-전체-점검)
- [5. 자주 막히는 곳](#5-자주-막히는-곳)

---

## 0. 이 문서로 만드는 것

**iSCSI = 디스크 공유.** 파일서버가 디스크(sdc)를 포맷하지 않은 채 통째로 빌려주고, DB 서버가 랜선으로 그 디스크를 받아 자기 디스크처럼 LVM·XFS 를 직접 만든다. NFS(폴더 공유)와 달리 **파일시스템의 주인은 빌려 쓰는 DB 서버**다.

```
[파일서버 192.168.56.102]  타깃 (빌려주는 쪽)
  sdc 100G (포맷 안 함)
    → 백스토어 block/lun0 (by-id 경로로 지정)
    → 타깃 iqn.2026-10.com.cloud:fileserver
    → LUN 0
    → ACL: iqn.2026-10.com.cloud:db 만 허용 + CHAP
    → 포털 192.168.56.102:3260

  ═══ 랜선 · 포트 3260 · DB 의 IQN + CHAP 비밀번호로 로그인 ═══

[DB 서버 192.168.56.103]  이니시에이터 (빌려 쓰는 쪽)
  로그인하면 새 디스크가 생김 (DB 서버에선 sdb 로 보임)
    → PV → VG vg_db (100G)
        ├ lv_db      40G → XFS → /var/lib/mysql  (MariaDB 실제 데이터)
        ├ lv_backup  30G → XFS → /dbbackup       (덤프 원본)
        └ 여유 30G (자동 증설용, 비워 둠)
```

> 같은 디스크를 파일서버는 **sdc**, DB 서버는 **sdb** 로 부른다. 각 서버가 자기한테 붙은 순서대로 이름을 붙이기 때문. 그래서 이름 대신 **by-id 경로, by-path 경로, IQN** 처럼 바뀌지 않는 이름을 쓴다.

### 용어

| 용어 | 뜻 |
|---|---|
| 타깃 (target) | 디스크를 빌려주는 쪽 = 파일서버 |
| 이니시에이터 (initiator) | 디스크를 빌려 쓰는 쪽 = DB 서버 |
| IQN | iSCSI 이름표. `iqn.날짜.도메인거꾸로:이름` (파일서버 `iqn.2026-10.com.cloud:fileserver`, DB 서버 `iqn.2026-10.com.cloud:db`) |
| LUN | 타깃이 내보내는 디스크의 번호 (건물 안의 호실 번호) |
| 포털 (portal) | 타깃이 접속을 기다리는 IP:포트 |
| ACL | 어느 이니시에이터(IQN)에게 빌려줄지 |
| CHAP | 접속할 때 확인하는 아이디·비밀번호 |
| 백스토어 (backstore) | 빌려줄 실물 디스크를 targetcli 에 등록한 것 |
| PV / VG / LV | LVM 의 디스크 등록 / 디스크 묶음 / 잘라 쓰는 칸 |

### 설정 파일이 있는 곳

| 서버 | 파일 | 내용 |
|---|---|---|
| 파일서버 | `/etc/target/saveconfig.json` | 타깃 전체 설정 (targetcli 로만 바꿈) |
| 파일서버 | `/etc/lvm/lvm.conf` | `global_filter` 로 sdc 제외 |
| DB 서버 | `/etc/iscsi/initiatorname.iscsi` | 내 IQN |
| DB 서버 | `/etc/iscsi/iscsid.conf` | CHAP 아이디·비밀번호, 자동 로그인 |
| DB 서버 | `/var/lib/iscsi/nodes/` | discovery 때 생기는 접속 대상 기록 |
| DB 서버 | `/etc/fstab` | LV 두 개를 폴더에 붙이는 줄 |
| DB 서버 | `/etc/systemd/system/mariadb.service.d/` | DB 가 디스크를 기다리게 |

- 명령은 전부 root 로 실행한다. 각 단계 제목의 서버 이름을 꼭 확인할 것.
- `<꺾쇠>` 로 표시한 값은 각자 환경에 맞게 바꾼다.

---

## 1. 파일서버 (192.168.56.102) — 타깃

### 1) 패키지 설치와 서비스

📍 **FileServer**
```bash
# iSCSI 타깃 관리 도구 설치
dnf install -y targetcli

# target 서비스 시작 및 부팅 시 자동 시작
systemctl enable --now target
```
> `target.service` 는 부팅 때 `/etc/target/saveconfig.json` 을 읽어 타깃을 다시 만든다. 이게 꺼져 있으면 재부팅 후 타깃이 사라진다.

### 2) iSCSI 로 내줄 디스크 확인 ★ 가장 중요

📍 **FileServer**
```bash
# 붙어 있는 디스크 목록 확인
lsblk

# 디스크 시리얼(by-id) 목록 확인
ls -l /dev/disk/by-id/ | grep -v part
```

- 파일서버의 100G 디스크 두 개 :
  - **sdb → NFS 용** (이미 `/nfs` 에 마운트돼 있음. 절대 건드리지 않는다)
  - **sdc → iSCSI 용** (파티션도 마운트도 없는 빈 디스크)
- by-id 목록에서 `../../sdc` 를 가리키는 이름을 찾아 적어 둔다.  
  우리 환경 예 : `ata-VBOX_HARDDISK_VBdad1d094-927c6ca7 -> ../../sdc`
- VM 마다 이 값이 다르니 **반드시 자기 서버에서 확인한 값**을 쓴다.

```bash
# sdc 의 by-id 경로를 변수에 저장
DISK=/dev/disk/by-id/<위에서 찾은 sdc 의 by-id 이름>

# ../../sdc 를 가리키는지 한 번 더 확인
ls -l $DISK

# 아래에 아무것도 없어야 함 (빈 디스크)
lsblk $DISK
```
> sdc 대신 by-id 를 쓰는 이유 : VirtualBox 에서 디스크 순서가 바뀌면 sdc 가 sdb 로 바뀔 수 있다. by-id 는 디스크 시리얼이라 바뀌지 않는다.

### 3) targetcli 로 타깃 만들기

📍 **FileServer**
```bash
# ① 백스토어 : 빌려줄 실물 디스크 등록 (이름 lun0 은 우리가 지은 이름)
targetcli /backstores/block create name=lun0 dev=$DISK

# ② 타깃 : 파일서버의 iSCSI 이름표
targetcli /iscsi create iqn.2026-10.com.cloud:fileserver

# ③ LUN : 이 타깃이 내보낼 디스크 0번 = ①의 백스토어
targetcli /iscsi/iqn.2026-10.com.cloud:fileserver/tpg1/luns create /backstores/block/lun0

# ④ 포털 : 모든 NIC(0.0.0.0) 기본 포털 지우기
targetcli /iscsi/iqn.2026-10.com.cloud:fileserver/tpg1/portals delete 0.0.0.0 3260

# ④ 포털 : 서버망 IP 하나에서만 접속받기
targetcli /iscsi/iqn.2026-10.com.cloud:fileserver/tpg1/portals create 192.168.56.102 3260

# ⑤ ACL : DB 서버의 IQN 만 허용 (LUN 0 이 자동으로 mapped_lun0 으로 연결됨)
targetcli /iscsi/iqn.2026-10.com.cloud:fileserver/tpg1/acls create iqn.2026-10.com.cloud:db

# ⑥ CHAP : 그 ACL 에 아이디·비밀번호 (DB 서버 iscsid.conf 와 같은 값)
targetcli /iscsi/iqn.2026-10.com.cloud:fileserver/tpg1/acls/iqn.2026-10.com.cloud:db \
  set auth userid=dbiscsi password=<CHAP 비밀번호>

# ⑦ 저장 ★ 안 하면 재부팅 때 타깃이 통째로 사라진다
targetcli saveconfig
```

- targetcli 는 폴더 구조처럼 생긴 설정 트리다. `경로 + 동작(create/delete/set)` 으로 한 줄씩 실행한다.
- `tpg1` = Target Portal Group 1. 포털·LUN·ACL 을 묶는 설정 단위.
- ⑥ CHAP 비밀번호는 12자 이상으로 정한다. **이 문서에는 적지 않는다.**

### 4) 결과 확인

📍 **FileServer**
```bash
# 타깃 전체 구조 확인 (by-id 값은 서버마다 다름)
targetcli ls
```
```
o- backstores
| o- block
|   o- lun0 ............ [/dev/disk/by-id/ata-VBOX_... (100.0GiB) write-thru activated]
o- iscsi
  o- iqn.2026-10.com.cloud:fileserver
    o- tpg1 ............ [no-gen-acls, auth per-acl]
      o- acls
      | o- iqn.2026-10.com.cloud:db ...... [1-way auth, Mapped LUNs: 1]
      |   o- mapped_lun0 ................. [lun0 block/lun0 (rw)]
      o- luns
      | o- lun0 .......................... [block/lun0 (...)]
      o- portals
        o- 192.168.56.102:3260 ........... [OK]
```

| 표시 | 뜻 |
|---|---|
| `backstores/block/lun0` | sdc 를 빌려줄 디스크로 등록 (이름) |
| `tpg1` | 이 타깃의 설정 묶음 1번 |
| `no-gen-acls` | ACL 에 적힌 IQN 만 허용 (`gen-acls` 면 아무나) |
| `auth per-acl` | CHAP 을 ACL 마다 따로 둠 |
| `1-way auth` | 파일서버가 DB 의 비밀번호를 확인 (반대 방향 확인은 안 함) |
| `mapped_lun0 (rw)` | DB 에게 LUN 0 을 읽기·쓰기로 보여 줌 |
| `luns/lun0` | 내보내는 디스크 번호 0 (위의 이름 lun0 과 우연히 같을 뿐) |
| `write-thru` | 디스크에 실제로 쓴 뒤 완료라고 답함 (NFS 의 sync 와 비슷) |

```bash
# 방금 저장한 시각인지 확인
ls -l /etc/target/saveconfig.json
```

### 5) 방화벽 (3260 은 DB 서버만)

📍 **FileServer**
```bash
# DB 서버(56.103)에게만 iSCSI 허용
firewall-cmd --permanent --zone=work \
  --add-rich-rule='rule family="ipv4" source address="192.168.56.103" service name="iscsi-target" accept'

# 방화벽 설정 다시 읽기
firewall-cmd --reload

# 허용 규칙 확인
firewall-cmd --zone=work --list-rich-rules | grep iscsi
```
> firewalld 의 `iscsi-target` 서비스 = TCP 3260

### 6) 파일서버 LVM 이 sdc 를 보지 않게 막기

DB 서버가 sdc 위에 `vg_db` 를 만들면, 파일서버도 sdc 를 물리적으로 갖고 있어서 파일서버 LVM 이 "여기 vg_db 가 있네" 하고 활성화할 수 있다. **두 서버가 같은 LVM 을 동시에 만지면 데이터가 깨진다.** 그래서 파일서버에게는 sdc 를 아예 보지 말라고 막는다. (2부에서 DB 서버가 vg_db 를 만들기 전에 해 두는 게 안전하다)

📍 **FileServer**
```bash
# LVM 설정 파일 열기
vi /etc/lvm/lvm.conf
```
`devices { ... }` 구역 안에 아래 한 줄 추가 (by-id 는 1-2 에서 찾은 값)
```
global_filter = [ "r|/dev/disk/by-id/<sdc 의 by-id 이름>|", "a|.*|" ]
```
> `r| |` = 이 경로는 무시(reject), `a|.*|` = 나머지는 전부 허용(accept). 앞에서부터 순서대로 판단한다.

```bash
# 부팅 이미지(initramfs)에도 반영 — 부팅 초기에 LVM 이 sdc 를 잡지 않게
dracut -f

# 파일서버에서 vg_db 가 보이면 안 됨
pvs; vgs
```

---

## 2. DB 서버 (192.168.56.103) — 이니시에이터 + LVM

### 1) 패키지와 내 이름표(IQN)

📍 **DBServer**
```bash
# iSCSI 이니시에이터 패키지 설치
dnf install -y iscsi-initiator-utils

# DB 서버의 IQN 지정
echo 'InitiatorName=iqn.2026-10.com.cloud:db' > /etc/iscsi/initiatorname.iscsi

# IQN 확인
cat /etc/iscsi/initiatorname.iscsi
```
> 이 이름이 파일서버 ACL(1-3 ⑤)에 적은 이름과 **글자 하나까지 같아야** 한다.

### 2) CHAP 아이디·비밀번호

📍 **DBServer**
```bash
# iSCSI 설정 파일 열기
vi /etc/iscsi/iscsid.conf
```
아래 세 줄의 `#` 을 지우고 값을 넣는다 (파일서버 1-3 ⑥과 같은 값)
```ini
node.session.auth.authmethod = CHAP
node.session.auth.username = dbiscsi
node.session.auth.password = <CHAP 비밀번호>

# 이 줄도 확인 (부팅 때 자동 로그인)
node.startup = automatic
```

```bash
# CHAP·자동 로그인 설정이 들어갔는지 확인
grep -E '^node\.(startup|session\.auth\.(authmethod|username))' /etc/iscsi/iscsid.conf

# iscsid, iscsi 서비스 시작 및 부팅 시 자동 시작
systemctl enable --now iscsid iscsi

# 바뀐 설정 반영을 위해 iscsid 재시작
systemctl restart iscsid
```

- `iscsid` = 실제 iSCSI 세션을 관리하는 데몬, `iscsi` = 부팅 때 기록된 노드에 자동 로그인하는 서비스.
- **discovery 를 하기 전에 CHAP 을 넣어야 한다.** discovery 때 이 파일의 값이 노드 기록(`/var/lib/iscsi/nodes/`)으로 복사된다.

### 3) 타깃 찾기 (discovery) → 로그인

📍 **DBServer**
```bash
# 파일서버에 어떤 타깃이 있는지 묻기
iscsiadm -m discovery -t sendtargets -p 192.168.56.102
# 192.168.56.102:3260,1 iqn.2026-10.com.cloud:fileserver

# 로그인 → 이 순간 DB 서버에 새 디스크가 생긴다
iscsiadm -m node -T iqn.2026-10.com.cloud:fileserver -p 192.168.56.102 --login

# 붙어 있는 타깃 확인
iscsiadm -m session

# 새 100G 디스크 확인 (보통 sdb)
lsblk

# by-path 이름 확인
ls -l /dev/disk/by-path/ | grep iscsi
# ip-192.168.56.102:3260-iscsi-iqn.2026-10.com.cloud:fileserver-lun-0 -> ../../sdb
```

| 옵션 | 뜻 |
|---|---|
| `-m discovery` | 탐색 모드 |
| `-t sendtargets` | 타깃에게 "가진 목록을 보내 달라" 고 묻는 방식 |
| `-p` | 포털 주소 |
| `-m node -T <IQN>` | 기록된 노드 중 이 타깃을 대상으로 |
| `--login` | 로그인 (`--logout` 은 해제) |
| `-m session` | 현재 연결된 세션 목록 |

by-path 이름 읽는 법 :
```
ip-192.168.56.102:3260 - iscsi-iqn.2026-10.com.cloud:fileserver - lun-0
└──── 포털 ─────────┘    └────────── 타깃 IQN ──────────────┘   └ LUN ┘
```
> "어느 주소의, 어느 타깃의, 몇 번 디스크" 가 이름에 그대로 들어 있다.

### 4) LVM 만들기 (PV → VG → LV)

📍 **DBServer**
```bash
# iSCSI 디스크의 by-path 경로를 변수에 저장
DEV=/dev/disk/by-path/ip-192.168.56.102:3260-iscsi-iqn.2026-10.com.cloud:fileserver-lun-0

# ① 이 디스크를 LVM 용으로 등록
pvcreate $DEV

# ② 디스크 묶음 vg_db (100G)
vgcreate vg_db $DEV

# ③ 40G 잘라 DB 데이터용
lvcreate -n lv_db -L 40G vg_db

# 30G 잘라 백업용 (나머지 약 30G 는 일부러 비워 둔다, 증설용)
lvcreate -n lv_backup -L 30G vg_db

# PV · VG · LV 확인
pvs; vgs; lvs
```

| 명령 | 뜻 |
|---|---|
| `pvcreate` | 디스크를 LVM 물리 볼륨(PV)으로 초기화 |
| `vgcreate vg_db` | PV 들을 묶어 볼륨 그룹(VG) 생성 |
| `lvcreate -n 이름 -L 크기` | VG 에서 정해진 크기만큼 논리 볼륨(LV) 생성 |

> 비유 : **PV = 땅 한 필지, VG = 합친 부지, LV = 칸막이로 나눈 창고.**  
> 일반 파티션은 울타리를 박은 것이라 늘리기 어렵고, LVM 은 칸막이를 밀어 남은 공간을 붙일 수 있다.  
> (NFS 쪽 sdb 는 LVM 없이 파티션으로 꽉 채워 만들어서, 늘리려면 파일서버를 꺼야 한다)

### 5) XFS 포맷 → 마운트 (재부팅해도 자동으로)

📍 **DBServer**
```bash
# DB 데이터용 LV 포맷
mkfs.xfs /dev/vg_db/lv_db

# 백업용 LV 포맷
mkfs.xfs /dev/vg_db/lv_backup

# 백업 마운트 폴더 만들기
mkdir -p /dbbackup

# 부팅 때 자동 마운트되도록 fstab 에 두 줄 등록
cat >> /etc/fstab <<'EOF'
/dev/vg_db/lv_db      /var/lib/mysql  xfs  _netdev,nofail  0 0
/dev/vg_db/lv_backup  /dbbackup       xfs  _netdev,nofail  0 0
EOF

# 바뀐 fstab 을 systemd 에 반영
systemctl daemon-reload
```

| 옵션 | 뜻 |
|---|---|
| `_netdev` | 네트워크 디스크다. 네트워크와 iSCSI 로그인 뒤에 마운트하라 |
| `nofail` | 파일서버가 꺼져 있어도 DB 서버 부팅은 계속하라 |

> ⚠️ `/var/lib/mysql` 은 2-6 에서 데이터를 옮긴 뒤에 마운트한다. 지금 바로 `mount -a` 하면 **기존 MariaDB 데이터 위를 빈 디스크가 덮는다.**

```bash
# 백업 폴더만 먼저 마운트
mount /dbbackup

# 마운트 확인 (/dev/mapper/vg_db-lv_backup xfs 30G)
df -Th /dbbackup
```

### 6) MariaDB 데이터를 iSCSI 디스크로 옮기기

> [03. DB서버](03-db.md)(MariaDB 설치 · 스키마)를 마친 뒤 진행 (README 설치 순서 5단계)  
> MariaDB 를 이미 설치해서 쓰고 있는 경우의 순서. (설치 전이라면 마운트 후 설치)

📍 **DBServer**
```bash
# MariaDB 중지
systemctl stop mariadb

# 원본을 로컬에 대피 (만일을 위해 남겨 둠)
rsync -aAXH /var/lib/mysql/ /var/lib/mysql.local/

# 새 LV 를 붙일 임시 폴더 만들기
mkdir -p /mnt/newdb

# 새 LV 를 임시 위치에 마운트
mount /dev/vg_db/lv_db /mnt/newdb

# 데이터 복사
rsync -aAXH /var/lib/mysql/ /mnt/newdb/

# 임시 마운트 해제
umount /mnt/newdb

# 이제 fstab 대로 /var/lib/mysql 에 마운트
mount /var/lib/mysql

# 소유자를 mysql 로
chown mysql:mysql /var/lib/mysql

# SELinux 라벨 복구 (mysqld_db_t)
restorecon -Rv /var/lib/mysql

# 마운트 확인 (/dev/mapper/vg_db-lv_db xfs 40G)
df -Th /var/lib/mysql
```

| rsync 옵션 | 뜻 |
|---|---|
| `-a` | 권한·소유자·시간 등을 그대로 유지 (archive) |
| `-A` | ACL 유지 |
| `-X` | 확장 속성(SELinux 라벨 등) 유지 |
| `-H` | 하드 링크 유지 |
| 경로 끝의 `/` | 폴더 자체가 아니라 **폴더 안의 내용**을 복사 |

> `restorecon` 을 안 하면 SELinux 가 MariaDB 의 데이터 접근을 막아 서비스가 뜨지 않는다.

### 7) MariaDB 가 디스크를 기다리게 하기

📍 **DBServer**
```bash
# MariaDB 서비스 덮어쓰기 폴더 만들기
mkdir -p /etc/systemd/system/mariadb.service.d

# /var/lib/mysql 이 마운트된 뒤에만 시작하도록 설정
cat > /etc/systemd/system/mariadb.service.d/iscsi.conf <<'EOF'
[Unit]
RequiresMountsFor=/var/lib/mysql
EOF

# 바뀐 서비스 설정 다시 읽기
systemctl daemon-reload

# MariaDB 시작
systemctl start mariadb

# 실행 상태 확인
systemctl status mariadb --no-pager

# 데이터가 그대로인지 확인
mysql -e "SHOW DATABASES; SELECT COUNT(*) FROM filehub.users;"
```

- `서비스.d/*.conf` = **drop-in**. 원래 서비스 파일은 그대로 두고 일부 설정만 덧붙이는 방법 (패키지 업데이트에도 유지됨).
- iSCSI 디스크보다 MariaDB 가 먼저 뜨면, 로컬 디스크의 빈 `/var/lib/mysql` 로 DB 가 시작돼 버린다. 이 설정이 그 사고를 막는다.

### 8) 재부팅 테스트

📍 **DBServer**
```bash
# 재부팅
reboot

# 다시 들어와서 자동 로그인 됐는지
iscsiadm -m session

# lv_db, lv_backup 활성 확인
lvs

# 둘 다 붙었는지
findmnt /var/lib/mysql /dbbackup

# MariaDB 가 active 인지
systemctl is-active mariadb
```
> 기대하는 순서 : **네트워크 → iSCSI 로그인 → LVM 활성 → 마운트 → MariaDB 시작**

---

## 3. 무중단 증설과 자동 증설 (DB 서버)

### 1) 손으로 증설해 보기

📍 **DBServer**
```bash
# 증설 전 용량 확인
df -h /dbbackup

# lv_backup 을 3G 늘리고 XFS 도 함께 확장
lvextend -r -L +3G /dev/vg_db/lv_backup

# 3G 늘어났는지 확인 (서비스는 그대로)
df -h /dbbackup

# VFree 가 3G 줄었는지 확인
vgs
```

- `-L +3G` = 지금 크기에서 3G 더. (`-L 33G` 처럼 쓰면 최종 크기 지정)
- `-r` = LV 를 늘리면서 그 위의 XFS 도 같이 늘린다 (`xfs_growfs` 를 자동 실행).
- XFS 는 **늘릴 수만 있고 줄일 수는 없다.** 함부로 늘리지 말 것.
- 증설은 파일시스템 주인(DB 서버)이 한다. 파일서버는 할 일이 없다.

### 2) 자동 증설 스크립트

**규칙** : 사용률 85% 이상이 2번 연속(15초 간격)이면 3G 증설. LV 별 상한(`lv_db` 55G, `lv_backup` 45G)을 넘거나 VG 여유가 5G 미만이면 증설하지 않고 "수동 조치 필요" 경고만 남긴다. 결과는 syslog 태그 `storage-load` 로 남아 **Loki → Grafana → Slack.**

📍 **DBServer**
```bash
# 자동 증설 스크립트 작성
cat > /usr/local/bin/lv_autoextend.sh <<'EOF'
#!/bin/bash
# iSCSI LV 자동 증설: AUTO_AT% 이상이 HITS번 연속이면 STEP 만큼 증설
AUTO_AT=85; HITS=2; STEP=3G; MIN_FREE_G=5; TAG=storage-load
declare -A MAX_G=( [lv_backup]=45 [lv_db]=55 )
TARGETS="/dev/vg_db/lv_backup /dev/vg_db/lv_db"
[ -f /etc/lv-autoextend.conf ] && . /etc/lv-autoextend.conf
STATE=/run/lv_autoextend; mkdir -p $STATE
log() { logger -t $TAG -p user.$1 "$2 user=auto"; }
for LV in $TARGETS; do
  NAME=$(basename "$LV")
  MNT=$(findmnt -no TARGET "$LV") || continue
  USE=$(df --output=pcent "$MNT" | tail -1 | tr -dc 0-9)
  if [ "$USE" -lt "$AUTO_AT" ]; then rm -f $STATE/$NAME; continue; fi
  N=$(( $(cat $STATE/$NAME 2>/dev/null || echo 0) + 1 )); echo $N > $STATE/$NAME
  [ "$N" -lt "$HITS" ] && continue
  rm -f $STATE/$NAME
  VG=$(lvs --noheadings -o vg_name "$LV" | tr -d ' ')
  FREE=$(vgs --noheadings --units g --nosuffix -o vg_free "$VG" | tr -d ' ')
  SIZE=$(lvs --noheadings --units g --nosuffix -o lv_size "$LV" | tr -d ' ')
  if awk "BEGIN{exit !($FREE < $MIN_FREE_G)}"; then
    log warning "level=warn event=auto_extend_skipped storage=iSCSI mount=$MNT used=${USE}% vg_free=${FREE}G msg=\"VG 여유 부족 - 수동 조치 필요\""
    continue
  fi
  if awk "BEGIN{exit !($SIZE + ${STEP%G} > ${MAX_G[$NAME]:-50})}"; then
    log warning "level=warn event=auto_extend_skipped storage=iSCSI mount=$MNT used=${USE}% size=${SIZE}G max=${MAX_G[$NAME]:-50}G msg=\"자동 증설 상한 도달 - 수동 조치 필요\""
    continue
  fi
  if lvextend -r -L +$STEP "$LV" >/tmp/lvae.$$ 2>&1; then
    log info "level=info event=action action=auto_extend storage=iSCSI mount=$MNT lv=$LV size_before=${SIZE}G size_after=$(lvs --noheadings --units g --nosuffix -o lv_size "$LV" | tr -d ' ')G before=${USE}% after=$(df --output=pcent "$MNT" | tail -1 | tr -d ' ') vg_free=$(vgs --noheadings --units g --nosuffix -o vg_free "$VG" | tr -d ' ')G msg=\"자동 증설 조치\""
  else
    log err "level=error event=action_failed action=auto_extend storage=iSCSI mount=$MNT err=\"$(tail -1 /tmp/lvae.$$ | tr '"' "'")\" msg=\"자동 증설 실패\""
  fi
  rm -f /tmp/lvae.$$
done
EOF

# 실행 권한 주기
chmod +x /usr/local/bin/lv_autoextend.sh
```

스크립트 동작 순서 :

| 단계 | 하는 일 |
|---|---|
| 설정값 | `AUTO_AT=85`(임계 %), `HITS=2`(연속 횟수), `STEP=3G`(증설 단위), `MIN_FREE_G=5`(VG 최소 여유), `MAX_G`(LV별 상한) |
| `/etc/lv-autoextend.conf` | 있으면 읽어서 위 값을 덮어씀 (시험용 임계치 조정) |
| `log()` | `logger -t storage-load` 로 syslog 에 key=value 형식 기록 → Alloy 가 Loki 로 보냄 |
| `findmnt -no TARGET` | LV 가 어디에 마운트돼 있는지 (없으면 건너뜀) |
| `df --output=pcent` | 사용률(%)만 출력, `tr -dc 0-9` 로 숫자만 남김 |
| 상태 파일 `/run/lv_autoextend/<LV>` | 연속으로 임계치를 넘은 횟수 기록 (임계치 아래로 내려가면 삭제) |
| VG 여유 검사 | 5G 미만이면 `auto_extend_skipped` 경고 후 건너뜀 |
| 상한 검사 | 지금 크기 + 3G 가 상한 초과면 `auto_extend_skipped` 경고 |
| `lvextend -r -L +3G` | 성공 시 `action=auto_extend` (전후 크기·사용률·VG 여유 기록), 실패 시 `action_failed` |

> `awk "BEGIN{exit !(조건)}"` = 소수점 비교를 위해 awk 로 계산. 조건이 참이면 exit 0 → if 가 참.

### 3) 15초마다 돌리는 타이머

📍 **DBServer**
```bash
# 검사 1회를 실행하는 서비스 파일 작성
cat > /etc/systemd/system/lv-autoextend.service <<'EOF'
[Unit]
Description=iSCSI LV auto extend check
[Service]
Type=oneshot
ExecStart=/usr/local/bin/lv_autoextend.sh
EOF

# 15초마다 서비스를 실행하는 타이머 파일 작성
cat > /etc/systemd/system/lv-autoextend.timer <<'EOF'
[Unit]
Description=Run iSCSI LV auto extend every 15s
[Timer]
OnBootSec=1min
OnUnitActiveSec=15s
AccuracySec=1s
[Install]
WantedBy=timers.target
EOF

# 새 서비스·타이머 파일 다시 읽기
systemctl daemon-reload

# 타이머 시작 및 부팅 시 자동 시작
systemctl enable --now lv-autoextend.timer

# 타이머가 돌고 있는지 확인
systemctl list-timers | grep lv-autoextend
```

| 항목 | 뜻 |
|---|---|
| `Type=oneshot` | 한 번 실행하고 끝나는 작업 |
| `OnBootSec=1min` | 부팅 1분 뒤 첫 실행 |
| `OnUnitActiveSec=15s` | 마지막 실행 15초 뒤 다시 실행 |
| `AccuracySec=1s` | 실행 시각 오차 1초 (기본 1분이라 15초 간격이 흐트러짐) |
| `WantedBy=timers.target` | 부팅 시 타이머 자동 활성 |

- cron 은 최소 1분 단위라 15초 간격은 systemd 타이머로 만든다.
- 이미 만들어 둔 서버라면 위 두 파일 대신 `systemctl cat lv-autoextend.timer` 로 기존 설정을 확인한다.
- 리허설처럼 증설 없이 부하만 볼 때는 `systemctl stop lv-autoextend.timer` 로 멈추고, 본 시연 전에 다시 start 한다 ([06. 시연 가이드](06-demo.md)). 증설은 되돌릴 수 없어 VG 여유를 아끼기 위함.

### 4) 시연용 부하 스크립트 배치

📍 **DBServer**
```bash
# 부하 스크립트 배치 (storage_load.sh 를 /root 에 올려 둔 뒤)
install -m 755 /root/storage_load.sh /usr/local/bin/storage_load.sh

# I/O 부하(io 명령)에 쓰는 fio 설치
dnf install -y fio
```

- `install -m 755` = 복사 + 권한 지정을 한 번에.
- NFS 용량 시연([06. 시연 가이드 3-5)](06-demo.md#5-선택-발표-외-nfs-저장소-용량--정리로-조치))을 하려면 **웹서버에도** 같은 두 명령을 실행한다.
- `storage_load.sh` 는 로컬 디스크를 대상으로 받지 않는다. 파일서버에서 `/nfs` 에 직접 돌리면 "로컬 디스크는 대상이 아님" 으로 거부된다.
- 부하를 걸어 자동 증설을 확인하는 명령은 [06. 시연 가이드 3-1)](06-demo.md#1-iscsi-백업-공간-부족--lvm-자동-증설-lv_backup) 에 모았다 (실제 시연 대상 lv_backup · /dbbackup).
- 리허설도 같은 명령으로 하되, **먼저 VirtualBox 스냅샷을 찍고 끝나면 스냅샷으로 되돌린다.** 자동 증설로 커진 lv_backup 은 줄일 수 없기 때문이다.

---

## 4. 전체 점검

### A. 파일서버

📍 **FileServer**
```bash
# target 이 enabled 인지
systemctl is-enabled target

# lun0 / fileserver / acl db (1-way auth) / portal OK 인지
targetcli ls

# 최근에 saveconfig 했는지
ls -l /etc/target/saveconfig.json

# sdc 아래에 아무것도 없어야 정상
lsblk /dev/sdc

# vg_db 가 보이면 안 됨
pvs; vgs

# iSCSI 허용 규칙 확인
firewall-cmd --zone=work --list-rich-rules | grep iscsi
```

### B. DB 서버

📍 **DBServer**
```bash
# iqn.2026-10.com.cloud:db 인지
cat /etc/iscsi/initiatorname.iscsi

# fileserver 타깃 1개가 붙어 있는지
iscsiadm -m session

# sdb → vg_db-lv_db, vg_db-lv_backup
lsblk

# vg_db 100G, 여유 확인
pvs; vgs; lvs

# 둘 다 xfs 로 붙어 있는지
df -Th /var/lib/mysql /dbbackup

# MariaDB 가 디스크를 기다리는 설정이 있는지
systemctl cat mariadb | grep RequiresMountsFor

# 자동 증설 타이머 확인
systemctl list-timers | grep lv-autoextend
```

---

## 5. 자주 막히는 곳

**(1) 재부팅했더니 파일서버의 타깃이 통째로 사라짐**  
- 원인 : `targetcli saveconfig` 를 안 했거나 `target.service` 가 꺼져 있음.
- 해결 : 서비스를 켜고 타깃을 다시 만든 뒤 반드시 saveconfig. (DB 서버는 이니시에이터라 targetcli 가 비어 있는 게 정상이다)
```bash
# 📍 FileServer
systemctl enable --now target
```

**(2) discovery 는 되는데 login 에서 "authorization failure"**  
- 원인 : IQN 또는 CHAP 불일치.
- 해결 : DB 서버의 IQN 과 파일서버 `targetcli ls` 의 acls 이름이 글자까지 같은지 확인. CHAP 을 discovery 뒤에 넣었다면 노드 기록에 반영이 안 됐으므로 직접 넣거나, `iscsiadm -m node -o delete` 후 discovery 부터 다시.
```bash
# 📍 DBServer — IQN 확인
cat /etc/iscsi/initiatorname.iscsi

# 노드 기록에 CHAP 비밀번호 직접 넣기
iscsiadm -m node -T iqn.2026-10.com.cloud:fileserver -p 192.168.56.102 \
  -o update -n node.session.auth.password -v '<CHAP 비밀번호>'
```

**(3) discovery 가 멈추거나 "No route to host"**  
→ 파일서버 방화벽 [1-5)](#5-방화벽-3260-은-db-서버만), 포털 IP [1-3) ④](#3-targetcli-로-타깃-만들기) 확인.

**(4) 파일서버에서 `pvs` 에 vg_db 가 보임**  
- 원인 : [1-6)](#6-파일서버-lvm-이-sdc-를-보지-않게-막기) `global_filter` 를 안 걸었거나 `dracut -f` 를 안 함.
- ⚠️ 파일서버에서 vg_db 를 활성화하거나 고치려 하지 말 것.

**(5) DB 서버 재부팅 후 MariaDB 가 빈 DB 로 뜨거나 안 뜸**  
→ `findmnt /var/lib/mysql` 로 iSCSI 디스크가 붙었는지 먼저. 안 붙었으면 `iscsiadm -m session`, 파일서버가 켜져 있는지 확인. [2-7)](#7-mariadb-가-디스크를-기다리게-하기) `RequiresMountsFor` 가 있으면 디스크 없이 뜨지는 않는다.

**(6) 서버를 켜는 순서**  
→ **파일서버 먼저, 그다음 DB 서버.** 반대로 하면 DB 서버가 디스크 없이 부팅된다 (`nofail` 이라 부팅은 되지만 MariaDB 는 디스크를 기다리며 안 뜬다).

**(7) 자동 증설이 안 일어남**  
→ 타이머가 돌고 있는지, `/etc/lv-autoextend.conf` 가 시험용으로 남아 있는지 확인. `journalctl -t storage-load` 에 `auto_extend_skipped` 가 있으면 상한 또는 VG 여유 부족.
```bash
# 📍 DBServer — 타이머 확인
systemctl list-timers | grep lv-autoextend

# 자동 증설 로그 확인
journalctl -t storage-load
```
