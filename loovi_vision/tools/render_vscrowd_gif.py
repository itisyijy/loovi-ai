"""VSCrowd 프레임별 GT 머리 박스를 GIF로 시각화하는 CLI.

사용 예:
  python -m loovi_vision.tools.render_vscrowd_gif --clip test_001 --frames 30
"""
import argparse
from pathlib import Path

import cv2
from PIL import Image

from loovi_vision.config import Settings, load_config
from loovi_vision.detectors.person import PersonDetector
from loovi_vision.eval.vscrowd_loader import frame_image_path, load_clip
from loovi_vision.tracking.factory import create_tracker


def parse_args():
    """명령행 인자를 읽는다."""
    parser = argparse.ArgumentParser(description="VSCrowd GT 박스 GIF 생성")
    parser.add_argument("--data-root", default="data/VSCrowd", help="VSCrowd 루트 경로")
    parser.add_argument("--clip", default="test_001", help="시각화할 클립 이름")
    parser.add_argument("--source", choices=["gt", "model"], default="gt",
                        help="gt=정답 박스, model=우리 검출기·트래커 결과")
    parser.add_argument("--config", default="loovi_vision/configs/person_only.yaml",
                        help="--source model에서 사용할 파이프라인 설정")
    parser.add_argument("--frames", type=int, default=30, help="앞에서부터 사용할 프레임 수(0=전체)")
    parser.add_argument("--width", type=int, default=960, help="GIF 출력 폭(0=원본 크기)")
    parser.add_argument("--fps", type=float, default=10, help="GIF 재생 FPS")
    parser.add_argument("--out", default="", help="출력 GIF 경로")
    return parser.parse_args()


def output_path(args):
    """출력 경로를 정한다."""
    if args.out:
        return Path(args.out)
    return Path("data/videos") / f"vscrowd_{args.clip}_{args.source}.gif"


def resize(frame, width):
    """가로 폭 기준으로 비율을 보존해 축소한다."""
    if not width or frame.shape[1] <= width:
        return frame
    ratio = width / frame.shape[1]
    height = round(frame.shape[0] * ratio)
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def draw_ground_truth(frame, heads, scale):
    """GT 머리 박스와 데이터셋의 고유 Head ID를 프레임에 표시한다."""
    for head_id, (x, y, w, h) in heads:
        left, top = round(x * scale), round(y * scale)
        right, bottom = round((x + w) * scale), round((y + h) * scale)
        cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 80), 2)
        cv2.putText(
            frame, f"GT {head_id}", (left, max(16, top - 5)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 80), 1, cv2.LINE_AA,
        )


def draw_model_results(frame, detections, det_to_track, scale):
    """우리 모델의 사람 검출 박스·신뢰도·추적 ID를 프레임에 표시한다."""
    for index, detection in enumerate(detections):
        x, y, w, h = detection["bbox"]
        left, top = round(x * scale), round(y * scale)
        right, bottom = round((x + w) * scale), round((y + h) * scale)
        track_id = det_to_track.get(index)
        color = (0, 180, 255) if track_id is None else (255, 190, 0)
        label = f"person {detection['confidence']:.2f}" if track_id is None else f"ID {track_id} {detection['confidence']:.2f}"
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, label, (left, max(16, top - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def read_frame(data_root, clip, frame_id):
    """VSCrowd의 원본 이미지 프레임을 읽는다."""
    source = frame_image_path(data_root, clip, frame_id)
    raw = cv2.imread(str(source))
    if raw is None:
        raise FileNotFoundError(f"프레임을 읽지 못했습니다: {source}")
    return raw


def as_gif_frame(frame):
    """OpenCV BGR 프레임을 Pillow GIF 프레임으로 변환한다."""
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def render_gt_frame(data_root, clip, annotation, width):
    """원본 프레임 하나에 GT를 그려 Pillow GIF 프레임으로 변환한다."""
    raw = read_frame(data_root, clip, annotation["frame"])
    scale = min(1.0, width / raw.shape[1]) if width else 1.0
    canvas = resize(raw, width)
    draw_ground_truth(canvas, annotation["heads"], scale)
    cv2.putText(
        canvas, f"{clip} | frame {annotation['frame']:06d} | heads {len(annotation['heads'])}",
        (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (30, 220, 255), 2, cv2.LINE_AA,
    )
    return as_gif_frame(canvas)


def render_model_frames(args):
    """모든 프레임에 실제 모델 추론과 추적을 수행해 GIF 프레임을 만든다."""
    settings = Settings(load_config(args.config))
    detector, tracker = PersonDetector(settings), create_tracker(settings)
    frame_ids = sorted(int(path.stem) for path in (Path(args.data_root) / "videos" / args.clip).glob("*.jpg"))
    frame_ids = frame_ids[:args.frames] if args.frames else frame_ids
    images = []
    for frame_id in frame_ids:
        raw = read_frame(args.data_root, args.clip, frame_id)
        detections = detector.detect(raw)
        det_to_track = tracker.update(detections, raw)
        scale = min(1.0, args.width / raw.shape[1]) if args.width else 1.0
        canvas = resize(raw, args.width)
        draw_model_results(canvas, detections, det_to_track, scale)
        cv2.putText(canvas, f"{args.clip} | model | frame {frame_id:06d} | persons {len(detections)}",
                    (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (30, 220, 255), 2, cv2.LINE_AA)
        images.append(as_gif_frame(canvas))
    return images


def main():
    """선택된 프레임을 시간 순서로 렌더링해 하나의 GIF로 저장한다."""
    args = parse_args()
    if args.frames < 0 or args.width < 0 or args.fps <= 0:
        raise ValueError("--frames/--width는 0 이상, --fps는 0보다 커야 합니다.")

    if args.source == "model":
        images = render_model_frames(args)
    else:
        annotations = load_clip(args.data_root, args.clip)
        annotations = annotations[:args.frames] if args.frames else annotations
        images = [render_gt_frame(args.data_root, args.clip, item, args.width) for item in annotations]
    if not images:
        raise ValueError("시각화할 프레임이 없습니다.")
    path = output_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        path, save_all=True, append_images=images[1:], optimize=False,
        duration=round(1000 / args.fps), loop=0,
    )
    print(f"GIF 저장 완료: {path} ({len(images)} 프레임, {args.fps:g} FPS)")


if __name__ == "__main__":
    main()
