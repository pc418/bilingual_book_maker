from openai import OpenAI
from .chatgptapi_translator import ChatGPTAPI

XAI_MODEL_LIST = [
    "grok-beta",
]


class XAIClient(ChatGPTAPI):
    # This __init__ does not forward the context arguments to ChatGPTAPI's,
    # so a history it never receives cannot be kept.
    SUPPORTS_SESSION_CONTEXT = False
    SUPPORTS_PARALLEL_CONTEXT = False

    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(key, language)
        self.model_list = XAI_MODEL_LIST
        self.api_url = str(api_base) if api_base else "https://api.x.ai/v1"
        self.api_base = self.api_url
        self.openai_client = OpenAI(api_key=key, base_url=self.api_url)

    def rotate_model(self):
        self.model = self.model_list[0]
