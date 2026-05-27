from datetime import datetime, timezone
from pathlib import Path
import mimetypes


def build_final_result(
    image_path: str,
    menus,
    scan_status: str = "completed",
    source: str = "upload",
    storage_key: str | None = None,
    image_url: str | None = None,
):
    image = Path(image_path)

    return {
        "scan_session": {
            "title": image.name,
            "menu_count": len(menus),
            "risky_menu_count": None,
            "scan_status": scan_status,
            "scanned_at": current_timestamp(),
        },
        "menu_image": {
            "source": source,
            "storage_key": storage_key,
            "image_url": image_url,
            "mime_type": infer_mime_type(image),
            "file_size": infer_file_size(image),
        },
        "menu_analyses": [
            build_menu_analysis(menu, index)
            for index, menu in enumerate(menus, start=1)
        ],
    }


def build_menu_analysis(menu, display_order: int):
    return {
        "menu_name_ko": menu.get("matchedMenu") or menu.get("normalizedCandidate") or menu.get("rawName"),
        "menu_name_en": None,
        "description_ko": menu.get("description") or "",
        "description_en": None,
        "price_text": menu.get("priceRaw") or stringify_price(menu.get("price")),
        "risk_level": None,
        "is_spicy": None,
        "image_url": None,
        "display_order": display_order,
    }


def current_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def infer_mime_type(image: Path):
    mime_type, _ = mimetypes.guess_type(image.name)
    return mime_type


def infer_file_size(image: Path):
    if image.exists() and image.is_file():
        return image.stat().st_size
    return None


def stringify_price(price):
    if price is None:
        return None
    return str(price)
