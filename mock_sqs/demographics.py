"""명동역 인근 상권의 인구통계 프로파일.

명동은 성격이 다른 두 집단이 겹치는 상권이라 단일 분포로는 재현되지 않는다.
그래서 세그먼트를 나눠 정의하고 시간대별 혼합 비율로 합성한다.

  - 내국인(오피스·중구 직장인): 40~50대 편중. 서울시 상권분석 길단위인구에서
    명동은 40~50대 비중 53%로 집계되는 지역이다.
  - 외국인 관광객: 20~30대·여성 편중. 방한 외래관광객은 여성 약 60%이고,
    최대 국적인 중국인은 20~30대 47%·여성 67% 구성이다. 서울 방문 외국인의
    약 70%가 명동을 찾아 체류 규모가 월 548만 명대에 이른다.

혼합 비율(관광객 점유율)은 평일 업무시간에 낮고 저녁·주말에 높아지는데,
이는 오피스 상권과 관광 상권이 시간대로 갈리는 통념에 따른 가정이다.
세그먼트 가중치·혼합 비율은 이 파일의 상수만 고쳐 조정할 수 있다.
"""
# generator.AGE_KEYS와 같은 순서: under10, 10s, 20s, 30s, 40s, 50s, 60plus
DOMESTIC_AGE_WEIGHTS = [1, 4, 15, 20, 27, 26, 7]   # 40~50대 합 53% (실측 근거)
TOURIST_AGE_WEIGHTS = [1, 6, 30, 23, 20, 14, 6]    # 20~30대 합 53% (중국인 47%·기타 국적 혼합)

DOMESTIC_MALE_RATIO = 0.50  # 직장인 성비는 중립 가정
TOURIST_MALE_RATIO = 0.40   # 방한 외래관광객 여성 약 60%

# 시간대별 관광객 점유율 (시작시각, 끝시각, 비율). 평일은 업무시간에 직장인이,
# 저녁에는 관광·쇼핑 인파가 우세하다. 주말은 하루 종일 관광객 중심.
TOURIST_SHARE_WEEKDAY = [(6, 10, 0.25), (10, 14, 0.45), (14, 17, 0.55), (17, 24, 0.65)]
TOURIST_SHARE_WEEKEND = [(6, 11, 0.55), (11, 24, 0.75)]


def _share(bands, hour):
    # 시간대 band에서 관광객 점유율을 찾는다 (band를 벗어나면 마지막 값).
    for start, end, value in bands:
        if start <= hour < end:
            return value
    return bands[-1][2]


def mix_at(hour, weekday):
    """해당 시각의 (남성 비율, 연령 가중치)를 두 세그먼트 혼합으로 계산한다."""
    bands = TOURIST_SHARE_WEEKEND if weekday >= 5 else TOURIST_SHARE_WEEKDAY
    tourist = _share(bands, hour)
    domestic = 1.0 - tourist
    male_ratio = DOMESTIC_MALE_RATIO * domestic + TOURIST_MALE_RATIO * tourist
    age_weights = [
        d * domestic + t * tourist
        for d, t in zip(DOMESTIC_AGE_WEIGHTS, TOURIST_AGE_WEIGHTS)
    ]
    return male_ratio, age_weights
