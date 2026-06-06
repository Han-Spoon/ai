# ──────────────────────────────────────────────────────────────
# 한스푼 AI — FastAPI 래퍼 (Azure Container Apps / 로컬 공용)
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# 파이썬 런타임 설정
#   PYTHONDONTWRITEBYTECODE: .pyc 파일 생성 안 함
#   PYTHONUNBUFFERED       : 로그 즉시 출력 (버퍼링 X)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# OpenCV(opencv-python-headless) 런타임 시스템 의존성
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 먼저 설치 → 코드만 바뀔 때 이 레이어는 캐시 재사용
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
