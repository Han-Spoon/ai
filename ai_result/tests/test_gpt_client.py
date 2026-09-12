import unittest
from unittest.mock import patch

from ai_result.gpt.gpt_client import (
    GPTServiceError,
    _claim_gpt_call,
    _get_client,
    gpt_request_budget,
)


class GptClientConfigTest(unittest.TestCase):
    def tearDown(self):
        _get_client.cache_clear()

    def test_placeholder_api_key_is_rejected_before_network_call(self):
        _get_client.cache_clear()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "PLACEHOLDER"}):
            with self.assertRaises(GPTServiceError):
                _get_client()

    def test_invalid_timeout_is_rejected_before_network_call(self):
        _get_client.cache_clear()
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-key", "OPENAI_TIMEOUT_SECONDS": "invalid"},
        ):
            with self.assertRaises(GPTServiceError):
                _get_client()

    def test_request_budget_limits_sequential_calls(self):
        with gpt_request_budget(total_seconds=5, max_calls=1):
            _claim_gpt_call()
            with self.assertRaises(GPTServiceError):
                _claim_gpt_call()

    def test_request_budget_is_reset_after_context(self):
        with gpt_request_budget(total_seconds=5, max_calls=0):
            with self.assertRaises(GPTServiceError):
                _claim_gpt_call()

        # 컨텍스트 밖의 독립 호출에는 이전 요청의 예산이 누수되지 않는다.
        _claim_gpt_call()


if __name__ == "__main__":
    unittest.main()
