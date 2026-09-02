from .chatgptapi_translator import ChatGPTAPI

XAI_MODEL_LIST = [
    "grok-beta",
]


class XAIClient(ChatGPTAPI):
    # An OpenAI-shaped gateway: everything ChatGPTAPI can do — context,
    # session history, structured output, batching — it can do here, so every
    # argument is forwarded. Only the default address and model differ.
    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(
            key,
            language,
            api_base=str(api_base) if api_base else "https://api.x.ai/v1",
            **kwargs,
        )
        self.model_list = XAI_MODEL_LIST
        self.api_url = self.api_base

    def rotate_model(self):
        self.model = self.model_list[0]
