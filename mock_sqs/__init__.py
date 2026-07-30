"""SQS mock 데이터 전송 모듈 (QA용).

비전 파이프라인(카메라/모델) 없이 vision-summary 스키마에 맞는
mock summary를 생성해 SQS로 전송한다. 수신 측(서버/백엔드) QA에 사용.

사용법: python -m mock_sqs --help  또는  python main.py --mock-sqs
"""
