"""Page bodies for kept search results (docs/6-agent-websearch.md §2-3-1)."""

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

    fetcher = HttpPageFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    blog = SearchResult(query="q", title="김치찌개", url="https://blog.naver.com/a/1", snippet="s")
    gone = SearchResult(query="q", title="김치찌개", url="https://www.10000recipe.com/recipe/1")

    filled, kept = fetcher.fetch(blog), fetcher.fetch(gone)

    assert filled.content == "재료: 김치, 두부"
    assert filled.url == blog.url
    assert kept == gone
