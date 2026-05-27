import json
import os
from ocr_client import AzureOCRClient

MODELS = [
    "prebuilt-read",
    "prebuilt-layout",
    "prebuilt-document"
]

IMAGE_PATH = "images/menu_001.jpg"

client = AzureOCRClient()

os.makedirs("outputs/model_compare", exist_ok=True)

for model_id in MODELS:
    print(f"Testing model: {model_id}")

    lines = client.analyze_image(IMAGE_PATH, model_id=model_id)

    output_path = f"outputs/model_compare/{model_id}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(lines, f, ensure_ascii=False, indent=2)

    print(f"Saved: {output_path}")