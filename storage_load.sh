#!/bin/bash
# 스토리지 부하 시연 (NFS · iSCSI 공용)
#   fill   <목표%> <조각GB> <경로...>   용량 채우기 (목표에서 정확히 멈춤)
#   io     <초> <경로>                 I/O 부하 (순차+랜덤+혼합)
#   stop                               실행 중인 부하 중지
#   clean  <경로...>                   테스트 파일 삭제 (조치)
#   extend <LV경로> <크기>             LV 증설 (조치, root)
#   status [경로...]                   실행 상태와 사용률
TAG=storage-load; WARN_AT=80; MAX_TARGET=95
PROG=$(basename "$0")

log() {
  local lvl=$1; shift
  local pri=info; [ "$lvl" = warn ] && pri=warning; [ "$lvl" = error ] && pri=err
  local line="$*" m="" e=""
  if [[ $line == *" msg="* ]]; then m=${line#* msg=}; line=${line%% msg=*}; fi
  if [[ $line == *" err="* ]]; then e=${line#* err=}; line=${line%% err=*}; e=${e//\"/\'}; line="$line err=\"$e\""; fi
  [ -n "$m" ] && line="$line msg=\"$m\""
  line="level=$lvl $line user=$(id -un)"
  echo "$(date +%T) $line"
  logger -t "$TAG" -p user.$pri "$line"
}
usage()   { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
kind_of() {
  local fs src
  fs=$(df --output=fstype "$1" 2>/dev/null | tail -1 | tr -d ' ')
  [[ $fs == nfs* ]] && { echo NFS; return; }
  src=$(df --output=source "$1" | tail -1)
  lsblk -s -no TRAN "$src" 2>/dev/null | grep -q iscsi && echo iSCSI || echo local
}
mnt_of()  { df --output=target "$1" | tail -1; }
pct_of()  { df --output=pcent "$1" | tail -1 | tr -dc 0-9; }
avail_g() { df --output=avail -B1G "$1" | tail -1 | tr -d ' '; }
check_dir() {
  [ -d "$1" ] || { echo "경로 없음: $1"; exit 1; }
  [ -w "$1" ] || { echo "쓰기 권한 없음: $1 (NFS는 sudo -u filehub 로 실행)"; exit 1; }
  [ "$(kind_of "$1")" = local ] && { echo "로컬 디스크는 대상이 아님: $1"; exit 1; }
}

fill_one() {
  local DIR=$1 KIND MNT i=0 warned=0 written=0 T0 SIZE USED LEFT BS S E P
  KIND=$(kind_of "$DIR"); MNT=$(mnt_of "$DIR"); T0=$(date +%s)
  trap 'log warn event=stopped mode=fill storage=$KIND mount=$MNT used=$(pct_of "$DIR")% avail=$(avail_g "$DIR")G written=${written}MB msg="부하 중단됨"; exit 1' TERM INT
  log info event=start mode=fill storage=$KIND mount=$MNT target=${TARGET}% chunk=${CHUNK_GB}G used=$(pct_of "$DIR")% avail=$(avail_g "$DIR")G msg="용량 채우기 시작"
  [ "$(pct_of "$DIR")" -ge "$WARN_AT" ] && warned=1
  while :; do
    read SIZE USED < <(df --output=size,used -B1M "$DIR" | tail -1)
    LEFT=$(( SIZE*TARGET/100 - USED )); [ "$LEFT" -le 0 ] && break
    BS=$(( CHUNK_GB*1024 )); [ "$LEFT" -lt "$BS" ] && BS=$LEFT
    i=$((i+1)); while [ -e "$DIR/demo_fill_$i.dat" ]; do i=$((i+1)); done
    S=$(date +%s%N)
    if ! dd if=/dev/zero of="$DIR/demo_fill_$i.dat" bs=1M count=$BS oflag=direct status=none 2>/tmp/sl_err.$$; then
      log error event=write_failed storage=$KIND mount=$MNT used=$(pct_of "$DIR")% err="$(head -1 /tmp/sl_err.$$)" msg="쓰기 실패"
      rm -f /tmp/sl_err.$$; exit 1
    fi
    E=$(( ($(date +%s%N)-S)/1000000 )); [ "$E" -lt 1 ] && E=1
    written=$((written+BS)); P=$(pct_of "$DIR")
    log info event=write storage=$KIND mount=$MNT file=demo_fill_$i.dat size=${BS}MB speed=$(( BS*1000/E ))MB/s used=${P}% avail=$(avail_g "$DIR")G
    if [ "$warned" -eq 0 ] && [ "$P" -ge "$WARN_AT" ]; then
      warned=1
      log warn event=threshold storage=$KIND mount=$MNT used=${P}% threshold=${WARN_AT}% avail=$(avail_g "$DIR")G msg="경보 기준 초과"
    fi
  done
  rm -f /tmp/sl_err.$$
  log warn event=done mode=fill storage=$KIND mount=$MNT used=$(pct_of "$DIR")% avail=$(avail_g "$DIR")G written=${written}MB elapsed=$(( $(date +%s)-T0 ))s msg="목표 도달 - 조치 필요"
}

io_run() {
  local DIR=$1 SEC=$2 KIND MNT T0
  command -v fio >/dev/null || { echo "fio 가 없습니다: dnf install -y fio"; exit 1; }
  [ "$(avail_g "$DIR")" -lt 10 ] && { echo "여유 공간 10G 이상 필요 (지금 $(avail_g "$DIR")G)"; exit 1; }
  KIND=$(kind_of "$DIR"); MNT=$(mnt_of "$DIR"); T0=$(date +%s)
  trap 'rm -f "$DIR"/io_*.dat; log warn event=stopped mode=io storage=$KIND mount=$MNT elapsed=$(( $(date +%s)-T0 ))s msg="I/O 부하 중단됨"; exit 1' TERM INT
  log info event=start mode=io storage=$KIND mount=$MNT duration=${SEC}s msg="I/O 부하 시작"
  fio --directory="$DIR" --direct=1 --ioengine=libaio --time_based --runtime="$SEC" --group_reporting \
    --name=sl_seq  --filename=io_seq.dat  --size=4G --rw=write     --bs=1M --iodepth=64 --numjobs=2 \
    --name=sl_rand --filename=io_rand.dat --size=2G --rw=randwrite --bs=4k --iodepth=64 --numjobs=8 \
    --name=sl_mix  --filename=io_mix.dat  --size=2G --rw=randrw --rwmixwrite=70 --bs=64k --iodepth=64 --numjobs=4
  rm -f "$DIR"/io_*.dat
  log info event=done mode=io storage=$KIND mount=$MNT elapsed=$(( $(date +%s)-T0 ))s msg="I/O 부하 종료"
}

clean_dir() {
  local DIR=$1 KIND MNT B FREED
  KIND=$(kind_of "$DIR"); MNT=$(mnt_of "$DIR"); B=$(pct_of "$DIR")
  FREED=$(du -cm "$DIR"/demo_fill_*.dat "$DIR"/io_*.dat 2>/dev/null | tail -1 | cut -f1)
  rm -f "$DIR"/demo_fill_*.dat "$DIR"/io_*.dat; sync; sleep 1
  log info event=action action=cleanup storage=$KIND mount=$MNT before=${B}% after=$(pct_of "$DIR")% freed=${FREED:-0}MB msg="테스트 파일 삭제 조치"
}

extend_lv() {
  local LV=$1 ADD=$2 MNT KIND B S1 VG
  [ "$(id -u)" -eq 0 ] || { echo "root 로 실행하세요"; exit 1; }
  MNT=$(findmnt -no TARGET "$LV") || { echo "마운트 안 된 LV: $LV"; exit 1; }
  KIND=$(kind_of "$MNT"); B=$(pct_of "$MNT")
  S1=$(lvs --noheadings --units g -o lv_size "$LV" | tr -d ' ')
  VG=$(lvs --noheadings -o vg_name "$LV" | tr -d ' ')
  if lvextend -r -L +"$ADD" "$LV" >/tmp/sl_ext.$$ 2>&1; then
    log info event=action action=lvextend storage=$KIND mount=$MNT lv=$LV size_before=$S1 size_after=$(lvs --noheadings --units g -o lv_size "$LV" | tr -d ' ') before=${B}% after=$(pct_of "$MNT")% vg_free=$(vgs --noheadings --units g -o vg_free "$VG" | tr -d ' ') msg="LV 무중단 증설 조치"
  else
    log error event=action_failed action=lvextend lv=$LV err="$(tail -1 /tmp/sl_ext.$$)" msg="증설 실패"
  fi
  rm -f /tmp/sl_ext.$$
}

status() {
  pgrep -af "$PROG (fill|io) |dd if=/dev/zero of=.*demo_fill|--name=sl_" || echo "실행 중인 부하 없음"
  for D in "$@"; do echo "$D  $(kind_of "$D")  사용률 $(pct_of "$D")%  남은 $(avail_g "$D")G"; done
}

stop_all() {
  pkill -TERM -f "$PROG (fill|io) "; sleep 0.5
  pkill -f "dd if=/dev/zero of=.*demo_fill"; pkill -f -- "--name=sl_"
  sleep 1; echo "중지 요청 완료"; status
}

CMD=$1; shift
case "$CMD" in
  fill)   [ $# -lt 3 ] && usage; TARGET=$1; CHUNK_GB=$2; shift 2
          [ "$TARGET" -gt "$MAX_TARGET" ] && { echo "목표는 ${MAX_TARGET}% 이하"; exit 1; }
          for D in "$@"; do check_dir "$D"; done
          for D in "$@"; do fill_one "$D" & done; wait ;;
  io)     [ $# -lt 2 ] && usage; check_dir "$2"; io_run "$2" "$1" ;;
  stop)   stop_all ;;
  clean)  [ $# -eq 0 ] && usage; for D in "$@"; do check_dir "$D"; clean_dir "$D"; done ;;
  extend) [ $# -lt 2 ] && usage; extend_lv "$1" "$2" ;;
  status) status "$@" ;;
  *)      usage ;;
esac
