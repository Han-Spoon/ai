import os
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

load_dotenv()

class AzureOCRClient:
    def __init__(self):
        self.endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        self.key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")

        self.client = DocumentIntelligenceClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.key)
        )

    def analyze_image(self, image_path: str, model_id: str = "prebuilt-layout"):
        with open(image_path, "rb") as f:
            poller = self.client.begin_analyze_document(
                model_id=model_id,
                body=f
            )

        result = poller.result()

        lines = []

        for page in result.pages:
            for line in page.lines:
                polygon = line.polygon

                xs = [p.x for p in polygon]
                ys = [p.y for p in polygon]

                lines.append({
                    "text": line.content,
                    "page": page.page_number,
                    "x1": min(xs),
                    "y1": min(ys),
                    "x2": max(xs),
                    "y2": max(ys),
                    "confidence": 1.0
                })

        return lines