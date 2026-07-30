"""mock 메시지의 스키마 검증과 동기 SQS 전송.

파이프라인용 SqsSender(비동기 스레드·재시도·디스크 스필)와 달리, QA 도구는
성공/실패를 즉시 드러내야 하므로 동기 send_message를 직접 호출한다.
큐 URL/리전 해석은 파이프라인과 동일한 resolve_sqs_target을 재사용한다.
"""
import json
from pathlib import Path

from loovi_vision.realtime.sqs_sender import resolve_sqs_target

# 실행 위치와 무관하게 저장소 루트 기준으로 스키마를 찾는다.
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "vision-summary-schema.json"


def load_schema(path=SCHEMA_PATH):
    # 페이로드 계약(JSON Schema, draft-07)을 1회 로드한다.
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_summary(message, schema):
    # 전송 직전 스키마 검증. 실패 시 jsonschema.ValidationError 그대로 전파.
    import jsonschema

    jsonschema.validate(message, schema)


def check_queue_url(queue_url):
    # 큐 URL 형식 오류를 사람이 읽을 메시지로 반환한다 (정상이면 None).
    # sqs_smoke_test와 동일한 검사 규칙.
    if not queue_url:
        return "큐 URL 미설정: realtime.sqs_queue_url 또는 환경변수 SQS_QUEUE_URL 를 지정하세요."
    if any(ch.isspace() for ch in queue_url) or "|" in queue_url:
        return "큐 URL 형식 오류: SQS_QUEUE_URL 에 공백이나 '|' 문자가 섞여 있습니다."
    if not queue_url.startswith("https://sqs."):
        return "큐 URL 형식 오류: SQS_QUEUE_URL 은 https://sqs.<region>.amazonaws.com/... 형식이어야 합니다."
    return None


class MockSqsSender:
    """QA용 동기 전송기: 실패를 숨기지 않고 예외를 그대로 드러낸다."""

    def __init__(self, settings):
        self.queue_url, self.region = resolve_sqs_target(settings)
        self._client = None

    def connect(self):
        # boto3 클라이언트 생성. 자격증명은 boto3 기본 해석 체인(환경변수/.aws)에 위임.
        import boto3

        self._client = boto3.client("sqs", region_name=self.region or None)

    def send(self, message):
        # 1건 동기 전송 후 MessageId 반환. 리전/자격증명/큐 오설정은 예외로 즉시 드러난다.
        resp = self._client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(message, ensure_ascii=False),
        )
        return resp["MessageId"]

    def send_batch(self, messages):
        # 최대 10건 배치 전송 (weekly 등 대량 backfill용). 부분 실패도 즉시 예외로 드러낸다.
        entries = [
            {"Id": str(i), "MessageBody": json.dumps(m, ensure_ascii=False)}
            for i, m in enumerate(messages)
        ]
        resp = self._client.send_message_batch(QueueUrl=self.queue_url, Entries=entries)
        failed = resp.get("Failed") or []
        if failed:
            first = failed[0]
            raise RuntimeError(
                f"배치 {len(failed)}건 전송 실패 (예: {first.get('Code')}: {first.get('Message')})"
            )
        return len(resp.get("Successful", []))
