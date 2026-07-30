"""시나리오 프로파일 정의.

mock summary의 값 범위(인원수·응시율·dwell 등)를 시나리오 단위로 묶어 둔다.
QA가 다른 상황을 원하면 여기 숫자만 조정하면 된다 (생성 로직 수정 불필요).
값 범위는 실측 통계가 아닌 상식적 추정치이며, 조정을 전제로 상수화했다.
"""
import math
from dataclasses import dataclass

# wave 곡선의 하한 배율: 곡선 저점에서도 통행량이 완전히 0이 되지 않게 한다.
WAVE_FLOOR = 0.1


@dataclass(frozen=True)
class ScenarioProfile:
    # 식별자를 따로 갖지 않는 시나리오는 config의 device_id/board_id를 그대로 쓴다
    # (weekly만 명동 식별자를 프로파일 속성으로 덮어쓴다).
    device_id = None
    board_id = None

    name: str              # 시나리오 이름 (CLI --scenario 값)
    ots_mean: float        # 5초 구간당 평균 통행자 수 (푸아송 λ)
    zero_prob: float       # 강제 0명 구간 확률 (한산한 구간 재현)
    lts_ratio: tuple       # 통행자 중 응시(LTS) 전환율 범위 (min, max)
    dwell_range: tuple     # 응시자별 dwell 초 범위 (min, max)
    unknown_gender: float  # 성별 미상 비율 → demographics 합 < count 재현
    unknown_age: float     # 연령 미상 비율 → age 버킷 합 < count 재현
    intensity_curve: str   # "flat"(고정) | "wave"(시간대 변동 사인 곡선)

    def intensity(self, index, wave_period, clock=None):
        # 메시지 순번(index)에 따른 통행량 배율.
        # flat: 항상 1.0 / wave: 한산→증가→피크→감소를 사인 반주기로 반복
        # (대시보드 시계열 그래프 QA용 시간대 변동 시뮬레이션).
        # clock은 weekly 등 시각 기반 프로파일용 인터페이스이며 여기서는 쓰지 않는다.
        if self.intensity_curve != "wave" or wave_period <= 0:
            return 1.0
        phase = (index % wave_period) / wave_period
        return WAVE_FLOOR + (1.0 - WAVE_FLOOR) * math.sin(math.pi * phase)

    def sample_dwell(self, rng):
        # 응시자 1명의 dwell 초 샘플링. 기본은 균등분포 (weekly는 로그정규로 재정의).
        return rng.uniform(*self.dwell_range)

    def sample_gaze_prob(self, rng, clock=None):
        # 이번 구간의 LTS 전환율. 기본은 고정 범위 (weekly는 시간대·우천으로 재정의).
        return rng.uniform(*self.lts_ratio)

    def unknown_rates(self, clock=None):
        # (성별 미상, 연령 미상) 비율. 기본은 고정값 (weekly는 우천 시 상향 재정의).
        return self.unknown_gender, self.unknown_age

    def demographic_mix(self, clock=None):
        # (남성 비율, 연령 가중치). None이면 generator의 기본 분포를 쓴다
        # (weekly는 명동 상권 세그먼트 혼합으로 재정의).
        return None, None


# 시나리오 레지스트리. ots_mean 기준: quiet=한산, normal=일반, peak=붐빔,
# stress=대량 적재/성능 QA, daily=quiet↔peak를 오가는 시간대 변동.
SCENARIOS = {
    "quiet": ScenarioProfile("quiet", 1.0, 0.35, (0.2, 0.4), (1.0, 4.0), 0.15, 0.10, "flat"),
    "normal": ScenarioProfile("normal", 4.0, 0.10, (0.3, 0.5), (1.0, 6.0), 0.15, 0.10, "flat"),
    "peak": ScenarioProfile("peak", 12.0, 0.0, (0.3, 0.5), (1.0, 8.0), 0.15, 0.10, "flat"),
    "stress": ScenarioProfile("stress", 60.0, 0.0, (0.3, 0.5), (1.0, 8.0), 0.15, 0.10, "flat"),
    "daily": ScenarioProfile("daily", 12.0, 0.0, (0.3, 0.5), (1.0, 8.0), 0.15, 0.10, "wave"),
}

DEFAULT_SCENARIO = "normal"
