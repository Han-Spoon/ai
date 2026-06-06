"""
Hidden ingredient candidate DB.

Converted to the ai_result format:

    menu_or_ingredient -> ambiguity flag -> [{"name": hidden_name, "tag": tag}]
"""


HIDDEN_RULES: dict[str, dict[str, list[dict]]] = {
    "김치": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "액젓", "tag": "is_shellfish"},
            {"name": "멸치액젓", "tag": "is_fish"},
            {"name": "까나리액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "배추김치": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "액젓", "tag": "is_shellfish"},
            {"name": "멸치액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "열무김치": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "액젓", "tag": "is_shellfish"},
            {"name": "멸치액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "신김치": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "김칫국물": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "김칫잎": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "김치잎": {
        "has_unclear_jeotgal": [
            {"name": "액젓", "tag": "is_fish"},
            {"name": "새우젓", "tag": "is_shellfish"},
        ],
    },
    "된장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "된장찌개": {
        "has_unclear_broth": [
            {"name": "멸치육수", "tag": "is_fish"},
            {"name": "육수용 멸치", "tag": "is_fish"},
        ],
    },
    "일본된장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "청국장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "고추장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "찹쌀", "tag": "is_soybean"},
            {"name": "찹쌀", "tag": "is_wheat"},
        ],
    },
    "초고추장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "식초", "tag": "is_soybean"},
            {"name": "식초", "tag": "is_wheat"},
        ],
    },
    "쌈장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "돼지고기 지방", "tag": "is_pork"},
        ],
    },
    "간장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "진간장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "국간장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "청장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "햇살담은간장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "양념간장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "조림간장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "두반장": {
        "has_unclear_jeotgal": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "순창콩된장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "아기된장소스": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "새우젓": {
        "has_unclear_jeotgal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "새우젓국": {
        "has_unclear_jeotgal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "멸치액젓": {
        "has_unclear_jeotgal": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "까나리액젓": {
        "has_unclear_jeotgal": [
            {"name": "까나리", "tag": "is_fish"},
        ],
    },
    "액젓": {
        "has_unclear_jeotgal": [
            {"name": "멸치", "tag": "is_fish"},
            {"name": "까나리", "tag": "is_fish"},
        ],
    },
    "액체육젓": {
        "has_unclear_jeotgal": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "젓국": {
        "has_unclear_jeotgal": [
            {"name": "새우", "tag": "is_fish"},
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "멸치젓": {
        "has_unclear_jeotgal": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "전어젓갈": {
        "has_unclear_jeotgal": [
            {"name": "전어", "tag": "is_fish"},
        ],
    },
    "감동젓": {
        "has_unclear_jeotgal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "피쉬소스": {
        "has_unclear_seasoning": [
            {"name": "멸치", "tag": "is_fish"},
            {"name": "어류", "tag": "is_fish"},
        ],
    },
    "청정원어장": {
        "has_unclear_seasoning": [
            {"name": "멸치", "tag": "is_fish"},
            {"name": "어류", "tag": "is_fish"},
        ],
    },
    "멸칫국물": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "멸치다시물": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "국물용멸치": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "육수용멸치": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "국멸치": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "잔멸치": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "쇠고기육수": {
        "has_unclear_broth": [
            {"name": "쇠고기", "tag": "is_beef"},
        ],
    },
    "쇠고기 육수": {
        "has_unclear_broth": [
            {"name": "쇠고기", "tag": "is_beef"},
        ],
    },
    "닭육수": {
        "has_unclear_broth": [
            {"name": "닭고기", "tag": "is_chicken"},
        ],
    },
    "육수": {
        "has_unclear_broth": [
            {"name": "멸치", "tag": "is_fish"},
            {"name": "다시마", "tag": "is_fish"},
            {"name": "다시마", "tag": "is_beef"},
            {"name": "쇠고기", "tag": "is_beef"},
        ],
    },
    "다시물": {
        "has_unclear_broth": [
            {"name": "다시마", "tag": "is_fish"},
            {"name": "멸치", "tag": "is_fish"},
        ],
    },
    "다시마국물": {
        "has_unclear_broth": [
            {"name": "다시마", "tag": None},
        ],
    },
    "가쓰오브시": {
        "has_unclear_broth": [
            {"name": "가다랑어", "tag": "is_fish"},
        ],
    },
    "동치미국물": {
        "has_unclear_broth": [
        ],
    },
    "청주": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "미림": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
        ],
    },
    "맛술": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
        ],
    },
    "술": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
        ],
    },
    "요리술": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
        ],
    },
    "조미술": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
        ],
    },
    "정종": {
        "has_unclear_seasoning": [
            {"name": "알코올", "tag": "is_alcohol"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "어묵": {
        "has_hidden_animal": [
            {"name": "생선살", "tag": "is_fish"},
            {"name": "생선살", "tag": "is_wheat"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "전분", "tag": "is_fish"},
            {"name": "전분", "tag": "is_wheat"},
        ],
    },
    "게맛살": {
        "has_hidden_animal": [
            {"name": "생선살", "tag": "is_fish"},
            {"name": "게 향료", "tag": "is_crab"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "맛살": {
        "has_hidden_animal": [
            {"name": "생선살", "tag": "is_fish"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "맛살조개": {
        "has_hidden_animal": [
            {"name": "생선살", "tag": "is_fish"},
            {"name": "조개향료", "tag": "is_shellfish"},
        ],
    },
    "햄": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
            {"name": "소금", "tag": "is_pork"},
            {"name": "아질산나트륨", "tag": "is_pork"},
        ],
    },
    "네모난햄": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
        ],
    },
    "베이컨": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
        ],
    },
    "프랑크소시지": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "비엔나소시지": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "순대": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
            {"name": "돼지피", "tag": "is_pork"},
            {"name": "당면", "tag": "is_pork"},
        ],
    },
    "장조림": {
        "has_hidden_animal": [
            {"name": "쇠고기", "tag": "is_beef"},
            {"name": "간장", "tag": "is_beef"},
            {"name": "간장", "tag": "is_soybean"},
            {"name": "간장", "tag": "is_wheat"},
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "유부": {
        "has_hidden_animal": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "식용유", "tag": "is_soybean"},
        ],
    },
    "두부": {
        "has_hidden_animal": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "순두부": {
        "has_hidden_animal": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "콩비지": {
        "has_hidden_animal": [
            {"name": "대두", "tag": "is_soybean"},
        ],
    },
    "라면": {
        "has_hidden_animal": [
            {"name": "밀", "tag": "is_wheat"},
            {"name": "돼지고기 추출물", "tag": "is_pork"},
            {"name": "쇠고기 추출물", "tag": "is_beef"},
        ],
    },
    "굴소스": {
        "has_unclear_seasoning": [
            {"name": "굴", "tag": "is_shellfish"},
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "청정원굴소스": {
        "has_unclear_seasoning": [
            {"name": "굴", "tag": "is_shellfish"},
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "마요네즈": {
        "has_unclear_seasoning": [
            {"name": "계란", "tag": "is_egg"},
            {"name": "식초", "tag": "is_egg"},
            {"name": "식물성기름", "tag": "is_egg"},
        ],
    },
    "버터": {
        "has_unclear_seasoning": [
            {"name": "우유", "tag": "is_milk"},
        ],
    },
    "양념장": {
        "has_unclear_seasoning": [
            {"name": "간장", "tag": "is_soybean"},
            {"name": "간장", "tag": "is_wheat"},
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "참기름", "tag": "is_soybean"},
            {"name": "참기름", "tag": "is_wheat"},
        ],
    },
    "양념": {
        "has_unclear_seasoning": [
            {"name": "간장", "tag": "is_soybean"},
            {"name": "간장", "tag": "is_wheat"},
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "청정원맛선생": {
        "has_unclear_seasoning": [
            {"name": "멸치", "tag": "is_fish"},
            {"name": "다시마", "tag": "is_fish"},
        ],
    },
    "청정원국선생": {
        "has_unclear_seasoning": [
            {"name": "멸치", "tag": "is_fish"},
            {"name": "다시마", "tag": "is_fish"},
        ],
    },
    "청정원순창쌈장": {
        "has_unclear_seasoning": [
            {"name": "대두", "tag": "is_soybean"},
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "오징어채": {
        "has_hidden_animal": [
            {"name": "오징어", "tag": "is_squid"},
        ],
    },
    "마른오징어": {
        "has_hidden_animal": [
            {"name": "오징어", "tag": "is_squid"},
        ],
    },
    "마른새우": {
        "has_hidden_animal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "말린새우": {
        "has_hidden_animal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "건새우가루": {
        "has_hidden_animal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "잔새우": {
        "has_hidden_animal": [
            {"name": "새우", "tag": "is_shellfish"},
            {"name": "새우", "tag": "is_shrimp"},
        ],
    },
    "참치통조림": {
        "has_hidden_animal": [
            {"name": "참치", "tag": "is_fish"},
        ],
    },
    "꽁치통조림": {
        "has_hidden_animal": [
            {"name": "꽁치", "tag": "is_fish"},
        ],
    },
    "골뱅이통조림": {
        "has_hidden_animal": [
            {"name": "골뱅이", "tag": "is_shellfish"},
        ],
    },
    "튀김가루": {
        "has_hidden_animal": [
            {"name": "밀", "tag": "is_wheat"},
            {"name": "전분", "tag": "is_wheat"},
            {"name": "베이킹파우더", "tag": "is_wheat"},
        ],
    },
    "부침가루": {
        "has_hidden_animal": [
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "빵가루": {
        "has_hidden_animal": [
            {"name": "밀", "tag": "is_wheat"},
            {"name": "계란", "tag": "is_egg"},
            {"name": "우유", "tag": "is_milk"},
        ],
    },
    "만두피": {
        "has_hidden_animal": [
            {"name": "밀", "tag": "is_wheat"},
        ],
    },
    "땅콩가루": {
        "has_unclear_seasoning": [
            {"name": "땅콩", "tag": "is_peanut"},
        ],
    },
    "땅콩버터": {
        "has_unclear_seasoning": [
            {"name": "땅콩", "tag": "is_peanut"},
        ],
    },
    "만두": {
        "has_hidden_animal": [
            {"name": "돼지고기", "tag": "is_pork"},
            {"name": "밀", "tag": "is_wheat"},
            {"name": "부추", "tag": "is_pork"},
            {"name": "부추", "tag": "is_wheat"},
        ],
    },
    "김밥": {
        "has_hidden_animal": [
            {"name": "계란", "tag": "is_egg"},
            {"name": "햄", "tag": "is_pork"},
            {"name": "게맛살", "tag": "is_fish"},
        ],
    },
}
