"""Page bodies for kept search results (docs/6-agent-websearch.md §2-3-1)."""

import asyncio
import json

import httpx

from ai_web_search_agent import HttpPageFetcher, SearchResult
from ai_web_search_agent.pages import page_content, readable_url

NAV = "<nav>두부 찌개 소고기 국 오이 무침</nav>"


def recipe_page(ingredients: list[str]) -> str:
    data = {"@context": "https://schema.org", "@type": "Recipe", "recipeIngredient": ingredients}
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}'
        f"</script></head><body>{NAV}<p>참치김치찌개</p></body></html>"
    )


def test_recipe_data_gives_the_ingredient_list_only() -> None:
    # 10000recipe's page text starts with a menu full of food names; the JSON-LD does not.
    content = page_content(recipe_page(["신김치 1국그릇", "참치 1캔"]))

    assert content == "재료: 신김치 1국그릇, 참치 1캔"


def test_recipe_data_inside_a_graph_is_found() -> None:
    data = {"@graph": [{"@type": "WebPage"}, {"@type": ["Recipe"], "recipeIngredient": ["두부 1모"]}]}
    page = f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'

    assert page_content(page) == "재료: 두부 1모"


def test_pages_without_recipe_data_use_visible_text() -> None:
    page = (
        "<html><head><title>t</title><script>var x = '소고기';</script></head>"
        f"<body>{NAV}<div>재료: 돼지고기   200g,\n두부</div></body></html>"
    )

    assert page_content(page) == "재료: 돼지고기 200g, 두부"


def test_semie_user_post_stops_where_the_post_ends() -> None:
    # 2026-11-12: 꽃게탕 got 우유·요구르트 from the writer's other posts listed below.
    page = (
        "<html><body><div>요리해요 꽃게탕 2024.09.18 23:07 꽃게와 된장을 넣어 끓여요</div>"
        "<div>댓글 0 로그인 하시고 댓글을 남겨보세요.</div>"
        "<div>댕댕이님 의 다른 글도 살펴보세요 스크랩 댕댕이 2025.05.13 01:30 우유와 요구르트로 요거트 만들기</div>"
        "</body></html>"
    )
    url = "https://semie.cooking/cooking/id/6053"
    assert page_content(page, url) == "요리해요 꽃게탕 2024.09.18 23:07 꽃게와 된장을 넣어 끓여요 댓글 0"
    # The site menu before the post is cut too.
    headed = page.replace("<div>요리해요", "<div>로그인 해주세요 회원가입 홈 공유 × 카카오톡 URL복사 요리해요", 1)
    assert page_content(headed, url).startswith("요리해요 꽃게탕")
    # Other sites keep the whole text.
    assert "요구르트" in page_content(page, "https://blog.example.com/1")


def test_mangae_post_without_ingredient_lines_reads_the_recipe_only() -> None:
    # 2026-11-18: 멍게비빔밥 (/recipe/5838234) has Recipe data with no recipeIngredient. The
    # page text held the site menu and other dishes ("짬뽕 맛보장 레시피 더보기").
    data = {"@context": "http://schema.org/", "@type": "Recipe", "name": "멍게비빔밥"}
    page = (
        f'<html><head><script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}'
        f"</script></head><body>{NAV}<div>소고기 국 두부 조림 제육 볶음</div>"
        '<div class="view2_summary st3"><h3>멍게비빔밥</h3></div>'
        '<div class="view_step"><div class="view_cont_old"><div>재료 멍게5개, 고추장 4T</div>'
        "<p>밥 위에 멍게를 올려요</p></div></div>"
        '<div id="divRelativeRecipes">짬뽕 맛보장 레시피 더보기 후라이드치킨</div></body></html>'
    )
    url = "https://www.10000recipe.com/recipe/5838234"

    assert page_content(page, url) == "멍게비빔밥 재료 멍게5개, 고추장 4T 밥 위에 멍게를 올려요"
    # Newer posts: the ingredient box and steps.
    newer = page.replace('class="view_cont_old"', 'class="cont_ingre2"')
    assert "멍게5개" in page_content(newer, url)
    # No recipe part on the page: nothing is read, so the title and description stay.
    bare = page.replace("view2_summary", "x").replace("view_cont_old", "x")
    assert page_content(bare, url) == ""


def test_wtable_text_stops_before_products_and_user_notes() -> None:
    page = (
        "<html><body><div>해물 칼국수 재료 칼국수면 2인분 감자 1개</div><div>Step 1 감자를 잘라요</div>"
        "<div>광고때문에 보기 불편하시죠? 1초만에 로그인/회원가입 하러가기</div>"
        "<div>이 레시피를 위한 추천 상품 코인육수 (멸치/해물)</div>"
        "<div>Wisteria 팁 고추가루대신 파프리카가루넣고 밑간했어요</div></body></html>"
    )
    url = "https://wtable.co.kr/recipes/7ikM4CQvgdXGqYWuM3FP6o9k"

    assert page_content(page, url) == "해물 칼국수 재료 칼국수면 2인분 감자 1개 Step 1 감자를 잘라요"
    # Other sites keep the whole text.
    assert "파프리카가루" in page_content(page, "https://blog.example.com/1")


def test_naver_blog_posts_are_read_from_the_mobile_page() -> None:
    assert readable_url("https://blog.naver.com/love-cafe/224453539431") == (
        "https://m.blog.naver.com/love-cafe/224453539431"
    )
    assert readable_url("https://blog.naver.com/PostView.naver?blogId=a&logNo=1") == (
        "https://m.blog.naver.com/a/1"
    )
    assert readable_url("https://www.10000recipe.com/recipe/1") == "https://www.10000recipe.com/recipe/1"


def test_fetcher_fills_content_and_keeps_unreadable_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "m.blog.naver.com":
            return httpx.Response(200, text="<p>재료: 김치, 두부</p>")
        return httpx.Response(404)

    fetcher = HttpPageFetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    blog = SearchResult(query="q", title="김치찌개", url="https://blog.naver.com/a/1", snippet="s")
    gone = SearchResult(query="q", title="김치찌개", url="https://www.10000recipe.com/recipe/1")

    async def both():
        return await asyncio.gather(fetcher.fetch(blog), fetcher.fetch(gone))

    filled, kept = asyncio.run(both())

    assert filled.content == "재료: 김치, 두부"
    assert filled.url == blog.url
    assert kept == gone


def test_own_client_is_made_per_event_loop() -> None:
    # Evaluation scripts call asyncio.run more than once with one fetcher; a client from a
    # closed loop must not be reused.
    fetcher = HttpPageFetcher()

    async def client():
        return fetcher._client_now()

    first, other = asyncio.run(client()), asyncio.run(client())
    assert first is not other
