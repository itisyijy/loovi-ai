"""mock summary 생성기.

필드를 따로 랜덤 생성하지 않고 "가상의 통행자 개인"을 만들어 집계한다
(실제 파이프라인 SummaryAggregator와 같은 구조). 그래서 아래 불변식이
샘플링 구조상 자동으로 보장된다:
  - lts_count ≤ ots_count (응시자는 통행자의 부분집합)
  - male.count + female.count ≤ count (성별 미상은 집계 제외)
  - 성별별 age 버킷 합 ≤ count (연령 미상은 성별만 집계)
  - dwell 분포·합·평균이 같은 dwell 샘플에서 도출 (내적 일관성)
"""
import math
import random
from datetime import datetime, timedelta, timezone

from loovi_vision.enrich.session_summary import empty_age_dist

# 기본 연령 분포 가중치: 옥외 광고 유동인구는 20~40대가 많다고 가정 (상식적 추정치).
# weekly 시나리오는 프로파일이 상권별 가중치를 따로 공급한다 (demographics 참고).
AGE_KEYS = ["under10", "10s", "20s", "30s", "40s", "50s", "60plus"]
AGE_WEIGHTS = [3, 8, 25, 25, 20, 12, 7]
DEFAULT_MALE_RATIO = 0.5

# 응시자 중 이 비율은 "아직 응시 중"으로 간주해 dwell 분포에서 제외한다
# (실제 파이프라인은 해당 구간에 응시가 끝난 사람만 분포에 계상 → 버킷 합 ≤ lts).
ONGOING_GAZE_RATIO = 0.3


def _poisson(rng, lam):
    # Knuth 방식 푸아송 샘플링 (λ가 수백 이하인 mock 용도로 충분).
    threshold = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def _aggregate_demographics(persons):
    # (성별, 연령키) 개인 목록을 서버 스키마(v2) demographics로 집계한다.
    # 실제 파이프라인의 demographics_of()와 동일한 미상 처리 규칙.
    demo = {g: {"count": 0, "age": empty_age_dist()} for g in ("male", "female")}
    for gender, age_key in persons:
        if gender is None:
            continue  # 성별 미상 → 어느 쪽에도 집계하지 않음
        demo[gender]["count"] += 1
        if age_key is not None:
            demo[gender]["age"][age_key] += 1
    return demo


class MockSummaryGenerator:
    """시나리오 프로파일에 따라 summary를 seq/timestamp 연속으로 생성한다."""

    def __init__(self, settings, profile, seed=None, start_seq=1, wave_period=60,
                 start_time=None, device_id=None, board_id=None, live_clock=False):
        self._rng = random.Random(seed)  # 전역 random 오염 방지 + seed 재현성
        self._profile = profile
        # 식별자 우선순위: CLI 인자 > 시나리오 프로파일(weekly=명동) > config 값.
        # mock 전용 값이므로 실제 파이프라인 config는 건드리지 않는다.
        self._device_id = device_id or profile.device_id or settings.rt_device_id
        self._board_id = board_id or profile.board_id or settings.board_id
        self.window_sec = float(settings.rt_summary_interval)  # 메시지가 나타내는 구간 길이
        self._seq = start_seq
        self._index = 0  # wave 곡선 계산용 메시지 순번
        self._wave_period = wave_period
        # start_time을 주면 과거 시각부터 backfill 생성 (weekly 시나리오용).
        self._clock = start_time if start_time is not None else datetime.now(timezone.utc)
        # 실시간 모드는 실제 전송 시각을 사용한다. 과거 backfill은 논리 시계를 유지한다.
        self._live_clock = live_clock and start_time is None

    def next(self, interval_override=None):
        # summary 1건 생성. interval_override는 짧은 마지막 window(partial)용.
        rng, profile = self._rng, self._profile
        interval = self._next_interval(interval_override)
        scale = interval / self.window_sec  # partial window는 인원수도 비례 축소

        # 1) 통행자 수: 시간대 변동 배율 × 푸아송, zero_prob 확률로 강제 0명
        if rng.random() < profile.zero_prob:
            ots_count = 0
        else:
            # clock 전달: weekly처럼 "몇 시인가"로 배율을 정하는 프로파일 지원.
            factor = profile.intensity(self._index, self._wave_period, self._clock)
            lam = max(profile.ots_mean * factor * scale, 0.05)
            ots_count = _poisson(rng, lam)

        # 2) 통행자 개개인에게 성별·연령 부여 (미상 포함).
        #    미상 비율은 구간 단위로 프로파일에 위임 (우천 시 우산 가림 재현 등).
        #    성비·연령 가중치도 프로파일에 위임 (상권·시간대별 구성 재현).
        unknown_gender, unknown_age = profile.unknown_rates(self._clock)
        male_ratio, age_weights = profile.demographic_mix(self._clock)
        passersby = [
            self._make_person(unknown_gender, unknown_age, male_ratio, age_weights)
            for _ in range(ots_count)
        ]

        # 3) 응시자 선정: 개인별 추첨이므로 lts ≤ ots, lts 인구통계 ⊂ ots 인구통계.
        #    전환율도 프로파일에 위임 (시간대별 변주 지원).
        gaze_prob = profile.sample_gaze_prob(rng, self._clock)
        gazers = [p for p in passersby if rng.random() < gaze_prob]

        message = {
            "device_id": self._device_id,
            "board_id": self._board_id,
            "seq": self._seq,
            # clock이 KST 등 다른 timezone이어도 항상 UTC로 변환해 전송 (실제 파이프라인 규약).
            "timestamp": self._clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval_sec": round(interval, 3),
            "ots_count": len(passersby),
            "lts_count": len(gazers),
            "ots_demographics": _aggregate_demographics(passersby),
            "lts_demographics": _aggregate_demographics(gazers),
            "attention": self._make_attention(len(gazers)),
        }
        self._seq += 1
        self._index += 1
        if not self._live_clock:
            self._clock += timedelta(seconds=interval)
        return message

    def next_partial(self):
        # 종료 직전의 짧은 마지막 window (실제 GazeRuntime.finalize 동작 재현).
        if self._live_clock:
            # 실제 종료 시에는 직전 전송 이후 경과한 시간만큼만 만든다.
            return self.next()
        partial = round(self._rng.uniform(0.5, self.window_sec * 0.9), 3)
        return self.next(interval_override=partial)

    def _next_interval(self, interval_override):
        # 실시간은 실제 경과 시간으로 timestamp/interval_sec를 맞춰 드리프트를 없앤다.
        if self._live_clock:
            now = datetime.now(timezone.utc)
            elapsed = (now - self._clock).total_seconds()
            self._clock = now
            # 첫 메시지는 시작 전 1개 window를 나타낸다고 간주한다.
            return self.window_sec if self._index == 0 else max(elapsed, 0.001)
        return self.window_sec if interval_override is None else interval_override

    def _make_person(self, unknown_gender, unknown_age, male_ratio=None, age_weights=None):
        # 가상 통행자 1명: (성별 or None, 연령키 or None). None = 미상.
        rng = self._rng
        male_ratio = DEFAULT_MALE_RATIO if male_ratio is None else male_ratio
        weights = AGE_WEIGHTS if age_weights is None else age_weights
        if rng.random() < unknown_gender:
            gender = None
        else:
            gender = "male" if rng.random() < male_ratio else "female"
        age_key = None if rng.random() < unknown_age else rng.choices(AGE_KEYS, weights=weights)[0]
        return gender, age_key

    def _make_attention(self, lts_count):
        # 응시자별 dwell 초를 샘플링하고, 같은 샘플에서 분포/합/평균을 도출한다.
        # dwell은 이전 구간에서 시작된 응시일 수 있으므로 window 길이를 넘어도 자연스럽다.
        rng, profile = self._rng, self._profile
        dwells = [profile.sample_dwell(rng) for _ in range(lts_count)]
        dist = {"1_to_under_2s": 0, "2_to_under_3s": 0, "3_to_under_4s": 0, "4s_and_over": 0}
        for dwell in dwells:
            if rng.random() < ONGOING_GAZE_RATIO:
                continue  # 아직 응시 중 → 이 구간 분포에는 계상하지 않음
            if dwell < 2:
                dist["1_to_under_2s"] += 1
            elif dwell < 3:
                dist["2_to_under_3s"] += 1
            elif dwell < 4:
                dist["3_to_under_4s"] += 1
            else:
                dist["4s_and_over"] += 1
        total = float(sum(dwells))
        return {
            "avg_dwell_sec": round(total / lts_count, 3) if lts_count else 0.0,
            "dwell_sum_sec": round(total, 3),
            "dwell_distribution": dist,
        }
