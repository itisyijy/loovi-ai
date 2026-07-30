"""weekly 시나리오 실행 루프 (과거 일주일치 backfill 생성·전송).

하루를 실제 파이프라인의 1개 세션처럼 다룬다:
  - 매일 06:00(KST) 시작, seq는 1부터 다시 시작 (런타임 재기동 재현)
  - 24:00 종료 시 마지막 1건은 짧은 partial window (finalize 재현)
  - 00~06시는 미운영 → 메시지 없음 (timestamp가 다음날 06시로 점프)
대량 건수(기본 7일 ≈ 9만 건)이므로 SQS 배치 전송(10건 묶음)을 사용한다.
"""
import json
from datetime import datetime, timedelta

from mock_sqs.generator import MockSummaryGenerator
from mock_sqs.weekly import KST, OPEN_HOUR, CLOSE_HOUR, WeeklyProfile

BATCH_SIZE = 10  # SQS send_message_batch 최대 묶음 크기


def parse_start_date(text, days):
    # --start-date(YYYY-MM-DD, KST 기준) 해석. 미지정 시 "오늘로부터 days일 전"
    # → 어제 24:00에 끝나는 최근 일주일을 backfill 한다.
    if text:
        day = datetime.strptime(text, "%Y-%m-%d")
    else:
        day = datetime.now(KST) - timedelta(days=days)
    return day.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0, tzinfo=KST)


class _DayStats:
    """하루치 생성 결과 요약 (진행 로그용)."""

    def __init__(self):
        self.msgs = self.ots = self.lts = self.peak_ots = 0

    def add(self, message):
        self.msgs += 1
        self.ots += message["ots_count"]
        self.lts += message["lts_count"]
        self.peak_ots = max(self.peak_ots, message["ots_count"])


def _flush(sender, writer, buffer):
    # 버퍼를 배치 전송하거나 파일로 흘려보내고 비운다 (dry-run이면 둘 다 None).
    if buffer:
        if sender is not None:
            sender.send_batch(buffer)
        if writer is not None:
            writer.write_many(buffer)
    buffer.clear()


def run_weekly(settings, schema, sender, args, writer=None):
    """days일치 메시지를 날짜순으로 생성→검증→전송/저장한다. 반환값은 exit code."""
    import jsonschema

    validator = jsonschema.Draft7Validator(schema)  # 대량 검증이라 1회 컴파일해 재사용
    profile = WeeklyProfile()
    start = parse_start_date(args.start_date, args.days)
    # 실제 메시지에 들어가는 값을 보여준다 (생성기와 같은 우선순위로 해석).
    board = args.board_id or profile.board_id or settings.board_id
    device = args.device_id or profile.device_id or settings.rt_device_id
    print(f"  식별자    : board={board} device={device}")
    print(f"  기간      : {start:%Y-%m-%d}(KST) 부터 {args.days}일, "
          f"매일 {OPEN_HOUR:02d}:00~{CLOSE_HOUR % 24:02d}:00 운영")

    total = _DayStats()
    sample_shown = False
    buffer = []
    for day_idx in range(args.days):
        day_start = start + timedelta(days=day_idx)
        # 하루 = 세션 1개: seq 1부터, seed는 날짜별로 달리해 재현성 유지.
        generator = MockSummaryGenerator(
            settings, profile,
            seed=None if args.seed is None else args.seed + day_idx,
            start_seq=1, start_time=day_start,
            device_id=args.device_id, board_id=args.board_id,
        )
        day_sec = (CLOSE_HOUR - OPEN_HOUR) * 3600
        full_windows = max(int(day_sec // generator.window_sec) - 1, 0)

        stats = _DayStats()
        for i in range(full_windows + 1):
            # 마지막 1건은 partial window (자정 finalize 재현).
            message = generator.next() if i < full_windows else generator.next_partial()
            errors = list(validator.iter_errors(message))
            if errors:
                print(f"[FAIL] 스키마 위반 (day={day_start:%Y-%m-%d} seq={message['seq']}): "
                      f"{errors[0].message}")
                return 1
            if args.dry_run and writer is None and not sample_shown:
                # 형태 확인용으로 첫 메시지 1건만 전체 JSON 출력.
                # 파일 저장 모드에서는 파일 자체가 결과물이므로 출력하지 않는다.
                print(json.dumps(message, ensure_ascii=False, indent=2))
                sample_shown = True
            stats.add(message)
            buffer.append(message)
            if len(buffer) >= BATCH_SIZE:
                _flush(sender, writer, buffer)
        _flush(sender, writer, buffer)

        weekday = "월화수목금토일"[day_start.weekday()]
        if writer is not None:
            done = " 저장 완료"
        elif args.dry_run:
            done = " (dry-run)"
        else:
            done = " 전송 완료"
        print(f"  [{day_start:%Y-%m-%d}({weekday})] {stats.msgs}건 "
              f"ots={stats.ots} lts={stats.lts} peak={stats.peak_ots}{done}")
        total.msgs += stats.msgs
        total.ots += stats.ots
        total.lts += stats.lts

    if writer is not None:
        target = str(writer.path)
    elif args.dry_run:
        target = "(dry-run: 전송 생략)"
    else:
        target = sender.queue_url
    lts_pct = total.lts / total.ots * 100 if total.ots else 0.0
    print(f"[DONE] weekly {args.days}일 = {total.msgs}건 "
          f"ots={total.ots} lts={total.lts}({lts_pct:.1f}%) 대상={target}")
    return 0
