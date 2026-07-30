import argparse
import sys

# 실행 모드는 config 파일로 결정한다.
#   기본값 attention.yaml : person + face + gaze + realtime 전체 파이프라인
#   person_only.yaml      : 사람 검출/추적 + 로컬 기록만 하는 순수 baseline
# 예) 순수 person-only 실행:
#   python main.py --config loovi_vision/configs/person_only.yaml
# 예) 파이프라인 없이 mock summary를 SQS로 전송 (QA용):
#   python main.py --mock-sqs --count 12 --scenario daily
DEFAULT_CONFIG = "loovi_vision/configs/attention.yaml"


def main():
    parser = argparse.ArgumentParser(description="Loovi Vision 파이프라인 실행")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="사용할 YAML config 경로 (기본: 전체 파이프라인 attention.yaml)",
    )
    parser.add_argument(
        "--mock-sqs",
        action="store_true",
        help="파이프라인 대신 mock summary를 SQS로 전송 (QA용, 추가 옵션은 python -m mock_sqs --help)",
    )
    # mock 전용 옵션(--count, --scenario 등)은 mock_sqs.cli 가 파싱하도록 통과시킨다.
    args, extra = parser.parse_known_args()

    if args.mock_sqs:
        # 지연 import: mock 모드에서 무거운 파이프라인 의존성(cv2/모델)을 로드하지 않는다.
        from mock_sqs.cli import main as mock_main

        sys.exit(mock_main(["--config", args.config] + extra))

    if extra:
        parser.error(f"알 수 없는 인자: {extra}")

    from loovi_vision.pipelines.person_only import run

    run(args.config)


if __name__ == "__main__":
    main()
