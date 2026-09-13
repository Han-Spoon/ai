def count_matched_price_anchors(menus: list[dict]) -> int:
    """옵션 가격을 포함해 실제 메뉴에 연결된 가격 anchor 수를 센다."""
    matched = 0
    for menu in menus:
        options = [
            option
            for option in menu.get("options", [])
            if option.get("priceRaw") or option.get("price") is not None
        ]
        matched += len(options) if options else int(
            bool(menu.get("priceRaw") or menu.get("price") is not None)
        )
    return matched
