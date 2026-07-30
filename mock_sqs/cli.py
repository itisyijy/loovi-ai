"""mock summary를 SQS로 전송하는 CLI (QA용).

사용 예:
  python -m mock_sqs --dry-run --count 3 --seed 42        # 전송 없이 데이터 확인
  python -m mock_sqs --scenario daily --count 0           # 무한 스트리밍 (Ctrl+C 종료)
  python -m mock_sqs --scenario stress --interval 0 --count 500   # 벌크 적재
  python main.py --mock-sqs --count 12                    # main.py 플래그로 트리거

실시간 전송은 실제 경과 시간을 timestamp/interval_sec에 기록해 지연 누적을 막는다.
--interval 0 벌크 적재만 config의 summary_interval_sec(기본 5초) 논리 시간을 쓴다.
"""
import argparse
import json
import sys
import time

from loovi_vision.config import Settings, load_config

from mock_sqs.generator import MockSummaryGenerator
from mock_sqs.scenarios import DEFAULT_SCENARIO, SCENARIOS
from mock_sqs.sender import MockSqsSender, check_queue_url, load_schema, validate_summary
from mock_sqs.writer import MessageWriter

DEFAULT_CONFIG = "loovi_vision/configs/attention.yaml"


def build_parser():
    parser = argparse.ArgumentParser(prog="mock_sqs", description="SQS mock summary 전송기 (QA용)")
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="YAML config 경로 (device/board/큐 설정 공급, 기본: attention.yaml)")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS) + ["weekly"],
                        default=DEFAULT_SCENARIO,
                        help="mock 데이터 시나리오 (기본: normal, weekly=일주일치 backfill)")
    parser.add_argument("--count", type=int, default=12,
                        help="전송 건수 (0=무한 스트리밍, 기본 12건=1분 분량)")
    parser.add_argument("--interval", type=float, default=None,
                        help="전송 간격 초 (기본: summary 주기와 동일, 0=대기 없이 연속 전송)")
    parser.add_argument("--seed", type=int, default=None,
                        help="랜덤 seed (같은 seed면 같은 데이터 재현)")
    parser.add_argument("--start-seq", type=int, default=1, help="시작 seq 번호 (기본 1)")
    parser.add_argument("--wave-period", type=int, default=60,
                        help="daily 시나리오의 변동 주기 (메시지 건수, 기본 60건=5분 분량)")
    parser.add_argument("--days", type=int, default=7,
                        help="weekly 시나리오의 생성 일수 (기본 7일)")
    parser.add_argument("--start-date", default=None,
                        help="weekly 시작 날짜 YYYY-MM-DD, KST (기본: days일 전 → 어제까지 backfill)")
    parser.add_argument("--board-id", default=None,
                        help="mock 메시지의 board_id 덮어쓰기 (config 파일은 변경하지 않음)")
    parser.add_argument("--device-id", default=None,
                        help="mock 메시지의 device_id 덮어쓰기 (config 파일은 변경하지 않음)")
    parser.add_argument("--out", default=None,
                        help="SQS 전송 대신 파일로 저장 (.jsonl / .jsonl.gz / .json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="생성+스키마 검증+출력만 하고 SQS 전송은 생략")
    parser.add_argument("--no-final-partial", action="store_true",
                        help="종료 시 짧은 마지막 window 전송을 생략")
    return parser


def _emit(message, schema, sender, dry_run, writer=None):
    # 1건 처리: 검증 → 파일 저장 / dry-run 출력 / SQS 전송 중 하나.
    validate_summary(message, schema)
    if writer is not None:
        writer.write(message)
    elif dry_run:
        print(json.dumps(message, ensure_ascii=False, indent=2))
    else:
        message_id = sender.send(message)
        print(f"[OK] seq={message['seq']} ots={message['ots_count']} "
              f"lts={message['lts_count']} MessageId={message_id}")


def _run(settings, schema, sender, args, writer):
    """시나리오에 따라 메시지를 생성해 전송/저장한다. 반환값은 exit code."""
    if args.scenario == "weekly":
        # weekly는 과거 일주일치 backfill 전용 흐름 (count/interval 미사용, 배치 전송).
        from mock_sqs.weekly_run import run_weekly

        mode = "파일 저장" if writer is not None else "배치 전송"
        print(f"  시나리오  : weekly / {args.days}일치 backfill ({mode})")
        try:
            return run_weekly(settings, schema, sender, args, writer)
        except KeyboardInterrupt:
            print("\n[INFO] 사용자 중단(Ctrl+C) → 종료합니다.")
            return 1
        except Exception as exc:
            print(f"[FAIL] {type(exc).__name__}: {exc}")
            return 1

    generator = MockSummaryGenerator(
        settings, SCENARIOS[args.scenario],
        seed=args.seed, start_seq=args.start_seq, wave_period=args.wave_period,
        device_id=args.device_id, board_id=args.board_id,
        # 대기 전송은 실제 시각에 맞추고, interval=0 벌크 적재만 논리 시간을 사용한다.
        live_clock=args.interval != 0,
    )
    sleep_sec = generator.window_sec if args.interval is None else max(args.interval, 0.0)
    print(f"  시나리오  : {args.scenario} / 건수 {args.count or '무한'} / 간격 {sleep_sec}s")

    sent = 0
    try:
        # 본 전송 루프: count=0이면 Ctrl+C까지 무한 스트리밍.
        while args.count == 0 or sent < args.count:
            _emit(generator.next(), schema, sender, args.dry_run, writer)
            sent += 1
            is_last = args.count != 0 and sent >= args.count
            if not is_last and sleep_sec > 0:
                time.sleep(sleep_sec)
    except KeyboardInterrupt:
        print("\n[INFO] 사용자 중단(Ctrl+C) → 종료합니다.")
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1

    # 종료 시(정상 완료·Ctrl+C 모두) 짧은 partial window 1건 전송 (실제 finalize 재현).
    if not args.no_final_partial:
        try:
            _emit(generator.next_partial(), schema, sender, args.dry_run, writer)
            sent += 1
        except Exception as exc:
            print(f"[FAIL] {type(exc).__name__}: {exc}")
            return 1

    if writer is not None:
        target = str(writer.path)
    elif args.dry_run:
        target = "(dry-run: 전송 생략)"
    else:
        target = sender.queue_url
    last_seq = args.start_seq + sent - 1
    print(f"[DONE] 시나리오={args.scenario} {sent}건 "
          f"seq={args.start_seq}..{last_seq} 대상={target}")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.count < 0:
        parser.error("--count 는 0 이상이어야 합니다 (0=무한)")
    if args.days < 1:
        parser.error("--days 는 1 이상이어야 합니다")
    if args.out and args.count == 0 and args.scenario != "weekly":
        parser.error("--out 은 무한 스트리밍(--count 0)과 함께 쓸 수 없습니다")

    settings = Settings(load_config(args.config))
    schema = load_schema()
    # --out 은 "파일로만 저장" 모드다. 대량 데이터가 실수로 큐에 들어가지 않도록
    # 파일 저장과 SQS 전송을 동시에 하지 않는다.
    file_only = args.out is not None

    sender = None
    if not args.dry_run and not file_only:
        sender = MockSqsSender(settings)
        error = check_queue_url(sender.queue_url)
        if error:
            print(f"[FAIL] {error}")
            return 1
        try:
            sender.connect()
        except Exception as exc:
            print(f"[FAIL] SQS client 생성 실패: {exc}")
            return 1
        print(f"  대상 큐   : {sender.queue_url}")
        print(f"  리전      : {sender.region or '(boto3 기본 해석)'}")

    if not file_only:
        return _run(settings, schema, sender, args, None)

    print(f"  출력 파일 : {args.out}")
    try:
        # with 블록을 벗어나며 파일이 닫힌 뒤에 크기를 재야 실제 용량이 나온다.
        with MessageWriter(args.out) as writer:
            code = _run(settings, schema, sender, args, writer)
    except OSError as exc:
        print(f"[FAIL] 파일 저장 실패: {exc}")
        return 1
    if code == 0:
        print(f"[FILE] {writer.path} ({writer.count}건, {writer.size_mb():.1f} MB)")
    return code


if __name__ == "__main__":
    sys.exit(main())
