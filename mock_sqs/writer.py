"""생성한 mock 메시지를 파일로 저장하는 writer.

전송 없이 데이터 묶음만 전달해야 할 때 사용한다 (QA 담당자·수신 서버 개발자에게
파일로 넘기는 용도). 확장자로 형식을 자동 판별한다:

  out.jsonl     한 줄에 메시지 1건 (기본, 대용량 스트리밍에 적합)
  out.jsonl.gz  위와 같으나 gzip 압축 (일주일치 ~50MB → 수 MB)
  out.json      메시지 전체를 배열 하나로 (작은 묶음 확인용)
"""
import gzip
import json
from pathlib import Path


class MessageWriter:
    """메시지를 한 건씩 받아 파일로 흘려보낸다 (전체를 메모리에 쌓지 않는다)."""

    def __init__(self, path):
        self.path = Path(path)
        # .json 은 배열, 그 외(.jsonl/.jsonl.gz)는 줄 단위. .gz면 압축 저장.
        name = self.path.name.lower()
        self._gzip = name.endswith(".gz")
        self._as_array = name.removesuffix(".gz").endswith(".json")
        self._handle = None
        self.count = 0

    def __enter__(self):
        parent = self.path.parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        if self._gzip:
            self._handle = gzip.open(self.path, "wt", encoding="utf-8")
        else:
            self._handle = open(self.path, "w", encoding="utf-8")
        if self._as_array:
            self._handle.write("[\n")
        return self

    def write(self, message):
        # 배열 형식이면 항목 사이에 쉼표를 넣어준다.
        if self._as_array and self.count:
            self._handle.write(",\n")
        self._handle.write(json.dumps(message, ensure_ascii=False))
        if not self._as_array:
            self._handle.write("\n")
        self.count += 1

    def write_many(self, messages):
        for message in messages:
            self.write(message)

    def __exit__(self, exc_type, exc, tb):
        if self._as_array:
            self._handle.write("\n]\n")
        self._handle.close()
        self._handle = None
        return False

    def size_mb(self):
        # 저장 완료 후 파일 크기(MB). 인계 전 용량 안내용.
        return self.path.stat().st_size / (1024 * 1024)
