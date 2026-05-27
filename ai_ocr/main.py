import json
import os
from ocr_client import AzureOCRClient
from parser import parse_menu_candidates

def analyze_menu_image(image_path: str, model_id: str = "prebuilt-layout"):
    client = AzureOCRClient()

    lines = client.analyze_image(image_path, model_id=model_id)
    menus = parse_menu_candidates(lines)

    result = {
        "image": os.path.basename(image_path),
        "modelId": model_id,
        "menus": menus,
        "rawLines": lines
    }

    return result


if __name__ == "__main__":
    image_path = "images/menu_001.jpg"

    result = analyze_menu_image(image_path, model_id="prebuilt-layout")

    os.makedirs("outputs", exist_ok=True)

    with open("outputs/menu_001_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))