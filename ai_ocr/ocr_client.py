import os
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv


DEFAULT_MODEL_ID = "prebuilt-layout"
SUPPORTED_MODEL_IDS = ("prebuilt-read", "prebuilt-layout", "prebuilt-document")
MODEL_API_VERSIONS = {
    "prebuilt-document": "2023-07-31",
}


class OCRConfigError(RuntimeError):
    pass


class AzureOCRClient:
    def __init__(self):
        load_dotenv()

        self.endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        self.key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")

        if not self.endpoint or not self.key:
            raise OCRConfigError(
                ".env에 AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT와 "
                "AZURE_DOCUMENT_INTELLIGENCE_KEY를 설정해주세요."
            )

        self.client = self._build_client()

    def _build_client(self, model_id: str = DEFAULT_MODEL_ID):
        kwargs = {}
        if model_id in MODEL_API_VERSIONS:
            kwargs["api_version"] = MODEL_API_VERSIONS[model_id]

        return DocumentIntelligenceClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.key),
            **kwargs,
        )

    def analyze_image(self, image_path: str, model_id: str = DEFAULT_MODEL_ID):
        if model_id not in SUPPORTED_MODEL_IDS:
            raise ValueError(
                f"지원하지 않는 모델입니다: {model_id}. "
                f"사용 가능: {', '.join(SUPPORTED_MODEL_IDS)}"
            )

        image = Path(image_path)
        if not image.exists():
            raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")
        if not image.is_file():
            raise FileNotFoundError(f"이미지 경로가 파일이 아닙니다: {image_path}")

        client = self._build_client(model_id)

        with image.open("rb") as image_file:
            poller = client.begin_analyze_document(
                model_id=model_id,
                body=image_file,
            )

        result = poller.result()
        return extract_raw_lines(result)


def extract_raw_lines(result):
    lines = []

    for page in getattr(result, "pages", []) or []:
        for line in getattr(page, "lines", []) or []:
            x_values, y_values = polygon_to_xy(line.polygon)
            if not x_values or not y_values:
                continue

            lines.append(
                {
                    "text": line.content,
                    "page": page.page_number,
                    "x1": min(x_values),
                    "y1": min(y_values),
                    "x2": max(x_values),
                    "y2": max(y_values),
                    "confidence": 1.0,
                }
            )

    return lines


def polygon_to_xy(polygon):
    if not polygon:
        return [], []

    x_values = []
    y_values = []

    for point in polygon:
        if hasattr(point, "x") and hasattr(point, "y"):
            x_values.append(float(point.x))
            y_values.append(float(point.y))
        elif isinstance(point, dict) and "x" in point and "y" in point:
            x_values.append(float(point["x"]))
            y_values.append(float(point["y"]))

    if not x_values and all(isinstance(value, (int, float)) for value in polygon):
        coords = list(polygon)
        x_values = [float(value) for value in coords[0::2]]
        y_values = [float(value) for value in coords[1::2]]

    return x_values, y_values
