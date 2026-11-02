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
