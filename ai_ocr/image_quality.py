from pathlib import Path


def analyze_image_quality(image_path: str | Path) -> dict:
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        return {
            "available": False,
            "error": f"이미지 품질 분석 패키지를 불러오지 못했습니다: {error}",
            "score": None,
            "reasons": [],
            "suggestions": [],
        }

    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return {
            "available": False,
            "error": "이미지 파일을 찾을 수 없어 품질 분석을 건너뜁니다.",
            "score": None,
            "reasons": [],
            "suggestions": [],
        }

    bgr = cv2.imread(str(path))
    if bgr is None:
        return {
            "available": False,
            "error": "이미지 파일을 열 수 없어 품질 분석을 건너뜁니다.",
            "score": None,
            "reasons": [],
            "suggestions": [],
        }

    height, width = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    blur_score = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)
    brightness = round(float(gray.mean()), 2)
    contrast = round(float(gray.std()), 2)
    glare_ratio = round(float(np.mean((hsv[:, :, 2] >= 245) & (hsv[:, :, 1] <= 45))), 4)
    skew_angle = estimate_skew_angle(gray, cv2, np)

    reasons = []
    suggestions = []
    score = 100

    if min(width, height) < 600:
        score -= 25
        reasons.append("이미지 해상도가 낮아 작은 글자 판독이 어려울 수 있습니다.")
        suggestions.append("메뉴판 전체가 보이도록 더 가까이에서 촬영해주세요.")

    if blur_score < 80:
        score -= 30
        reasons.append("이미지가 흐려 글자 경계가 선명하지 않습니다.")
        suggestions.append("카메라 초점을 메뉴판 글자에 맞춘 뒤 흔들리지 않게 다시 촬영해주세요.")
    elif blur_score < 140:
        score -= 12
        reasons.append("이미지 선명도가 다소 낮습니다.")
        suggestions.append("조금 더 밝은 곳에서 초점을 맞춰 촬영하면 OCR 정확도가 좋아집니다.")

    if brightness < 65:
        score -= 18
        reasons.append("이미지가 어두워 글자 대비가 낮습니다.")
        suggestions.append("조명을 확보하거나 메뉴판을 더 밝은 위치에서 촬영해주세요.")
    elif brightness > 220:
        score -= 15
        reasons.append("이미지가 너무 밝아 글자가 날아갈 수 있습니다.")
        suggestions.append("노출을 낮추거나 빛을 직접 받지 않는 각도로 촬영해주세요.")

    if contrast < 35:
        score -= 15
        reasons.append("글자와 배경의 대비가 낮습니다.")
        suggestions.append("그림자나 반사가 적은 각도에서 메뉴판을 정면으로 촬영해주세요.")

    if glare_ratio >= 0.08:
        score -= 25
        reasons.append("강한 빛 반사로 보이는 영역이 많습니다.")
        suggestions.append("빛 반사가 사라지도록 촬영 각도를 조금 바꿔주세요.")
    elif glare_ratio >= 0.035:
        score -= 10
        reasons.append("일부 영역에 빛 반사가 있을 수 있습니다.")
        suggestions.append("반사되는 조명을 피해서 다시 촬영하면 더 안정적입니다.")

    if skew_angle is not None and abs(skew_angle) >= 8:
        score -= 15
        reasons.append("메뉴판 또는 글자가 크게 기울어져 있습니다.")
        suggestions.append("메뉴판을 화면과 평행하게 맞춰 정면에서 촬영해주세요.")
    elif skew_angle is not None and abs(skew_angle) >= 4:
        score -= 7
        reasons.append("메뉴판 또는 글자가 약간 기울어져 있습니다.")

    return {
        "available": True,
        "score": max(0, min(100, score)),
        "blur_score": blur_score,
        "brightness": brightness,
        "contrast": contrast,
        "glare_ratio": glare_ratio,
        "skew_angle": skew_angle,
        "reasons": dedupe(reasons),
        "suggestions": dedupe(suggestions),
    }


def estimate_skew_angle(gray, cv2, np):
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    threshold = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 3))
    connected = cv2.dilate(threshold, kernel, iterations=1)

    coords = np.column_stack(np.where(connected > 0))
    if len(coords) < 100:
        return None

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) > 45:
        return None

    return round(float(angle), 2)


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
