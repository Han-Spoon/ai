# -*- coding: utf-8 -*-
"""
재료명 정규화 스크립트 (배포용)

용도: 여러 명이 각자 다른 사이트를 크롤링하더라도, 같은 규칙으로 재료명을
      정규화해서 나중에 데이터를 합쳤을 때 같은 재료가 다른 이름으로
      남지 않게 합니다. 이 파일을 그대로 복사해서 각자 크롤링 결과에
      돌리면 됩니다. 외부 라이브러리 필요 없음 (표준 라이브러리만 사용).

입력 CSV 형식 (컬럼명 그대로 맞춰주세요):
    menu_query        - 76개 메뉴명 중 하나 (정확한 철자, 앞뒤 공백 없이)
    recipe_title       - 레시피 제목 (아무 문자열이나 가능, 참고용)
    recipe_url         - 레시피 고유 URL (메뉴당 레시피 수를 셀 때 이 값으로
                          중복 제거하니 반드시 레시피마다 유일해야 함)
    ingredient_name    - 크롤링한 재료명 원문 (정규화하지 말고 그대로! 수량은
                          필요 없음 - 사이트에서 수량이 이름에 안 섞여 나오게
                          가능하면 분리해서 크롤링해주세요)

실행:
    python normalize_ingredients.py 내크롤링결과.csv
    python normalize_ingredients.py 내크롤링결과.csv --out-prefix myname

출력 (기본적으로 입력 파일과 같은 폴더에 생성):
    <prefix>_normalized.csv   - menu_query, recipe_title, recipe_url,
                                 ingredient_raw, ingredient_canonical
    <prefix>_prior.csv        - menu_name, ingredient_canonical, n_total,
                                 k_count, freq, alpha_prior, beta_prior
    <prefix>_review.csv       - 규칙에 안 걸려서 원문 그대로 쓰인 재료명 중
                                 3회 이상 등장한 것 (검토용 - 새로운 변형이
                                 자주 보이면 단체 채팅방에 공유해주세요,
                                 규칙에 추가해서 다시 배포하겠습니다)

주의: 이 규칙 자체가 계속 업데이트되고 있습니다. 정규화를 각자 하더라도,
      최신 버전의 이 스크립트를 다 같이 맞춰서 쓰는 게 중요합니다.
      review.csv에서 자주 나오는 미매칭 재료를 발견하면 공유해주세요.
"""
import argparse
import csv
import os
import re
from collections import Counter, defaultdict

# ── 0) 도구 잔류 안전망 (혹시 재료 영역에 조리도구/문구가 섞여 들어온 경우) ──
TOOL_SUBSTR = [
    "냄비", "도마", "나이프", "요리스푼", "숟가락", "젓가락", "국자", "가위",
    "채반", "쟁반", "밧드", "믹싱볼", "장갑", "트레이", "볶음팬", "프라이팬",
    "후라이팬", "궁중팬", "계량컵", "계량스푼", "뚝배기", "접시", "그릇",
    # 우리의식탁 레시피/상품 영역에서 섞일 수 있는 도구 표현 보강
    "오븐", "에어프라이어", "전자레인지",
]

# ── 1) 정제 규칙 ──────────────────────────────────────────────
UNIT_SUFFIX_RE = re.compile(
    r"(\d+[\.\d/]*\s*(g|kg|ml|l|cm|mm|리터|cc|개|컵|큰술|작은술|스푼|줌|인분|팩|봉지|봉|모|장|알|마리|조각|톨|대|단|통|공기|꼬집|스틱|정도)?)\s*$",
    re.IGNORECASE,
)

# 색깔/크기 수식어는 재료 자체가 아니므로 접두/접미 양쪽에서 제거 대상.
# 주의: 단일 글자 색상(청/홍/백/흑/황/적)은 홍합·청국장처럼 단어 자체의 일부인
# 경우와 구분이 안 되어 위험하므로 제외하고, 2글자 이상 명확한 표현만 사용.
COLOR_SIZE_WORDS = [
    "분홍색", "노란색", "빨간색", "하얀색", "까만색", "보라색", "주황색", "초록색", "연두색", "갈색",
    "노랑", "빨강", "하양", "까망", "초록", "보라", "주황", "연두",
    "노란", "빨간", "하얀", "까만",
    "레드", "옐로우", "옐로", "그린", "블랙", "화이트", "핑크", "오렌지",
    "미니", "대형", "소형",
]
DESC_SUFFIX_PATTERNS = [
    "선택", "생략가능", "생략 가능", "조절", "취향껏", "기호에 따라", "기호에따라",
    "약간", "적당량", "적당히", "손질/절임용", "절임용", "손질용", "수북히",
    "듬뿍", "넉넉히", "작은거", "작은것", "큰것", "큰거", "작은 것", "큰 것",
    "기준", "정도", "가능", "무관", "시판용가능", "시판용",
    "갈은것", "간것", "갈은",
] + COLOR_SIZE_WORDS

# 다듬기 상태 접두어 (재료 정체성과 무관, 앞에 붙는 경우). 단일 글자 "간"은
# "간장"과 충돌 위험이 있어 제외, 2글자 이상 표현만 사용.
PREFIX_WORDS = COLOR_SIZE_WORDS + ["갈은"]


def clean_text(raw: str) -> str:
    s = raw.strip()
    # '=' 이후 단위 환산 설명 제거: "물1컵=237ml" -> "물1컵" (뒤이어 수량 제거됨)
    s = re.split(r"[=＝]", s)[0].strip()
    # 괄호 안 내용 제거: "곶감(말이)" -> "곶감"
    s = re.sub(r"\([^)]*\)", "", s)
    # 반복 적용: 끝쪽 수량표현 제거
    prev = None
    while prev != s:
        prev = s
        s = UNIT_SUFFIX_RE.sub("", s).strip()
    # 앞쪽 색깔/크기/다듬기 수식어 제거 (예: "노란 파프리카", "갈은 배")
    changed = True
    while changed:
        changed = False
        for pat in PREFIX_WORDS:
            if s.startswith(pat) and len(s) > len(pat):
                s = s[len(pat):].strip()
                changed = True
    # 부가 설명/색깔/크기 접미어 제거 (여러 번, 뒤에서부터) - 예: "파프리카 노랑"
    changed = True
    while changed:
        changed = False
        for pat in DESC_SUFFIX_PATTERNS:
            if s.endswith(pat) and len(s) > len(pat):
                s = s[: -len(pat)].strip()
                changed = True
    # 공백 전부 제거 (표기 통일용 키 생성 목적)
    s = re.sub(r"\s+", "", s)
    return s


# ── 2) 대표 재료명 매핑: 포함어 기반 규칙 (순서 중요! 먼저 매치되는 게 적용됨) ──
CATEGORY_RULES = [
    ("돼지고기", ["돼지고기", "돼지앞다리", "돼지목살", "돼지등심", "돼지안심", "삼겹살",
               "목살", "앞다리살", "뒷다리살", "돈육", "제육", "차돌", "대패삼겹",
               "돼지갈비", "등갈비", "돼지등뼈", "돼지족", "돼지목심"]),
    # 우삼겹은 이름에 '삼겹'이 들어가지만 '우'=소, 실제로는 소고기 부위
    ("소고기", ["소고기", "쇠고기", "우둔", "양지", "사태", "차돌박이", "등심스테이크",
              "채끝", "안심스테이크", "다짐육", "다진소고기", "다진 소고기",
              "우삼겹", "한우", "소불고기", "불고기감", "Beef", "beef"]),
    ("사골", ["사골", "잡뼈", "우족", "도가니"]),  # 소뼈 육수용 (소고기와는 별도 취급)
    ("곱창", ["곱창", "소곱창"]),
    ("닭고기", ["닭고기", "닭가슴살", "닭다리살", "닭안심", "닭날개", "영계", "생닭", "닭"]),
    ("마늘", ["다진마늘", "다진 마늘", "마늘"]),
    ("생강", ["다진생강", "다진 생강", "생강즙", "생강가루", "생강"]),
    ("대파", ["대파", "실파", "다진파", "파뿌리"]),
    ("쪽파", ["쪽파", "골파"]),
    ("양파", ["양파"]),
    ("당근", ["당근"]),
    ("애호박", ["애호박", "호박"]),
    ("청양고추", ["청양고추", "청고추", "청량고추", "땡초"]),
    # 홍고추는 별도 분류하지 않고 맨 아래 일반 '고추' 캐치올로 통합됨
    ("고춧가루", ["고춧가루", "고추가루", "고추 가루"]),
    ("고추장", ["고추장"]),
    ("된장", ["된장", "쌈장"]),
    ("간장", ["간장", "진간장", "국간장", "양조간장"]),
    ("참기름", ["참기름"]),
    ("들기름", ["들기름"]),
    ("식용유", ["식용유", "포도씨유", "카놀라유", "올리브유", "올리브오일", "해바라기씨유"]),
    ("설탕", ["설탕", "황설탕", "흑설탕", "비정제설탕"]),
    # 통깨가 소금보다 먼저 와야 함: "깨소금"에 "소금"이 부분문자열로 포함돼 있어서,
    # 순서가 바뀌면 참깨/깨소금이 전부 소금으로 잘못 분류됨
    ("통깨", ["통깨", "깨소금", "볶은깨", "참깨"]),
    ("소금", ["소금", "천일염"]),
    ("후춧가루", ["후춧가루", "후추가루", "후추"]),
    ("올리고당", ["올리고당", "물엿", "요리당", "조청"]),
    ("맛술", ["맛술", "청주", "미림"]),
    ("식초", ["식초"]),
    ("새우젓", ["새우젓"]),
    ("멸치액젓", ["멸치액젓", "까나리액젓", "액젓"]),
    ("멸치", ["멸치", "국물용멸치", "다시멸치", "디포리"]),
    ("새우", ["새우"]),  # 새우젓 규칙이 위에 있어 새우젓은 먼저 걸러짐
    ("오징어", ["오징어"]),
    ("전복", ["전복"]),
    ("꽃게", ["꽃게", "꽃개", "숫게", "블루크랩"]),  # '꽃개'는 '꽃게' 오타로 판단
    ("다시마", ["다시마"]),
    ("두부", ["두부", "손두부"]),
    ("계란", ["계란", "달걀", "계란노른자", "계란 노른자", "달걀노른자", "노른자"]),
    ("당면", ["당면"]),
    # 아래 4개는 전부 '무' 규칙보다 먼저 와야 함: "열무", "단무지", "쌈무", "총각무" 모두
    # 문자열에 '무'를 포함하고 있어서, 순서가 바뀌면 서로 다른 채소/가공식품이
    # 죄다 일반 '무'로 잘못 뭉쳐짐
    ("열무", ["열무"]),
    ("단무지", ["단무지"]),
    ("쌈무", ["쌈무"]),
    ("총각무", ["총각무", "알타리무", "알타리 무"]),
    ("무", ["무"]),
    ("얼갈이", ["얼갈이"]),
    ("양배추", ["양배추"]),  # '배추' 규칙보다 먼저 (양배추≠배추, 다른 채소)
    ("배추김치", ["배추김치", "김장김치", "신김치", "묵은지", "익은배추김치", "김치"]),
    ("배추", ["배추", "알배기", "알배추"]),  # 배추김치 규칙이 위에 있어 김치류는 먼저 걸러짐
    ("배즙", ["배즙"]),  # 일반 '배' 규칙보다 먼저 (즙은 다른 형태의 제품)
    ("갓", ["갓"]),
    ("호떡믹스", ["호떡믹스", "호떡잼믹스"]),  # '떡' 규칙보다 먼저 (호떡은 쌀떡이 아님)
    ("떡", ["떡"]),  # 가래떡/밀떡/떡국떡/떡볶이떡 모두 '떡'으로 통합
    ("만두", ["만두"]),
    ("순대", ["순대"]),
    ("햄", ["햄", "스팸"]),  # '김' 규칙보다 먼저 ('김밥용햄' 오매칭 방지)
    ("김", ["김"]),  # 배추김치/햄 규칙이 위에 있어야 안전
    ("어묵", ["어묵", "오뎅"]),
    ("밀가루", ["밀가루", "박력분", "중력분", "강력분"]),
    ("찹쌀가루", ["찹쌀가루"]),
    ("멥쌀가루", ["멥쌀가루", "쌀가루"]),
    ("표고버섯", ["표고버섯", "말린표고", "건표고"]),
    ("느타리버섯", ["느타리버섯"]),
    ("새송이버섯", ["새송이버섯"]),
    ("목이버섯", ["목이버섯"]),
    ("팽이버섯", ["팽이버섯"]),
    ("시금치", ["시금치"]),
    ("부추", ["부추"]),
    ("숙주", ["숙주", "숙주나물"]),
    ("깻잎", ["깻잎"]),
    ("굴소스", ["굴소스", "굴 소스"]),
    ("버터", ["버터"]),
    ("우유", ["우유"]),
    ("생크림", ["생크림"]),
    ("치즈", ["치즈", "슬라이스치즈", "피자치즈"]),
    ("베이킹파우더", ["베이킹파우더", "베이킹 파우더"]),
    ("전분", ["전분", "옥수수전분", "감자전분"]),
    ("케첩", ["케첩", "케찹"]),
    ("계피", ["계피", "계핏가루", "통계피"]),
    ("갈비", ["소갈비", "LA갈비", "갈비"]),  # 돼지갈비/등갈비는 위 돼지고기 규칙에서 먼저 걸러짐
    ("매실액", ["매실액", "매실청", "매실액기스"]),
    ("튀김가루", ["튀김가루", "치킨튀김가루", "치킨가루"]),
    ("냉면사리", ["냉면사리", "냉면면"]),
    ("칼국수면", ["칼국수면"]),
    ("파프리카", ["파프리카"]),
    ("카스테라", ["카스테라"]),
    ("감자", ["감자"]),  # 감자전분은 위 '전분' 규칙이 먼저 걸러서 안전
    ("배", ["배"]),  # 양배추/배추/배즙 규칙이 위에 있어야 안전 (순서 중요)
    ("채소", ["녹색채소", "채소"]),  # "고수또는 녹색채소" 같이 애매한 대체 표현용
    # 일반 '고추' 캐치올 - 청양고추/고춧가루/고추장/꽈리고추 등 구체적인 규칙보다
    # 아래(뒤)에 있어야 함. "매운고추", "마른고추", "다진고추", "홍고추" 등이 여기로 모임.
    ("고추", ["고추"]),
]

EXACT_MAP = {
    # clean_text 이후(공백 제거된) 정확일치 사전 - 규칙으로 못 잡는 것들 보강
    "물": "물",
    "생수": "물",
    "뜨거운물": "물",
    "미지근한물": "물",
    "따뜻한물": "물",
    "찬물": "물",
    "쌀뜨물": "쌀뜨물",
    "멸치육수": "멸치육수",
    "육수": "육수",
    "다시마육수": "육수",
    "김칫국물": "김치국물",
    "김치국물": "김치국물",
    # '무'(radish) 규칙이 부분문자열로 오매칭하던 것들 - 나무 이름(-나무는 '무'와 무관)과
    # '무염'(=염 없음, 부정 접두어 '무'이지 재료 '무'가 아님)
    "헛개나무": "헛개나무",
    "엄나무": "엄나무",
    "벌나무": "벌나무",
    "무염버터": "버터",
}


def canonicalize(cleaned: str):
    """returns (canonical_name, matched: bool) - matched=False면 사전/규칙에 안 걸려서 원문을 그대로 쓴 것"""
    if not cleaned:
        return cleaned, False
    if cleaned in EXACT_MAP:
        return EXACT_MAP[cleaned], True
    for canon, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in cleaned:
                return canon, True
    return cleaned, False  # fallback: 정제된 원문 그대로 (미매칭)


def is_tool_leak(cleaned: str) -> bool:
    return any(t in cleaned for t in TOOL_SUBSTR)


def main():
    parser = argparse.ArgumentParser(description="재료명 정규화 스크립트")
    parser.add_argument("input_csv", help="크롤링 결과 CSV (menu_query, recipe_title, recipe_url, ingredient_name 컬럼 필요)")
    parser.add_argument("--out-prefix", default=None, help="출력 파일 이름 접두어 (기본값: 입력 파일명)")
    parser.add_argument("--out-dir", default=None, help="출력 폴더 (기본값: 입력 파일과 같은 폴더)")
    args = parser.parse_args()

    in_path = args.input_csv
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(in_path))
    prefix = args.out_prefix or os.path.splitext(os.path.basename(in_path))[0]

    out_tidy = os.path.join(out_dir, f"{prefix}_normalized.csv")
    out_prior = os.path.join(out_dir, f"{prefix}_prior.csv")
    out_review = os.path.join(out_dir, f"{prefix}_review.csv")

    with open(in_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    tidy_rows = []
    dropped_tools = 0
    unmatched_counter = Counter()
    for r in rows:
        raw = r["ingredient_name"]
        cleaned = clean_text(raw)
        if not cleaned or is_tool_leak(cleaned):
            dropped_tools += 1
            continue
        canon, matched = canonicalize(cleaned)
        if not matched:
            unmatched_counter[cleaned] += 1
        tidy_rows.append({
            "menu_query": r["menu_query"],
            "recipe_title": r["recipe_title"],
            "recipe_url": r["recipe_url"],
            "ingredient_raw": raw,
            "ingredient_canonical": canon,
        })

    with open(out_tidy, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "menu_query", "recipe_title", "recipe_url", "ingredient_raw", "ingredient_canonical"
        ])
        w.writeheader()
        w.writerows(tidy_rows)

    raw_counter = Counter(r["ingredient_raw"] for r in tidy_rows)
    canon_counter = Counter(r["ingredient_canonical"] for r in tidy_rows)
    total = len(tidy_rows)
    unmatched_total = sum(unmatched_counter.values())

    print(f"총 행: {len(rows)} -> 도구잔류/빈값 제거 후: {total} (제거 {dropped_tools}건)")
    print(f"원본 고유 재료명: {len(raw_counter)} -> 정규화 후 고유 재료명: {len(canon_counter)}")
    print(f"사전/규칙에 매칭된 행 비율: {(total-unmatched_total)/total*100:.1f}% "
          f"(매칭 {total-unmatched_total}건 / 미매칭 {unmatched_total}건)")

    menu_recipe_count = defaultdict(set)
    pair_recipe_set = defaultdict(set)
    for r in tidy_rows:
        menu_recipe_count[r["menu_query"]].add(r["recipe_url"])
        pair_recipe_set[(r["menu_query"], r["ingredient_canonical"])].add(r["recipe_url"])

    A0, B0 = 1.0, 1.0  # 라플라스 스무딩 기본값
    prior_rows = []
    for (menu, canon), urlset in pair_recipe_set.items():
        n_total = len(menu_recipe_count[menu])
        k = len(urlset)
        alpha = k + A0
        beta = (n_total - k) + B0
        prior_rows.append({
            "menu_name": menu,
            "ingredient_canonical": canon,
            "n_total": n_total,
            "k_count": k,
            "freq": round(k / n_total, 3) if n_total else 0,
            "alpha_prior": alpha,
            "beta_prior": beta,
        })
    prior_rows.sort(key=lambda r: (r["menu_name"], -r["freq"]))

    with open(out_prior, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "menu_name", "ingredient_canonical", "n_total", "k_count",
            "freq", "alpha_prior", "beta_prior"
        ])
        w.writeheader()
        w.writerows(prior_rows)

    print(f"\n(메뉴, 재료) 쌍 수: {len(prior_rows)}")

    review = [(n, c) for n, c in unmatched_counter.items() if c >= 3]
    review.sort(key=lambda x: -x[1])
    with open(out_review, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ingredient_cleaned", "count"])
        w.writerows(review)

    print(f"미매칭 고유 재료명 수: {len(unmatched_counter)}, 그중 3회 이상 등장: {len(review)}건 -> {out_review}")
    print("\n미매칭 상위 20개 (자주 보이면 단체방에 공유해주세요):")
    for n, c in review[:20]:
        print(f"  {c:4d}  {n}")

    print(f"\n완료:\n  {out_tidy}\n  {out_prior}\n  {out_review}")


if __name__ == "__main__":
    main()
