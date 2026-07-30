import unittest

from bs4 import BeautifulSoup

from rank_monitor import (
    extract_google_play_category,
    has_google_play_pre_registration_action,
)


class GooglePlayPreRegistrationTests(unittest.TestCase):
    def parse(self, html):
        return BeautifulSoup(html, "lxml")

    def test_accepts_traditional_chinese_pre_registration_button(self):
        soup = self.parse("<button aria-label='預先註冊'>預先註冊</button>")
        self.assertTrue(has_google_play_pre_registration_action(soup))

    def test_accepts_english_pre_registration_role_button(self):
        soup = self.parse("<div role='button'>Pre-register</div>")
        self.assertTrue(has_google_play_pre_registration_action(soup))

    def test_rejects_installable_released_game(self):
        soup = self.parse(
            "<button aria-label='安裝'>安裝</button>"
            "<p>相關推薦遊戲現在可以 pre-register</p>"
        )
        self.assertFalse(has_google_play_pre_registration_action(soup))

    def test_category_comes_from_category_link(self):
        soup = self.parse(
            "<a href='/store/apps/category/GAME_ROLE_PLAYING'>角色扮演</a>"
            "<section>推薦：策略、冒險</section>"
        )
        self.assertEqual(extract_google_play_category(soup), "角色扮演")

    def test_recommendation_text_is_not_used_as_category(self):
        soup = self.parse("<section>推薦：角色扮演遊戲</section>")
        self.assertEqual(extract_google_play_category(soup), "")


if __name__ == "__main__":
    unittest.main()
