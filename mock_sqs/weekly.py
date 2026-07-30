"""weekly 시나리오: 명동역 인근 보드의 일주일치(06~24시 운영) 트래픽 프로파일.

값 근거 (data/jsonl 실측 attention 세션 27개, 총 175분 분석):
  - 5초당 통행자(OTS): 평균 1.07명, 혼잡 장면 최고 4.1명 → 피크 λ=4.0
  - LTS 전환율: 자연 관찰 세션 1.8~6.3% → uniform(0.02, 0.07)
  - dwell: 응시 에피소드 중앙값 ~2초, <2초 최빈, p90 ~5초 → 로그정규(median 2, σ 0.6)
실측이 전부 09~15시 촬영이라, 출퇴근·심야 배율은 통행량 상식에 따른 가정이다.
"""
from datetime import timedelta, timezone

from mock_sqs.demographics import mix_at

# 보드 운영 시간대(한국). KST는 DST가 없으므로 고정 오프셋으로 충분하다.
KST = timezone(timedelta(hours=9))
OPEN_HOUR, CLOSE_HOUR = 6, 24  # 매일 06:00~24:00 운영 (측정 시간)

# 식별자는 기존에 쓰던 config 값(board_gangnam_01 등)을 그대로 사용한다.
# 통행 패턴·인구통계는 명동 상권을 모델링하지만, 수신 측이 이미 등록해 둔
# 보드 식별자를 유지해야 QA가 매끄럽기 때문이다.
# 다른 값으로 보내야 하면 CLI의 --board-id / --device-id 를 쓴다.
BOARD_ID = None
DEVICE_ID = None

# 곡선 배율 1.0 = 피크 시간대의 5초 구간당 평균 통행자 수.
# 자체 실측 평균(09~15시 1.07명/5초)에 낮 시간대가 맞도록 역산한 값이며,
# 그 결과 평일 하루 통행량이 14,000명 내외가 된다. 요일 배율·우천·푸아송
# 노이즈가 겹치므로 실제 일별 값은 1만~1만7천 사이로 흩어진다.
BASE_OTS_MEAN = 1.7

# 시간대별 통행량 배율 anchor (시각 float, 배율). 사이 구간은 선형 보간.
# 평일: 출근(07:30~09:30)·점심(11:30~13:30)·퇴근+저녁(17:30~20:30) 3봉 구조.
WEEKDAY_CURVE = [
    (6.0, 0.15), (7.0, 0.45), (8.0, 0.85), (8.5, 0.9), (9.5, 0.55),
    (11.0, 0.5), (12.0, 0.9), (13.0, 0.85), (14.0, 0.5), (16.0, 0.55),
    (17.5, 0.85), (18.5, 1.0), (20.0, 0.9), (21.0, 0.65), (22.0, 0.45),
    (23.0, 0.3), (24.0, 0.15),
]
# 금요일: 평일 곡선에서 저녁 피크가 밤늦게까지 이어진다.
FRIDAY_CURVE = WEEKDAY_CURVE[:13] + [(21.0, 0.85), (22.0, 0.7), (23.0, 0.55), (24.0, 0.35)]
# 주말: 출근 피크 없이 오후~저녁 단봉 구조.
WEEKEND_CURVE = [
    (6.0, 0.05), (8.0, 0.15), (10.0, 0.4), (12.0, 0.7), (14.0, 0.9),
    (16.0, 1.0), (19.0, 1.0), (21.0, 0.8), (22.5, 0.55), (24.0, 0.25),
]

# 요일별 전체 배율 (월=0 … 일=6). 금·토는 유동 증가, 일요일은 감소.
DAY_FACTORS = {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.1, 5: 1.15, 6: 0.85}

# 현실감 이벤트 1: 비 오는 수요일. 연구상 우천 시 보행량 감소는 10~30% 수준이라
# 통행은 0.7×로 완만하게 줄이고, 대신 우산이 얼굴을 가려 검출이 어려워지는 효과를
# LTS 0.6× + 성별/연령 미상 비율 상향으로 재현한다 (통행↓보다 검출 저하가 본질).
RAIN_WEEKDAY = 2
RAIN_TRAFFIC_FACTOR = 0.7
RAIN_LTS_FACTOR = 0.6
RAIN_UNKNOWN_GENDER, RAIN_UNKNOWN_AGE = 0.30, 0.25
# 현실감 이벤트 2: 토요일 19:00~19:30 근처 행사 스파이크(2×).
SPIKE_WEEKDAY, SPIKE_START, SPIKE_END, SPIKE_FACTOR = 5, 19.0, 19.5, 2.0

# 시간대별 LTS 전환율 (시작시각, 끝시각, (하한, 상한)). 업계 통념 기반 가정:
# 출근길은 바쁘게 걸어 주목률이 낮고, 저녁·여가 시간대는 높다. 전체 범위는
# 실측(1.8~6.3%) 안에 머문다. 주말은 출근 피크가 없어 하루 종일 여가 수준.
LTS_BANDS_WEEKDAY = [(6, 10, (0.02, 0.04)), (10, 17, (0.03, 0.06)), (17, 24, (0.04, 0.07))]
LTS_BANDS_WEEKEND = [(6, 12, (0.03, 0.05)), (12, 24, (0.04, 0.07))]

# dwell 로그정규 파라미터: 중앙값 2초(mu=ln2), 산포 σ=0.6 → p90 ≈ 4.3초.
DWELL_LOG_MEDIAN = 0.6931471805599453
DWELL_LOG_SIGMA = 0.6
DWELL_MIN = 1.0  # LTS 판정 최소 응시 시간(1초) 미만은 존재하지 않는다.


def _interp(curve, hour):
    # anchor 목록에서 hour 위치의 배율을 선형 보간으로 구한다.
    if hour <= curve[0][0]:
        return curve[0][1]
    for (h0, v0), (h1, v1) in zip(curve, curve[1:]):
        if hour <= h1:
            return v0 + (v1 - v0) * (hour - h0) / (h1 - h0)
    return curve[-1][1]


class WeeklyProfile:
    """시각(clock) 기반 통행량 프로파일. ScenarioProfile과 같은 인터페이스를 제공한다."""

    name = "weekly"
    device_id = DEVICE_ID      # 프로파일이 자기 식별자를 갖는다 (config보다 우선)
    board_id = BOARD_ID
    ots_mean = BASE_OTS_MEAN   # 배율 1.0일 때의 푸아송 λ
    zero_prob = 0.0            # 심야 0명은 λ가 작아지며 자연 발생하므로 강제하지 않음
    lts_ratio = (0.02, 0.07)   # 전체 범위 표시용 (실제 샘플링은 sample_gaze_prob가 담당)
    unknown_gender = 0.15      # 평시 미상 비율 (실측 근거가 없어 기존 시나리오 값 유지)
    unknown_age = 0.10
    intensity_curve = "clock"  # 표시용 (index 기반 wave와 구분)

    @staticmethod
    def _local(clock):
        # (KST 시각 float, 요일) 튜플. 모든 시각 기반 판단의 공통 입력.
        local = clock.astimezone(KST)
        return local.hour + local.minute / 60 + local.second / 3600, local.weekday()

    def intensity(self, index, wave_period, clock=None):
        # "지금 몇 시·무슨 요일인가"로 배율을 정한다 (index/wave_period는 쓰지 않음).
        hour, weekday = self._local(clock)
        if weekday >= 5:
            curve = WEEKEND_CURVE
        elif weekday == 4:
            curve = FRIDAY_CURVE
        else:
            curve = WEEKDAY_CURVE
        factor = _interp(curve, hour) * DAY_FACTORS[weekday]
        if weekday == RAIN_WEEKDAY:
            factor *= RAIN_TRAFFIC_FACTOR
        if weekday == SPIKE_WEEKDAY and SPIKE_START <= hour < SPIKE_END:
            factor *= SPIKE_FACTOR
        return factor

    def sample_gaze_prob(self, rng, clock=None):
        # 시간대 band에서 LTS 전환율을 뽑고, 우천일은 우산 가림으로 하향 보정.
        hour, weekday = self._local(clock)
        bands = LTS_BANDS_WEEKEND if weekday >= 5 else LTS_BANDS_WEEKDAY
        low, high = bands[-1][2]
        for start, end, ratio_range in bands:
            if start <= hour < end:
                low, high = ratio_range
                break
        prob = rng.uniform(low, high)
        return prob * RAIN_LTS_FACTOR if weekday == RAIN_WEEKDAY else prob

    def demographic_mix(self, clock=None):
        # 명동 상권 구성: 내국인 직장인 + 외국인 관광객을 시간대별로 혼합한다.
        hour, weekday = self._local(clock)
        return mix_at(hour, weekday)

    def unknown_rates(self, clock=None):
        # 우천일은 우산·후드로 얼굴 속성 추정 실패가 늘어 미상 비율이 커진다.
        _, weekday = self._local(clock)
        if weekday == RAIN_WEEKDAY:
            return RAIN_UNKNOWN_GENDER, RAIN_UNKNOWN_AGE
        return self.unknown_gender, self.unknown_age

    def sample_dwell(self, rng):
        # 실측 분포 재현: 짧은 응시가 최빈이고 긴 응시는 소수인 로그정규.
        return max(DWELL_MIN, rng.lognormvariate(DWELL_LOG_MEDIAN, DWELL_LOG_SIGMA))
